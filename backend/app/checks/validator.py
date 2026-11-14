"""Five-layer checks. Errors and unresolved inference cannot reach the solver."""
from __future__ import annotations
import ast, math, os, re
from itertools import combinations
import httpx
from app.spec.models import as_dict, WHITELIST, INFERABLE, load_config
from app.data.grid import island_details, branch_kv
from app.modeling.generator import generate_artifacts, modified_value
from app.modeling.trial import trial_build

def _finite(value):
    return not isinstance(value,bool) and isinstance(value,(float,int)) and math.isfinite(value)

def run_checks(spec,grid,parse_state=None,try_build=False,code=None):
    spec=as_dict(spec);findings=[];config=load_config("app.yaml")
    ops={x["op_id"]:x for x in load_config("operators.yaml")["operators"]}
    periods=int(spec.get("omega",{}).get("periods",16));all_periods=set(range(1,periods+1))
    changes=spec.get("delta_pi",[]);structural=spec.get("structural",[])
    def add(layer,code,severity,message,item_id=None,suggestion=None,**extra):
        findings.append({"layer":layer,"code":code,"severity":severity,"message":message,"item_id":item_id,"suggestion":suggestion,**extra})
    def active(d):return set(d.get("periods") or all_periods)
    if not 1<=periods<=16:add(1,"E105","error","时段数必须在 1–16 范围内。","omega","恢复 16 时段。")
    ranges=config.get("ranges",{});max_scale=ranges.get("scale",{}).get("max",3);voltage=ranges.get("voltage",{"min":0.8,"max":1.2})
    ids=[]
    for d in changes:
        ident=d.get("id");ids.append(ident)
        component,target,param=d.get("component"),d.get("target"),d.get("param")
        if target not in grid.get(component,{}):add(1,"E101","error",f"对象 {target} 不存在；保留原始编号，未替换。",ident,"检查 B/L/G 编号或回答对象追问。")
        allowed=WHITELIST.get(component,{})
        if param not in allowed:add(1,"E102","error",f"{component} 不允许修改参数 {param}。",ident,"选择参数白名单中的项目。")
        operation,value=d.get("operation"),d.get("value")
        if d.get("source")=="explicit" and (operation is None or value is None):add(1,"E103","error","明确项缺少 operation 或 value。",ident,"补齐操作与数值。")
        if d.get("source")=="inferred" and not d.get("direction"):add(1,"E103","error","推断项缺少方向。",ident,"指定 increase/decrease/set_zero。")
        if operation and param in allowed and operation not in allowed[param]:add(1,"E102","error",f"{param} 不允许 {operation} 操作。",ident,"按参数白名单选择操作。")
        if d.get("source")=="inferred" and param not in INFERABLE.get(component,[]):add(1,"E102","error",f"{param} 不能由场景推断。",ident,"改为明确数值与操作。")
        bad=value is not None and not _finite(value)
        if _finite(value):
            if operation=="scale" and not 0<float(value)<=max_scale:bad=True
            if param in ("VMIN","VMAX") and not voltage["min"]<=float(value)<=voltage["max"]:bad=True
            if param in ("GEN_STATUS","BR_STATUS") and value not in (0,1):bad=True
            if param=="RATE_A" and operation=="set" and value<0:bad=True
        if bad:add(1,"E104","error",f"{target}.{param} 数值越界或不是有限数。",ident,"核对比例、状态或电压允许范围。")
        if any(isinstance(t,bool) or not isinstance(t,int) or t<1 or t>16 for t in d.get("periods",[])):add(1,"E105","error","时段超出 1–16 或类型不正确。",ident,"重新选择时段。")
        if d.get("source")=="inferred" and (not d.get("confirmed") or value is None or operation is None):
            add(1,"W109","warning",f"{target}.{param} 推断幅度未确认，禁止生成或求解。",ident,"回答幅度追问并确认。")
        if d.get("source")=="inferred" and d.get("confirmed") and operation=="scale" and _finite(value):
            if (d.get("direction")=="decrease" and value>=1) or (d.get("direction")=="increase" and value<=1):
                add(1,"E104","error","确认数值与推断方向相反。",ident,"核对上调/下调比例。")
    if len(ids)!=len(set(ids)):add(1,"E103","error","参数条目 id 重复，无法唯一定位。",None,"为每条修改设置唯一 id。")
    for op in structural:
        ident,name=op.get("id"),op.get("op_id");params=op.get("params",{})
        if name not in ops and not op.get("custom_code"):add(1,"E106","error",f"未知结构算子 {name} 且无自定义代码。",ident,"选择已登记算子。")
        if op.get("custom_code"):add(1,"W107","warning","含 custom_code，待人工审核；不会自动执行该代码。",ident,"独立审核自定义代码与物理含义。")
        if name in ops and op.get("kind")!=ops[name]["kind"]:add(1,"E108","error","结构算子 kind 与登记类型不一致。",ident,"使用算子库定义的类型。")
        if name in ("switching_limit_per_period","switching_limit_total"):
            k=params.get("k",params.get("K"))
            if isinstance(k,bool) or not isinstance(k,int) or k<=0:add(1,"E108","error","开关次数上限必须为正整数。",ident,"填写正整数 k。")
        if name=="section_limit":
            branches=params.get("branches")
            bad=not isinstance(branches,list) or not branches or any(x not in grid["branch"] for x in branches)
            if not _finite(params.get("limit_mw")) or params.get("limit_mw",0)<0:bad=True
            if params.get("direction","forward") not in ("forward","reverse","both"):bad=True
            if bad:add(1,"E108","error","断面支路、限额或方向缺失/非法。",ident,"指定存在的 L 编号、非负 MW 限额和方向。")
        if name in ("switching_penalty","loss_min","voltage_deviation"):
            beta=params.get("beta")
            if beta is None:add(1,"W110","warning",f"目标系数 beta 为空，模板采用配置默认值 {config.get('default_beta',1.0)}。",ident,"确认默认目标权重或填写 beta。")
            elif not _finite(beta) or beta<0:add(1,"E108","error","目标系数 beta 必须为非负有限数。",ident,"填写非负 beta。")
    # Overlapping changes are rejected when their isolated effects conflict or compound twice.
    for a,b in combinations(changes,2):
        if (a.get("component"),a.get("target"),a.get("param"))!=(b.get("component"),b.get("target"),b.get("param")):continue
        overlap=active(a)&active(b)
        if not overlap:continue
        if a.get("value") is None or b.get("value") is None:continue
        same_set=a.get("operation")==b.get("operation")=="set" and a["value"]==b["value"]
        if not same_set:add(2,"E201","error",f"{a['target']}.{a['param']} 在时段 {sorted(overlap)} 有重复或冲突修改（{a['id']} / {b['id']}）。",b["id"],"合并为唯一修改，明确最终参数值。")
    out=[d for d in changes if d.get("component")=="branch" and d.get("param")=="BR_STATUS" and d.get("value")==0]
    for d in out:
        if d["target"] in grid["branch"]:add(2,"I203","info",f"{d['target']} 停运时段固定为 0，并从可开断决策集合移出。",d["id"])
        if any(x.get("target")==d["target"] and x.get("param")=="RATE_A" and active(x)&active(d) for x in changes) or any(d["target"] in x.get("params",{}).get("branches",[]) for x in structural):
            add(2,"W202","warning",f"停运支路 {d['target']} 又被修改容量或纳入断面。",d["id"],"核对是否仍需要该容量/断面项。")
    checked=set()
    for t in range(1,periods+1):
        opened=tuple(sorted(d["target"] for d in out if d["target"] in grid["branch"] and t in active(d)))
        if not opened or opened in checked:continue
        checked.add(opened)
        islands=island_details(grid,list(opened))
        if islands:
            bus_count=sum(x["bus_count"] for x in islands);net=sum(x["net_injection_mw"] for x in islands);buses=[b for island in islands for b in island["buses"]]
            add(2,"W206","warning",f"停运 {', '.join(opened)} 形成孤岛：孤立 {bus_count} 个母线（{', '.join(buses[:8])}），净注入约 {net:.3f} MW。",next(d["id"] for d in out if d["target"] in opened),"核对孤岛供电与事故处置；不得忽略孤岛。",islands=islands,isolated_buses=buses,net_injection_mw=net)
    for component,lower,upper in [("bus","VMIN","VMAX"),("gen","PMIN","PMAX")]:
        targets={d["target"] for d in changes if d.get("component")==component and d.get("param") in (lower,upper) and d["target"] in grid[component]}
        for target in targets:
            for t in range(1,periods+1):
                if modified_value(grid,spec,component,target,lower,t)>modified_value(grid,spec,component,target,upper,t):
                    add(2,"E204","error",f"{target} 第 {t} 时段修改后 {lower} > {upper}。",next(d["id"] for d in changes if d["target"]==target),"核对上下限，保留原始数据。");break
    if spec.get("omega",{}).get("problem")=="multi_period_acopf" and any(x["op_id"].startswith("switching_") for x in structural):
        add(2,"W205","warning","固定拓扑问题仍含开关算子。","omega","确认开关约束/惩罚是否需要保留。")
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*kV\s*(?:线路|支路)?\s*(L\d+)",spec.get("instruction",""),re.I):
        lid=m[2].upper()
        if lid in grid["branch"] and not math.isclose(float(m[1]),branch_kv(grid,grid["branch"][lid])):
            add(2,"W207","warning",f"原文 {lid} 电压等级 {m[1]} kV 与数据 {branch_kv(grid,grid['branch'][lid]):g} kV 不一致。",next((d["id"] for d in changes if d["target"]==lid),None),"核对线路编号和电压等级。")
    required={"bus":["BUS_I","BUS_TYPE","PD","QD","GS","BS","VM","VA","BASE_KV","VMAX","VMIN"],"gen":["GEN_BUS","PG","QG","QMAX","QMIN","GEN_STATUS","PMAX","PMIN"],"branch":["F_BUS","T_BUS","BR_R","BR_X","BR_B","RATE_A","TAP","SHIFT","BR_STATUS","PF","QF","PT","QT"]}
    bad_rows=[]
    for component,columns in required.items():
        for ident,row in grid[component].items():
            if any(k not in row or not _finite(row[k]) for k in columns):bad_rows.append(ident)
    if bad_rows:add(3,"E301","error",f"断面存在缺列、NaN或缺失数值：{', '.join(bad_rows[:10])}。",None,"补充有效断面，不能静默修正。")
    unlimited=[l for l,row in grid["branch"].items() if float(row["RATE_A"])==0]
    if unlimited:add(3,"W302","warning",f"{len(unlimited)} 条支路 RATE_A=0，按不限容量处理。",None,"核实这些支路限额是否确实缺省。")
    high=sum(float(b["VM"])>float(b["VMAX"]) for b in grid["bus"].values())
    low=sum(float(b["VM"])<float(b["VMIN"]) for b in grid["bus"].values())
    genbad=sum(not float(g["PMIN"])<=float(g["PG"])<=float(g["PMAX"]) for g in grid["gen"].values())
    add(3,"I303","info",f"基态已有 {high} 个母线电压高于上限、{low} 个低于下限，{genbad} 台机组出力超界；未修改原始数据。")
    if spec.get("snapshot",{}).get("single_snapshot",True):add(3,"W304","warning","单断面复制为 16 时段：演示模式，不是实际多时段预测。",None,"接入连续断面后再评估时序策略。")
    for q in grid.get("summary",{}).get("quality",[]):
        if "time" in str(q.get("code","")).lower() or "时刻" in q.get("message",""):
            add(3,"W305","warning",q["message"])
    build={"status":"not_run","reason":"未请求试构建；已检查生成代码语法。"}
    artifacts=None
    try:
        artifacts=generate_artifacts(spec,grid)
        actual_code=code if code is not None else artifacts["code"]
        ast.parse(actual_code)
        add(4,"I403","info",f"模板规模：{artifacts['size']['binary']} 个0-1变量，{artifacts['size']['continuous']} 个连续变量，{artifacts['size']['constraints']} 条正常方式约束；不含 B9。",None,size=artifacts["size"])
        if try_build and not any(x["severity"]=="error" for x in findings) and not any(x["code"]=="W109" for x in findings):
            build=trial_build(actual_code,spec,grid,int(config.get("trial_build",{}).get("timeout_s",60)))
            if build["status"]=="error":add(4,"E402","error","试构建失败："+build["reason"],None,"检查生成代码、参数和网络索引。")
            elif build["status"]=="skipped":add(4,"W404","warning",build["reason"],None,"完成依赖安装/人工审核后再试构建。")
            else:add(4,"I403","info",f"真实试构建 T=1：{build['variables']} 个变量，{build['binary']} 个0-1变量，{build['constraints']} 条约束。未求解。",None,build=build)
        elif try_build:build={"status":"blocked","reason":"规格错误或推断幅度未确认，未执行代码。"}
    except SyntaxError as exc:add(4,"E401","error",f"生成代码语法错误，第 {exc.lineno} 行：{exc.msg}。",None,"定位代码对应规格或算子。")
    except (ValueError,TypeError,KeyError) as exc:add(4,"E402","error",f"代码生成/计数失败：{exc}。",None,"修正规格后重试。")
    original=spec.get("instruction","")
    back=[]
    for item in changes+structural:
        evidence=item.get("evidence","")
        if not evidence or evidence not in original:add(5,"W501","warning",f"{item['id']} 的原文依据不存在于调度指令中，疑似幻觉。",item["id"],"引用真实原文字句，或由用户补充指令。")
        if "target" in item:
            action=f"{item.get('operation') or item.get('direction')} {item.get('value') if item.get('value') is not None else '幅度待确认'}"
            text=f"{item['target']} 的 {item['param']} 执行 {action}，时段 {item.get('periods') or '全部'}。"
        else:text=f"增加 {item['op_id']}，参数 {item.get('params',{})}。"
        back.append({"id":item["id"],"evidence":evidence,"text":text})
    from app.modeling.engine import DemoRuleEngine
    expected_state=DemoRuleEngine().parse(original,{"grid":grid,"task_id":spec.get("task_id","check"),"snapshot":spec.get("snapshot")})
    expected=expected_state["spec"]
    keys={(x["target"],x["param"]) for x in changes}
    expected_keys={(x["target"],x["param"]) for x in expected["delta_pi"]}
    for q in expected_state["questions"]:
        for item in expected["delta_pi"]:
            if item["target"]==q.get("target_id"):
                expected_keys.update((target,item["param"]) for target in q.get("candidates",[]))
    for item in changes:
        if (item["target"],item["param"]) not in expected_keys:
            add(5,"W501","warning",f"{item['id']} 的对象/参数修改未被原文规则支持，疑似多出的修改。",item["id"],"核对对象、参数与原文依据；必要时补充指令。")
    for item in expected["delta_pi"]:
        candidate_keys={(item["target"],item["param"])}
        for q in expected_state["questions"]:
            if q.get("target_id")==item["target"]:candidate_keys.update((target,item["param"]) for target in q.get("candidates",[]))
        if not candidate_keys.intersection(keys):
            add(5,"W502","warning",f"原文要求 {item['target']}.{item['param']} 未进入规格，疑似遗漏。",None,"补齐该项或修改调度指令。")
        elif item.get("source")=="explicit":
            actual=[x for x in changes if (x["target"],x["param"]) in candidate_keys]
            if not any(x.get("operation")==item.get("operation") and x.get("value")==item.get("value") and x.get("periods",[])==item.get("periods",[]) for x in actual):
                add(5,"W502","warning",f"{item['target']}.{item['param']} 的操作、数值或时段与原文明确要求不一致。",actual[0]["id"],"核对编辑值，或补充修改后的调度指令。")
    current_ops={x["op_id"] for x in structural}
    expected_ops={x["op_id"] for x in expected["structural"]}
    for item in structural:
        if item["op_id"] not in expected_ops:
            add(5,"W501","warning",f"结构算子 {item['op_id']} 未被原文规则支持，需人工核对。",item["id"],"补充该结构要求的原文依据。")
    for item in expected["structural"]:
        if item["op_id"] not in current_ops:add(5,"W502","warning",f"原文要求 {item['op_id']} 未进入规格。",None,"补齐该结构算子或修改原指令。")
    add(5,"I503","info","已逐项回译规格，与原文依据对照。",None,back_translation=back)
    endpoint=os.environ.get("METHOD_ENDPOINT","").rstrip("/")
    if endpoint:
        try:
            with httpx.Client(timeout=8,trust_env=False) as client:
                response=client.post(endpoint+"/check",json={"instruction":original,"spec":spec});response.raise_for_status()
            ext=response.json();ext=ext.get("findings",[]) if isinstance(ext,dict) else ext
            for x in ext:
                severity=x.get("severity","warning")
                add(5,str(x.get("code","W504")),severity if severity in ("error","warning","info") else "warning",
                    str(x.get("message","外部校验意见")),x.get("item_id"),x.get("suggestion"),source="external_method")
        except (httpx.HTTPError,ValueError,TypeError):
            add(5,"W504","warning","外部去幻觉方法不可用，保留本地校验结果。",None,"检查 METHOD_ENDPOINT。")
    else:add(5,"I504","info","外部方法：未接入。当前为本地规则覆盖与回译检查。")
    layers=[]
    for index,name in enumerate(["规格合法","结构一致","数据检查","可执行","语义回译"],1):
        rows=[x for x in findings if x["layer"]==index]
        layers.append({"layer":index,"name":name,"status":"error" if any(x["severity"]=="error" for x in rows) else "warning" if any(x["severity"]=="warning" for x in rows) else "pass","count":len(rows)})
    errors=sum(x["severity"]=="error" for x in findings);warnings=sum(x["severity"]=="warning" for x in findings)
    unresolved=any(not q.get("answered") for q in (parse_state or {}).get("questions",[]))
    can_solve=not errors and not unresolved and not any(x["code"]=="W109" for x in findings) and bool(changes or structural)
    return {"findings":findings,"layers":layers,"errors":errors,"warnings":warnings,"can_solve":can_solve,"back_translation":back,"build":build,"external_method":"configured" if endpoint else "未接入","requires_warning_ack":warnings>0}
