"""Offline rules and schema-checked OpenAI-compatible specification parsers."""
from __future__ import annotations
import copy, json, os, re, time
from datetime import datetime, timedelta
from typing import Any, Protocol
import httpx
from pydantic import BaseModel, Field, ValidationError, ConfigDict
from app.spec.models import ModelSpec, DeltaPi, StructuralOp, BaseModelDef, WHITELIST, load_config, derived_level
from app.modeling.problem import detect_problem, apply_problem_choice

class ModelingEngine(Protocol):
    def parse(self, instruction: str, ctx: dict) -> dict: ...
    def answer(self, state: dict, question_id: str, answer: Any) -> dict: ...

def _clause(text, start, end):
    lo = max(text.rfind(sep, 0, start) for sep in "，。；;") + 1
    stops = [x for sep in "，。；;" if (x := text.find(sep, end)) >= 0]
    return text[lo:min(stops) if stops else len(text)].strip()

def period_options(snapshot):
    try: start = datetime.fromisoformat(str(snapshot["start"]).replace("Z", "+00:00"))
    except (ValueError, TypeError, KeyError): start = datetime(2026,10,4,15,35)
    return [{"label": f"第 {i} 时段 · {(start + timedelta(minutes=15*(i-1))):%H:%M}–{(start + timedelta(minutes=15*i)):%H:%M}", "value": i} for i in range(1,17)]

def _periods(text, snapshot):
    match = re.search(r"(?:第\s*)?(\d+)\s*[-–—到至]\s*(\d+)\s*(?:个\s*)?时段", text)
    if match:
        if int(match[2]) < int(match[1]): return [], True
        return list(range(int(match[1]), int(match[2])+1)), False
    one = re.search(r"第\s*(\d+)\s*时段", text)
    if one: return [int(one[1])], False
    clock = re.search(r"(\d{1,2}):(\d{2})\s*[-–—至到]\s*(\d{1,2}):(\d{2})", text)
    if clock:
        try:
            start=datetime.fromisoformat(str(snapshot["start"]).replace("Z","+00:00"))
            begin=start.replace(hour=int(clock[1]),minute=int(clock[2]),second=0,microsecond=0)
            end=start.replace(hour=int(clock[3]),minute=int(clock[4]),second=0,microsecond=0)
            if end<=begin: end+=timedelta(days=1)
            if begin<start or end>start+timedelta(minutes=240): return [],True
            a=(begin-start).total_seconds()/900; b=(end-start).total_seconds()/900
            if a!=int(a) or b!=int(b): return [],True
            return list(range(int(a)+1,int(b)+1)),False
        except (ValueError,KeyError): return [],True
    if re.search(r"全天|所有时段|全部时段|每个时段|每时段",text): return [],False
    return [],bool(re.search(r"午高峰|午后|晚上|近期|明天|早高峰|晚高峰|傍晚",text))

def _number(text):
    m=re.search(r"(提高|提升|增加|上调|增长|降低|减少|下降|下调)\s*(\d+(?:\.\d+)?)\s*[%％]",text)
    if m: return "scale",1+(-1 if m[1] in ("降低","减少","下降","下调") else 1)*float(m[2])/100
    m=re.search(r"(增加|减少)\s*(-?\d+(?:\.\d+)?)\s*(?:MW|Mvar|兆瓦)",text,re.I)
    if m: return "add",float(m[2])*(-1 if m[1]=="减少" else 1)
    m=re.search(r"(?:设为|设置为|调整为|限定为|限额(?:为)?|上限(?:为)?|下限(?:为)?)\s*(-?\d+(?:\.\d+)?)",text)
    if m: return "set",float(m[1])
    return None,None

def _question(items,kind,text,options=None,target_id=None,**extra):
    q={"id":f"q-{len(items)+1}","kind":kind,"text":text,"target_id":target_id,"options":options or [],"answered":False,**extra}
    items.append(q);return q["id"]

def _default_snapshot(grid):
    meta=grid.get("meta",{})
    return {"id":meta.get("id",meta.get("filename","snapshot")),"files":[meta.get("filename","")],"start":meta.get("time","2026-10-04T15:35:00"),"periods":16,"step_min":15,"single_snapshot":True}

class DemoRuleEngine:
    def parse(self,instruction,ctx):
        tic=time.perf_counter(); grid=ctx["grid"]
        snapshot=ctx.get("snapshot") or _default_snapshot(grid)
        spec=ModelSpec(task_id=ctx["task_id"],instruction=instruction,snapshot=snapshot).model_dump()
        entities=[];questions=[];warnings=[];targets=[];excluded=[]
        periods,ambiguous_time=_periods(instruction,snapshot)
        for m in re.finditer(r"母线\s*(\d+)\s*(?:上|处)?\s*的?机组",instruction):
            bid=int(m[1]); candidates=[gid for gid,g in grid["gen"].items() if int(g["GEN_BUS"])==bid]
            excluded.append(m.span())
            entity={"id":f"B{bid}","type":"bus","text":m[0],"start":m.start(),"end":m.end(),"evidence":m[0]}
            entities.append(entity)
            if len(candidates)==1: target=candidates[0];entity["resolved_id"]=target
            else:
                target=m[0]
                _question(questions,"object",f"{m[0]}对应{len(candidates)}台机组，请明确机组编号。",[{"label":x,"value":x} for x in candidates],target,candidates=candidates,component="gen")
            targets.append(("gen",target,m.start(),m.end()))
        for component,pattern in [("branch",r"(?<![A-Za-z0-9])L\s*(\d+)"),("gen",r"(?<![A-Za-z0-9])G\s*(\d+)"),("bus",r"母线\s*(\d+)|(?<![A-Za-z0-9])B\s*(\d+)")]:
            for m in re.finditer(pattern,instruction,re.I):
                if component=="bus" and any(a<=m.start()<b for a,b in excluded): continue
                num=next(x for x in m.groups() if x is not None)
                ident={"branch":"L","gen":"G","bus":"B"}[component]+str(int(num))
                entities.append({"id":ident,"type":component,"text":m[0],"start":m.start(),"end":m.end(),"evidence":m[0]})
                targets.append((component,ident,m.start(),m.end()))
        if not any(x[0]=="branch" for x in targets):
            for m in re.finditer(r"(?:线路|支路)\s*(\d+)\s*[-–—至]\s*(\d+)",instruction):
                a,b=int(m[1]),int(m[2]); candidates=[lid for lid,l in grid["branch"].items() if {int(l["F_BUS"]),int(l["T_BUS"])}=={a,b}]
                target=candidates[0] if len(candidates)==1 else m[0]
                entities.append({"id":target,"type":"branch","text":m[0],"start":m.start(),"end":m.end(),"evidence":m[0]})
                targets.append(("branch",target,m.start(),m.end()))
                if len(candidates)!=1:
                    options=[{"label":x,"value":x} for x in candidates]
                    if len(candidates)>1: options.append({"label":"全部并联回路","value":candidates})
                    _question(questions,"object",f"{m[0]}匹配{len(candidates)}条支路，请选择具体编号或全部并联回路。",options,target,candidates=candidates,component="branch")
        section=re.search(r"断面[\s\S]*?(?:限额|设为|设置为)[^\d-]*(-?\d+(?:\.\d+)?)\s*MW",instruction,re.I)
        rules=load_config("knowledge.yaml").get("rules",[])
        for component,target,a,b in targets:
            clause=_clause(instruction,a,b)
            if component=="branch" and section and not re.search(r"检修|停运|热稳|提高|降低|下降",clause): continue
            operation,value=_number(clause)
            param={"bus":"PD","gen":"PMAX","branch":"RATE_A"}[component]
            if component=="bus":
                if re.search(r"无功|QD",clause,re.I):param="QD"
                if re.search(r"电压上限|VMAX",clause,re.I):param="VMAX"
                if re.search(r"电压下限|VMIN",clause,re.I):param="VMIN"
            if component=="gen" and re.search(r"出力下限|PMIN",clause,re.I):param="PMIN"
            if component in ("branch","gen") and re.search(r"检修|停运|退出运行",clause):
                param="BR_STATUS" if component=="branch" else "GEN_STATUS";operation,value="set",0
            source="explicit";direction=None;chain=None
            if operation is None:
                rule=next((r for r in rules if r["component"]==component and any(w in instruction for w in r["keywords"])),None)
                if rule is None and component=="branch" and re.search(r"热稳.*下降|限额下降|能力下降",clause):rule=next(r for r in rules if r["id"]=="heat")
                if rule is None:continue
                param,direction,chain=rule["param"],rule["direction"],rule["chain"];source="inferred"
            dp={"id":f"dp-{len(spec['delta_pi'])+1}","component":component,"target":target,"param":param,"operation":operation,"direction":direction,"value":value,"periods":periods.copy(),"source":source,"evidence":clause or instruction,"inference_chain":chain,"confirmed":source=="explicit"}
            spec["delta_pi"].append(dp)
            if source=="inferred":
                default=rule.get("default_value")
                _question(questions,"amplitude",f"{target}.{param} 调整幅度是多少？请手动输入比例或设定值。",[{"label":"手动输入","value":"manual"},{"label":"按规程默认值","value":"default","disabled":default is None,"description":"规程默认值未配置" if default is None else "采用已登记默认值"}],dp["id"],direction=direction,default_value=default)
        def operator(op_id,kind,params,evidence):
            spec["structural"].append({"id":f"s-{len(spec['structural'])+1}","kind":kind,"op_id":op_id,"params":params,"source":"explicit","evidence":evidence,"custom_code":None})
        switch=re.search(r"(?:开关操作|开关动作|开关次数)[^，。；;]*?不超过\s*(\d+)\s*次",instruction)
        if switch:operator("switching_limit_total" if re.search(r"全天|总共|总计|累计",instruction) else "switching_limit_per_period","constraint",{"k":int(switch[1])},switch[0])
        if section:
            branches=[x[1] for x in targets if x[0]=="branch"]
            operator("section_limit","constraint",{"branches":branches,"limit_mw":float(section[1]),"direction":"forward"},instruction.strip("。"))
            if not branches:_question(questions,"object","断面由哪些支路组成？请输入 L 编号。")
        if re.search(r"少动开关|减少开关操作",instruction):
            evidence=re.search(r"(?:尽量)?(?:少动开关|减少开关操作)",instruction)[0]
            operator("switching_penalty","objective",{"beta":None},evidence);warnings.append("开关惩罚系数未指定，将采用配置默认 beta；确认规格时请核对。")
        for token,op in [("网损","loss_min"),("电压偏差","voltage_deviation"),("电压质量","voltage_deviation")]:
            if token in instruction and not any(x["op_id"]==op for x in spec["structural"]):operator(op,"objective",{"beta":None},token)
        model_choice=detect_problem(instruction)
        if model_choice:
            spec["omega"]["problem"]=model_choice["problem"]
            if model_choice["problem"]=="multi_period_acopf":
                operator("multi_period_acopf","problem",{},model_choice["evidence"])
        if re.search(r"N\s*[-−–—]\s*1",instruction,re.I):spec["omega"]["n_minus_1"]=True
        if ambiguous_time or (any(x["source"]=="inferred" for x in spec["delta_pi"]) and not periods and not re.search(r"全部时段|全天|所有时段",instruction)):
            _question(questions,"periods","请明确适用时段；当前断面窗口内可多选 1–16 时段。",period_options(snapshot),"all",multiple=True)
        if not spec["delta_pi"] and not spec["structural"]:_question(questions,"object","指令尚未给出可定位的调整对象，请指定母线 B、支路 L 或机组 G 编号。")
        entities.sort(key=lambda x:x["start"])
        return {"spec":ModelSpec.model_validate(spec).model_dump(),"questions":questions,"entities":entities,"level":derived_level(spec),"engine":"demo","elapsed_ms":round((time.perf_counter()-tic)*1000,3),"warnings":warnings}

    def answer(self,state,question_id,answer):
        out=copy.deepcopy(state);q=next((q for q in out.get("questions",[]) if q["id"]==question_id),None)
        if q is None:raise ValueError("追问不存在")
        if q.get("answered"):raise ValueError("该追问已回答，请编辑规格或重新解析")
        spec=out["spec"]
        if q["kind"]=="periods":
            if isinstance(answer,dict):answer=answer.get("periods",answer.get("value"))
            if isinstance(answer,str):
                if answer.strip() in ("全部","全天","所有时段"):answer=list(range(1,17))
                else:
                    m=re.match(r"^\s*(?:第)?\s*(\d+)\s*[-–—到至]\s*(\d+)",answer)
                    answer=list(range(int(m[1]),int(m[2])+1)) if m else [int(x) for x in re.findall(r"\d+",answer)]
            if not isinstance(answer,list) or not answer or any(isinstance(x,bool) or not isinstance(x,int) or x<1 or x>16 for x in answer):raise ValueError("请选择 1–16 内至少一个时段")
            for item in spec["delta_pi"]:item["periods"]=sorted(set(answer))
        elif q["kind"]=="amplitude":
            item=next((x for x in spec["delta_pi"] if x["id"]==q.get("target_id")),None)
            if item is None:raise ValueError("追问对应的规格条目不存在")
            if answer == "default":
                if q.get("default_value") is None: raise ValueError("规程默认值未配置")
                answer=q["default_value"]
            if isinstance(answer,str):
                operation,value=_number(answer)
                if operation is None:
                    try:operation,value="scale",float(answer)
                    except ValueError:raise ValueError("请输入比例，如0.8，或“降低20%”")
            elif isinstance(answer,dict):operation,value=answer.get("operation","scale"),answer.get("value")
            elif isinstance(answer,(int,float)) and not isinstance(answer,bool):operation,value="scale",answer
            else:raise ValueError("幅度答案格式不正确")
            if value is None or isinstance(value,bool):raise ValueError("调整数值不能为空")
            value=float(value)
            if operation not in WHITELIST[item["component"]][item["param"]]:raise ValueError("该参数不允许此操作")
            if operation=="scale" and not 0<value<=3:raise ValueError("比例必须大于0且不超过3")
            if operation=="scale" and item.get("direction")=="decrease" and value>=1:raise ValueError("下调比例必须小于1")
            if operation=="scale" and item.get("direction")=="increase" and value<=1:raise ValueError("上调比例必须大于1")
            item.update(operation=operation,value=value,confirmed=True)
        elif q["kind"]=="object":
            choices=answer if isinstance(answer,list) else [answer];choices=list(dict.fromkeys(str(x).strip().upper() for x in choices))
            if not choices or any(not x for x in choices):raise ValueError("请选择至少一个对象")
            if not q.get("target_id"):out["_reparse_instruction"]=spec["instruction"]+"；对象补充："+ "、".join(choices)
            else:
                allowed=q.get("candidates",[])
                if allowed and any(x not in allowed for x in choices):raise ValueError("请选择列出的对象编号")
                originals=[x for x in spec["delta_pi"] if x["target"]==q["target_id"]]
                next_id=max([int(x["id"].split("-")[-1]) for x in spec["delta_pi"] if re.fullmatch(r"dp-\d+",x["id"])] or [0])+1
                for item in originals:
                    spec["delta_pi"].remove(item)
                    for index,target in enumerate(choices):
                        clone=copy.deepcopy(item);clone["target"]=target
                        if index:
                            clone["id"]=f"dp-{next_id}";next_id+=1
                            if clone.get("source")=="inferred" and not clone.get("confirmed"):
                                original_q=next((x for x in out["questions"] if x["kind"]=="amplitude" and x.get("target_id")==item["id"]),None)
                                if original_q:
                                    new_q=copy.deepcopy(original_q);new_q.update(id=f"q-{len(out['questions'])+1}",target_id=clone["id"],text=f"{target}.{clone['param']} 推断幅度待确认。")
                                    out["questions"].append(new_q)
                        spec["delta_pi"].append(clone)
        else:raise ValueError("此追问需回到规格页手动修改")
        q["answered"]=True;q["answer"]=answer;out["level"]=derived_level(spec);return out

class RemotePayload(BaseModel):
    model_config=ConfigDict(extra="ignore")
    delta_pi:list[DeltaPi]=Field(default_factory=list)
    structural:list[StructuralOp]=Field(default_factory=list)
    questions:list[dict]=Field(default_factory=list)
    findings:list[dict]=Field(default_factory=list)
    omega:BaseModelDef|None=None

SYSTEM_PROMPT="""你是电网运行方式调整建模助手，把指令转成模型规格JSON，不求解、不写求解代码。
只使用提供的白名单、对象与结构算子。母线B{BUS_I}，支路L{行号}，机组G{行号}。
每项evidence必须从原文逐字摘录。明确数值填operation/value和explicit；场景推断用inferred、direction、inference_chain，value=null、confirmed=false。
不得编造编号、数值或时段，不明确就放questions。N−1仅确认omega.n_minus_1，不生成结构算子。
只输出符合schema的JSON。人工确认前推断幅度不得猜测。"""

def _remote_result(payload,baseline,engine):
    out=copy.deepcopy(baseline);out["spec"]["delta_pi"]=[x.model_dump() for x in payload.delta_pi];out["spec"]["structural"]=[x.model_dump() for x in payload.structural]
    if payload.omega:out["spec"]["omega"]=payload.omega.model_dump()
    out["questions"]=[]
    for item in out["spec"]["delta_pi"]:
        if item["source"]=="inferred":
            item.update(value=None,operation=None,confirmed=False)
            _question(out["questions"],"amplitude",f"{item['target']}.{item['param']} 推断幅度待人工确认。",[{"label":"手动输入","value":"manual"},{"label":"按规程默认值","value":"default","disabled":True}],item["id"])
    for q in payload.questions:_question(out["questions"],q.get("kind","object"),q.get("text","请补充建模条件"),q.get("options",[]),q.get("target_id"))
    if any(q["kind"]=="periods" for q in baseline["questions"]) and not any(q["kind"]=="periods" for q in out["questions"]):
        _question(out["questions"],"periods","请确认模糊时间对应的时段。",period_options(out["spec"]["snapshot"]),"all",multiple=True)
    out["level"]=derived_level(out["spec"]);out["engine"]=engine;out["external_findings"]=payload.findings;return out

class OpenAICompatEngine:
    def parse(self,instruction,ctx):
        tic=time.perf_counter();baseline=DemoRuleEngine().parse(instruction,ctx)
        base=os.environ.get("LLM_BASE_URL","http://127.0.0.1:8001/v1").rstrip("/")
        headers={"Content-Type":"application/json"}
        if os.environ.get("LLM_API_KEY"):headers["Authorization"]="Bearer "+os.environ["LLM_API_KEY"]
        examples=[]
        for text in ("第1–8时段，母线43有功负荷提高20%。","午高峰高温，220kV线路L155热稳限额下降，要求满足N−1，尽量少动开关。"):
            sample=DemoRuleEngine().parse(text,ctx)
            examples.append({"instruction":text,"delta_pi":sample["spec"]["delta_pi"],"structural":sample["spec"]["structural"],"questions":sample["questions"]})
        context={"whitelist":WHITELIST,"operators":load_config("operators.yaml")["operators"],"known_ids":{k:list(ctx["grid"][k]) for k in ("bus","gen","branch")},"schema":RemotePayload.model_json_schema(),"snapshot":ctx.get("snapshot") or _default_snapshot(ctx["grid"]),"examples":examples}
        messages=[{"role":"system","content":SYSTEM_PROMPT+"\n"+json.dumps(context,ensure_ascii=False)},{"role":"user","content":instruction}]
        for attempt in range(2):
            try:
                with httpx.Client(timeout=20,trust_env=False) as client:
                    r=client.post(base+"/chat/completions",headers=headers,json={"model":os.environ.get("LLM_MODEL","Qwen"),"messages":messages,"temperature":0,"response_format":{"type":"json_object"}})
                    r.raise_for_status();raw=r.json()["choices"][0]["message"]["content"]
                fence=chr(96)*3
                cleaned=re.sub(r"^\s*"+fence+r"(?:json)?\s*|\s*"+fence+r"\s*$","",raw)
                result=_remote_result(RemotePayload.model_validate_json(cleaned),baseline,"openai")
                result["elapsed_ms"]=round((time.perf_counter()-tic)*1000,3);return result
            except (httpx.HTTPError,KeyError,IndexError) as exc:
                baseline["warnings"].append(f"本地大模型服务不可用（{type(exc).__name__}），已回落离线演示规则。")
                baseline["engine"]="demo_fallback";baseline["elapsed_ms"]=round((time.perf_counter()-tic)*1000,3);return baseline
            except (ValidationError,ValueError,TypeError) as exc:
                if attempt==0:
                    messages.append({"role":"assistant","content":raw if "raw" in locals() else ""})
                    messages.append({"role":"user","content":"输出未通过schema，请修正JSON重试："+str(exc)[:1200]})
                else:
                    baseline["spec"]["delta_pi"]=[];baseline["spec"]["structural"]=[];baseline["questions"]=[]
                    _question(baseline["questions"],"object","模型两次输出均未通过schema，请补充明确对象、参数与幅度后重试。")
                    baseline["level"]="待追问";baseline["engine"]="openai_invalid";baseline["warnings"].append("大模型输出格式校验失败，未采纳任何修改。");return baseline

class ExternalMethodEngine:
    def parse(self,instruction,ctx):
        endpoint=os.environ.get("METHOD_ENDPOINT","").rstrip("/");baseline=DemoRuleEngine().parse(instruction,ctx)
        if not endpoint:
            baseline["engine"]="demo_fallback";baseline["warnings"].append("铮昊方法接口未配置，已使用离线演示规则。");return baseline
        try:
            with httpx.Client(timeout=20,trust_env=False) as client:
                r=client.post(endpoint+"/parse",json={"instruction":instruction,"snapshot_meta":ctx["grid"].get("meta",{}),"whitelist":WHITELIST,"operators":load_config("operators.yaml")["operators"]});r.raise_for_status()
            return _remote_result(RemotePayload.model_validate(r.json()),baseline,"external")
        except (httpx.HTTPError,ValidationError,ValueError):
            baseline["engine"]="demo_fallback";baseline["warnings"].append("外部建模方法不可用，已回落演示规则。");return baseline

def parse_instruction(instruction,grid,task_id,engine="demo",snapshot=None,problem=None):
    if not instruction.strip():raise ValueError("请输入调度指令")
    selected={"demo":DemoRuleEngine,"openai":OpenAICompatEngine,"external":ExternalMethodEngine}.get(engine)
    if selected is None:raise ValueError("未知建模引擎")
    parsed=selected().parse(instruction,{"grid":grid,"task_id":task_id,"snapshot":snapshot})
    return apply_problem_choice(parsed,problem)

def answer_parse(state,question_id,answer,grid):
    out=DemoRuleEngine().answer(state,question_id,answer)
    if "_reparse_instruction" in out:
        return parse_instruction(out.pop("_reparse_instruction"),grid,out["spec"]["task_id"],"demo",out["spec"]["snapshot"],
                                 problem=(state.get("problem_selection") or {}).get("selected"))
    out["spec"]=ModelSpec.model_validate(out["spec"]).model_dump();return out
