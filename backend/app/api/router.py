import asyncio
import csv
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse, FileResponse
from pydantic import BaseModel, Field
from app.config import ROOT, RUNS_DIR, dump_json, read_json, read_yaml
from app.data.grid import load_grid, get_snapshots, snapshot_ref, load_task_grid
from app.storage import store
from app.solver.service import ResultModel, start_job, get_job, import_result, baseline_result
from app.integrations.catalog import features, integrations
from app.modeling.problem import PROBLEM_NAMES, synchronize_problem, apply_problem_choice

router = APIRouter(prefix="/api")

def _task(task_id):
    try:
        return store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

def _grid(task):
    return load_task_grid((task.get("draft") or {}).get("snapshot") or snapshot_ref(task.get("snapshot_id")))

def _schema(payload):
    from app.spec.models import ModelSpec
    try:
        return synchronize_problem(ModelSpec.model_validate(payload).model_dump(mode="json"))
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc

def _digest(spec):
    return hashlib.sha256(json.dumps(spec,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def _draft(task):
    if task.get("draft") is None:
        raise HTTPException(409, "请先解析调度指令")
    return task["draft"]

def _confirmed(task):
    spec = _draft(task)
    version = task.get("confirmed_version")
    if not version:
        raise HTTPException(409, "请先确认规格并生成代码")
    saved = store.get_version(task["task_id"],version)
    if _digest(spec) != _digest(saved["spec"]):
        raise HTTPException(409, "当前规格已修改，请重新确认并校验")
    return saved

def _question_policy(task, state, rounds=None):
    settings=task.get("settings") or {}
    enabled=bool(settings.get("ask_questions",True))
    maximum=int(settings.get("max_rounds",5))
    used=int(rounds if rounds is not None else (state.get("question_policy") or {}).get("rounds",0))
    pending=any(not q.get("answered") for q in state.get("questions",[]))
    manual=pending and (not enabled or used>=maximum)
    reason=("自动追问已关闭，请在规格页手动补全对象、时段和幅度，再保存草稿。"
            if not enabled else f"已达到{maximum}轮追问上限，请在规格页手动补全剩余信息，再保存草稿。") if manual else ""
    state["manual_completion"]=manual
    state["question_policy"]={"enabled":enabled,"max_rounds":maximum,"rounds":used,"reason":reason}
    return state

def _resolve_manual_questions(task, state):
    spec=state["spec"]
    changes=spec.get("delta_pi",[])
    grid=None
    for question in state.get("questions",[]):
        if question.get("answered"):
            continue
        kind=question.get("kind")
        item=next((x for x in changes if x["id"]==question.get("target_id")),None)
        complete=False
        if kind=="amplitude" and item:
            complete=item.get("confirmed") and item.get("value") is not None and item.get("operation") in ("scale","set","add")
        elif kind=="periods" and changes:
            complete=all(x.get("periods") and all(isinstance(t,int) and 1<=t<=spec["omega"]["periods"] for t in x["periods"]) for x in changes)
        elif kind=="object" and changes:
            grid=grid or _grid(task)
            complete=all(x.get("target") in grid.get(x.get("component"),{}) for x in changes)
        if complete:
            question["answered"]=True
            question["answer"]="用户在规格表手动补全并保存"
    return _question_policy(task,state)

def _state_with_spec(task, spec):
    from app.spec.models import derived_level
    state = dict(task.get("parse") or {})
    state.update(spec=spec,level=derived_level(spec))
    return apply_problem_choice(state,spec["omega"]["problem"])

def _note_problem_change(prior, spec):
    old,new=prior["omega"]["problem"],spec["omega"]["problem"]
    if old!=new:
        note=f"模型类型：{PROBLEM_NAMES[old]} → {PROBLEM_NAMES[new]}"
        existing=spec.get("change_reason") or ""
        spec["change_reason"]=(existing+"；" if existing else "")+note
    return spec

def _version_content(task_id, version):
    task = _task(task_id)
    try:
        return store.get_version(task_id,version)
    except KeyError:
        spec = task.get("draft")
        if spec and spec["version"]==version:
            from app.modeling.generator import generate_artifacts
            artifacts=generate_artifacts(spec,_grid(task))
            artifacts["code"]=""
            return {"spec":spec,"artifacts":artifacts}
        raise HTTPException(404,"版本不存在")

class NewTask(BaseModel):
    instruction: str = Field(min_length=1,max_length=8000)
    engine: str = "demo"
    snapshot_id: str | None = None
    problem: Literal["multi_period_ac_ots", "multi_period_acopf"] | None = None
    ask_questions: bool = True
    max_rounds: int = Field(default=5,ge=1,le=20)

@router.get("/health")
def health():
    return {"status":"ok","app":"keti4-workbench","solver":"blackbox","offline_demo":True}

@router.get("/history")
def historical_calculations():
    from app.history import history_catalog
    try:
        return history_catalog()
    except (ValueError, OSError) as exc:
        raise HTTPException(503, "历史计算资料读取或一致性校验失败") from exc

@router.get("/history/{record_id}/download", include_in_schema=False)
def download_historical_calculation(record_id: str):
    from app.history import load_history
    try:
        record = load_history(record_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(503, "历史计算资料读取或一致性校验失败") from exc
    return Response(json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2),
                    media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="' + record_id + '.json"'})

@router.get("/history/{record_id}")
def historical_calculation(record_id: str):
    from app.history import load_history
    try:
        return load_history(record_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(503, "历史计算资料读取或一致性校验失败") from exc


@router.get("/delivery",include_in_schema=False)
def download_delivery():
    path=ROOT/"deliveries"/"运行方式智能调整平台B-v4.0.zip"
    if not path.exists():
        raise HTTPException(404,"交付包尚未生成")
    return FileResponse(path,media_type="application/zip",filename=path.name)

@router.get("/delivery/manifest",include_in_schema=False)
def delivery_manifest():
    path=ROOT/"docs"/"validation"/"release-manifest.json"
    if not path.exists():
        raise HTTPException(404,"交付清单尚未生成")
    return FileResponse(path,media_type="application/json",filename=path.name)

@router.get("/features")
def get_features():
    return features()

@router.get("/integrations")
def get_integrations():
    return integrations()

@router.get("/snapshots")
def snapshots():
    items=get_snapshots()
    return {"items":items,"default_id":items[0]["id"] if items else None}

@router.get("/snapshots/{snapshot_id}/summary")
def summary(snapshot_id:str):
    if not any(x["id"]==snapshot_id for x in get_snapshots()):
        raise HTTPException(404,"断面不存在")
    return load_grid(snapshot_id)["summary"]

@router.get("/topology")
def topology(snapshot_id:str|None=None):
    return load_grid(snapshot_id)["topology"]

@router.get("/objects")
def objects(q:str="",type:str="",snapshot_id:str|None=None):
    grid=load_grid(snapshot_id)
    items=[]
    for component in (("bus","gen","branch") if not type else (type,)):
        if component not in ("bus","gen","branch"):
            raise HTTPException(422,"对象类型必须为bus/gen/branch")
        for key,row in grid[component].items():
            if component=="branch":
                label=f"{key}（{int(row['F_BUS'])}–{int(row['T_BUS'])}）"
            elif component=="gen":
                label=f"{key}（母线{int(row['GEN_BUS'])}）"
            else:
                label=f"{key}（{row['BASE_KV']:g}kV）"
            if q.lower() in label.lower():
                items.append({"id":key,"label":label,"component":component,"data":row})
    return {"items":items[:300],"total":len(items)}

@router.get("/catalog")
def catalog():
    from app.spec.models import ModelSpec,WHITELIST
    from app.testmode.evaluator import builtins
    op=read_yaml("operators.yaml")
    return {"whitelist":WHITELIST,"operators":op.get("operators",[]),
            "knowledge":read_yaml("knowledge.yaml"),"cases":builtins(),
            "schema":ModelSpec.model_json_schema(),"result_schema":ResultModel.model_json_schema()}

@router.get("/name-registry")
def registry():
    path=ROOT/"data"/"name_registry.csv"
    return {"items":list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig")))) if path.exists() else [],
            "status":"reserved","description":"名称到编号登记表为空，待课题2提供"}

@router.post("/tasks")
def new_task(body:NewTask):
    if body.engine not in ("demo","openai","external","qwen","proopf","topic2"):
        raise HTTPException(422,"请选择已登记的建模引擎")
    endpoint_keys={'qwen':'LLM_BASE_URL','proopf':'PROOPF_BASE_URL','topic2':'TOPIC2_MODEL_ENDPOINT'}
    if body.engine in endpoint_keys and not os.getenv(endpoint_keys[body.engine]):
        raise HTTPException(409,'该建模服务尚未配置，请在数据与接口页查看接入设置')
    if body.engine=="external" and not os.getenv("METHOD_ENDPOINT"):
        raise HTTPException(409,"铮昊方法接口尚未配置")
    if body.snapshot_id and not any(x["id"]==body.snapshot_id for x in get_snapshots()):
        raise HTTPException(404,"断面不存在")
    return store.create_task(body.instruction,body.engine,body.snapshot_id or get_snapshots()[0]["id"],
                             {"ask_questions":body.ask_questions,"max_rounds":body.max_rounds,"problem":body.problem})

@router.get("/tasks/{task_id}")
def task_detail(task_id:str):
    task = _task(task_id)
    return dict(task, current_spec_sha256=_digest(task["draft"]) if task.get("draft") else None)

@router.post("/tasks/{task_id}/parse")
def parse(task_id:str):
    from app.modeling.engine import parse_instruction
    task=_task(task_id)
    parsed=parse_instruction(task["instruction"],_grid(task),task_id,engine=task["engine"],
                             snapshot=snapshot_ref(task["snapshot_id"]),problem=(task.get("settings") or {}).get("problem"))
    spec=_schema(parsed["spec"])
    versions=store.list_versions(task_id)
    spec["version"]=max((x["version"] for x in versions),default=0)+1
    spec["parent_version"]=versions[-1]["version"] if versions else None
    parsed["spec"]=spec
    parsed=_question_policy(task,parsed,rounds=0)
    store.update_task(task_id,draft=spec,parse=parsed,check=None,result=None,metrics=None)
    store.log(task_id,f"解析完成，程序判级 {parsed['level']}，引擎 {parsed['engine']}。")
    return parsed

@router.post("/tasks/{task_id}/answer")
def answer(task_id:str,body:dict=Body(...)):
    from app.modeling.engine import answer_parse
    task=_task(task_id)
    if not task.get("parse"):
        raise HTTPException(409,"请先解析")
    state=_question_policy(task,dict(task["parse"],spec=_draft(task)))
    if state["manual_completion"]:
        raise HTTPException(409,state["question_policy"]["reason"])
    used=(state.get("question_policy") or {}).get("rounds",0)
    try:
        parsed=answer_parse(state,str(body.get("question_id","")),body.get("answer"),_grid(task))
    except (ValueError,KeyError,TypeError) as exc:
        raise HTTPException(422,str(exc)) from exc
    spec=_schema(parsed["spec"])
    versions=store.list_versions(task_id)
    if versions and _digest(spec)!=_digest(store.get_version(task_id,versions[-1]["version"])["spec"]):
        spec["version"]=versions[-1]["version"]+1
        spec["parent_version"]=versions[-1]["version"]
    parsed["spec"]=spec
    parsed=_question_policy(task,parsed,rounds=used+1)
    store.update_task(task_id,draft=spec,parse=parsed,check=None,result=None,metrics=None)
    store.log(task_id,"已保存追问回答："+str(body.get("question_id")))
    return parsed

@router.get("/tasks/{task_id}/spec")
def get_spec(task_id:str):
    return _draft(_task(task_id))

@router.put("/tasks/{task_id}/spec")
def put_spec(task_id:str,body:dict=Body(...)):
    task=_task(task_id)
    spec=_schema(body)
    if spec["task_id"]!=task_id:
        raise HTTPException(422,"规格task_id与任务不一致")
    prior=_draft(task)
    _note_problem_change(prior,spec)
    if spec["snapshot"]!=prior["snapshot"]:
        raise HTTPException(422,"更换断面请新建任务，不能改写已绑定的数据来源")
    if spec["instruction"]!=prior["instruction"]:
        raise HTTPException(422,"请新建任务修改原始指令，避免原文依据失配")
    versions=store.list_versions(task_id)
    if versions and _digest(spec)!=_digest(prior):
        spec["version"]=versions[-1]["version"]+1
        spec["parent_version"]=versions[-1]["version"]
        spec["change_reason"]=spec.get("change_reason") or "用户编辑规格"
    state=_resolve_manual_questions(task,_state_with_spec(task,spec))
    settings=dict(task.get("settings") or {},problem=spec["omega"]["problem"])
    store.update_task(task_id,draft=spec,parse=state,settings=settings,check=None,result=None,metrics=None)
    store.log(task_id,"规格草稿已保存，旧校验与结果不再代表当前草稿。")
    return spec

@router.get("/tasks/{task_id}/preview")
def preview(task_id:str):
    from app.modeling.generator import generate_artifacts
    task=_task(task_id)
    spec=_draft(task)
    artifacts=generate_artifacts(spec,_grid(task))
    try:
        confirmed=_confirmed(task)
        return confirmed["artifacts"]
    except HTTPException:
        artifacts["code"]=""
        return artifacts

@router.post("/tasks/{task_id}/confirm")
def confirm(task_id:str,body:dict=Body(default={})):
    from app.modeling.generator import generate_artifacts
    task=_task(task_id)
    spec=_schema(body.get("spec") or _draft(task))
    if spec["task_id"]!=task_id:
        raise HTTPException(422,"任务与规格不一致")
    draft=_draft(task)
    _note_problem_change(draft,spec)
    if spec["snapshot"]!=draft["snapshot"] or spec["instruction"]!=draft["instruction"]:
        raise HTTPException(422,"确认时不可改写断面或原始指令；请新建任务")
    pending=[q for q in (task.get("parse") or {}).get("questions",[]) if not q.get("answered")]
    if pending:
        raise HTTPException(409,"请先回答全部追问："+ "；".join(q.get("text",q["id"]) for q in pending))
    if not spec["delta_pi"] and not spec["structural"]:
        raise HTTPException(409,"没有可确认的修改，请补充对象或要求")
    for item in spec["delta_pi"]:
        if item["source"]=="inferred" and (not item["confirmed"] or item["value"] is None or not item.get("operation")):
            raise HTTPException(409,f"{item['id']} 推断幅度尚未由用户确认")
        if item["value"] is None:
            raise HTTPException(409,f"{item['id']} 缺少明确数值")
        if not item.get("target") or not item.get("param") or not item.get("operation"):
            raise HTTPException(409,f"{item['id']} 的对象、参数或操作尚未填写")
    for item in spec["structural"]:
        params=item.get("params") or {}
        name=item.get("op_id")
        if name in ("switching_limit_per_period","switching_limit_total") and params.get("k",params.get("K")) is None:
            raise HTTPException(409,f"{item['id']} 的开关次数上限尚未填写")
        if name=="section_limit" and (not params.get("branches") or params.get("limit_mw") is None):
            raise HTTPException(409,f"{item['id']} 的断面支路或限额尚未填写")
    # Generate only after all human confirmations; legality remains visible in M4.
    from app.modeling.dynamic import resource_issues
    missing=resource_issues(spec,_grid(task))
    if missing:raise HTTPException(409,'；'.join(x['message'] for x in missing))
    versions=store.list_versions(task_id)
    spec["version"]=max((v["version"] for v in versions),default=0)+1
    spec["parent_version"]=versions[-1]["version"] if versions else None
    artifacts=generate_artifacts(spec,_grid(task))
    spec=store.save_version(task_id,spec,artifacts)
    store.update_task(task_id,parse=_state_with_spec(task,spec))
    return {"spec":spec,"version":spec["version"],"artifacts":artifacts}

@router.get("/tasks/{task_id}/versions")
def versions(task_id:str):
    task=_task(task_id)
    items=store.list_versions(task_id)
    draft=task.get("draft")
    if draft and not any(x["version"]==draft["version"] for x in items):
        items.append({"version":draft["version"],"parent_version":draft.get("parent_version"),
                      "change_reason":draft.get("change_reason") or "待确认草稿","created_at":task["created_at"],"status":"draft"})
    return {"items":items}

@router.get("/tasks/{task_id}/versions/{version}/latex")
def latex(task_id:str,version:int):
    a=_version_content(task_id,version)["artifacts"]
    return {"blocks":a["latex"],"text":a["latex_text"]}

@router.get("/tasks/{task_id}/versions/{version}/code")
def code(task_id:str,version:int):
    return {"code":_version_content(task_id,version)["artifacts"]["code"]}

@router.get("/tasks/{task_id}/diff")
def diff(task_id:str,a:int,b:int):
    return {"a":a,"b":b,"changes":store.spec_diff(_version_content(task_id,a)["spec"],_version_content(task_id,b)["spec"])}

@router.post("/tasks/{task_id}/check")
def check(task_id:str,body:dict=Body(default={})):
    from app.checks.validator import run_checks
    task=_task(task_id)
    spec=_draft(task)
    code=None
    try:
        code=_confirmed(task)["artifacts"]["code"]
    except HTTPException:
        pass
    report=run_checks(spec,_grid(task),parse_state=task.get("parse"),try_build=bool(body.get("try_build") and code),code=code)
    report["spec_sha256"]=_digest(spec)
    with store.LOCK:
        if _digest(_draft(_task(task_id)))!=_digest(spec):
            raise HTTPException(409,"校验期间规格已变化，请重新校验当前草稿")
        store.update_task(task_id,check=report)
        if code:
            store.save_evidence(task_id,"check",report,spec=spec)
    store.log(task_id,f"五层校验完成：{report.get('errors',0)}项错误，{report.get('warnings',0)}项警告。")
    return report

@router.post("/tasks/{task_id}/solve")
def solve(task_id:str,body:dict=Body(default={})):
    task=_task(task_id)
    saved=_confirmed(task)
    report=task.get("check")
    if not report or report.get("spec_sha256")!=_digest(saved["spec"]):
        raise HTTPException(409,"请先对当前版本执行五层校验")
    if report.get("errors",0) or not report.get('can_solve',False):
        raise HTTPException(409,"当前校验未通过，不能进入求解黑盒")
    if report.get("warnings",0) and not body.get("acknowledge_warnings"):
        raise HTTPException(409,"请阅读并确认校验警告")
    try:
        return start_job(task_id,saved["spec"],_grid(task),body.get("mode","mock"))
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc

@router.get("/jobs/{job_id}")
def job_status(job_id:str):
    try:
        return get_job(job_id)
    except KeyError as exc:
        raise HTTPException(404,str(exc)) from exc

@router.get("/jobs/{job_id}/log")
def job_log(job_id:str):
    job_status(job_id)
    async def events():
        cursor=0
        while True:
            job=get_job(job_id)
            for line in job["logs"][cursor:]:
                yield "data: "+json.dumps({"message":line},ensure_ascii=False)+"\n\n"
            cursor=len(job["logs"])
            if job["status"] in ("completed","error"):
                yield "event: done\ndata: "+json.dumps({"status":job["status"]})+"\n\n"
                break
            await asyncio.sleep(0.3)
    return StreamingResponse(events(),media_type="text/event-stream")

@router.post("/tasks/{task_id}/results/import")
def results_import(task_id:str,body:dict=Body(...)):
    task=_task(task_id)
    saved=_confirmed(task)
    report=task.get("check")
    if not report or report.get("spec_sha256")!=_digest(saved["spec"]):
        raise HTTPException(409,"导入结果前请先对当前规格执行五层校验")
    if report.get("errors",0) or not report.get('can_solve',False):
        raise HTTPException(409,"当前校验未通过，不能导入结果")
    if report.get("warnings",0) and not body.get("acknowledge_warnings"):
        raise HTTPException(409,"导入结果前请阅读并确认校验警告")
    try:
        result=import_result(task_id,saved["spec"],_grid(task),body.get("result",body))
    except (ValueError,TypeError) as exc:
        raise HTTPException(422,str(exc)) from exc
    return {"result":result}

@router.get("/tasks/{task_id}/results")
def results(task_id:str):
    task=_task(task_id)
    periods=_draft(task)["omega"]["periods"]
    return {"result":task.get("result"),"baseline":baseline_result(_grid(task),periods),"periods":periods}

@router.post("/tasks/{task_id}/evaluate")
def evaluate(task_id:str):
    from app.metrics.evaluate import evaluate as compute
    task=_task(task_id)
    spec=_draft(task)
    metrics=compute(spec,task.get("result"),_grid(task))
    metrics["evaluation_stage"]="post_result" if metrics.get("after") is not None else "baseline"
    metrics["spec_sha256"]=_digest(spec)
    metrics["version"]=spec["version"]
    with store.LOCK:
        store.save_evidence(task_id,"metrics",metrics,spec=spec)
        if _digest(_draft(_task(task_id)))!=_digest(spec):
            raise HTTPException(409,"评估期间规格已变化，历史证据已归档，请重新评估当前草稿")
        store.update_task(task_id,metrics=metrics)
    store.log(task_id,"已计算真实断面指标、直流N−1筛查与风险；基态评估不调用求解器，未生成调整后方案。")
    return metrics

@router.post("/tasks/{task_id}/adjust")
def adjust(task_id:str,body:dict=Body(...)):
    from copy import deepcopy
    from app.spec.models import derived_level
    task=_task(task_id)
    saved=_confirmed(task)
    metrics=task.get("metrics")
    if not metrics:
        raise HTTPException(409,"请先计算风险指标")
    if metrics.get('spec_sha256')!=_digest(saved['spec']):
        raise HTTPException(409,'风险评估与当前版本不一致，请重新评估')
    requested=body.get("suggestion_ids",[])
    choices=[s for s in metrics.get("suggestions",[]) if s["id"] in requested]
    if not choices:
        raise HTTPException(422,"请勾选至少一条有效建议")
    spec=deepcopy(saved["spec"])
    spec["parent_version"]=spec["version"]
    spec["version"]+=1
    reasons="；".join(str(s.get("reason") or s.get("title")) for s in choices)
    spec["change_reason"]=body.get("reason") or "来自风险评估的协商建议："+reasons
    evidence="风险协商建议："+reasons
    spec["instruction"] += "\n"+evidence
    questions=[]
    for suggestion in choices:
        delta=suggestion.get("delta") or suggestion.get("delta_pi")
        structural=suggestion.get("structural")
        if delta:
            values=delta if isinstance(delta,list) else [delta]
            for item in values:
                item=deepcopy(item)
                item.update(id=f"dp-{len(spec['delta_pi'])+1}",value=None,confirmed=False,evidence=evidence)
                item.setdefault("source","inferred"); item.setdefault("periods",[])
                item.setdefault("operation",None); item.setdefault("direction","decrease")
                spec["delta_pi"].append(item)
                questions.append({"id":"adjust-"+item["id"],"kind":"value","text":f"请在规格表为 {item['target']}.{item['param']} 填写数值并确认。",
                                  "target_id":item["id"],"options":[],"answered":True})
        if structural:
            values=structural if isinstance(structural,list) else [structural]
            for item in values:
                item=deepcopy(item)
                item.update(id=f"s-{len(spec['structural'])+1}",evidence=evidence)
                item.setdefault("source","inferred"); item.setdefault("kind","constraint")
                spec["structural"].append(item)
    spec=_schema(spec)
    state={"spec":spec,"questions":questions,"entities":[],"level":derived_level(spec),
           "engine":"风险协商 · 待用户填写数值","elapsed_ms":0,"warnings":["建议数值均留空，填写并确认后重新校验。"]}
    store.update_task(task_id,draft=spec,parse=state,check=None,result=None,metrics=None)
    store.log(task_id,f"已生成v{spec['version']}协商草稿；父版本v{spec['parent_version']}。")
    return state

@router.get("/tasks/{task_id}/logs")
def logs(task_id:str):
    return {"items":_task(task_id).get("logs",[])}

@router.post("/testsets")
async def import_testset(request:Request):
    from app.testmode.evaluator import normalize_cases,parse_upload
    content_type=request.headers.get("content-type","")
    try:
        if "multipart/form-data" in content_type:
            form=await request.form()
            file=form.get("file")
            if file is None:
                raise ValueError("请选择JSON或CSV测试集文件")
            raw=await file.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:
                raise ValueError("测试集最大2MiB")
            cases=parse_upload(raw,file.filename or "cases.json")
            name=file.filename
        else:
            payload=await request.json()
            cases=normalize_cases(payload)
            name=payload.get("name","导入测试集") if isinstance(payload,dict) else "导入测试集"
    except (ValueError,UnicodeError) as exc:
        raise HTTPException(422,str(exc)) from exc
    test_id="set-"+uuid4().hex[:10]
    dump_json(RUNS_DIR/"testsets"/f"{test_id}.json",{"name":name,"cases":cases})
    return {"id":test_id,"count":len(cases),"name":name}

def _testset(test_id):
    from app.testmode.evaluator import builtins
    if test_id=="demo":
        return builtins()
    if not test_id.startswith("set-") or any(c not in "set-0123456789abcdef" for c in test_id):
        raise HTTPException(404,"测试集不存在")
    payload=read_json(RUNS_DIR/"testsets"/f"{test_id}.json")
    if payload is None:
        raise HTTPException(404,"测试集不存在")
    return payload["cases"]

@router.post("/testsets/{test_id}/run")
def run_testset(test_id:str,body:dict=Body(default={})):
    from app.testmode.evaluator import run_cases
    report=run_cases(_testset(test_id),load_grid(),body.get("engine","demo"))
    dump_json(RUNS_DIR/"testsets"/f"{test_id}.report.json",report)
    return report

@router.get("/testsets/{test_id}/report")
def report(test_id:str,format:str="json"):
    from app.testmode.evaluator import export_report
    _testset(test_id)
    payload=read_json(RUNS_DIR/"testsets"/f"{test_id}.report.json")
    if payload is None:
        raise HTTPException(404,"请先运行测试集")
    if format=="json":
        return payload
    if format not in ("md","csv"):
        raise HTTPException(422,"格式必须为json/md/csv")
    return Response(export_report(payload,format),
                    media_type="text/csv; charset=utf-8" if format=="csv" else "text/markdown; charset=utf-8",
                    headers={"Content-Disposition":f'attachment; filename="benchmark-{test_id}.{format}"'})

@router.post("/measurements/preview")
async def measurement_preview(file:UploadFile=File(...)):
    import pandas as pd
    name=file.filename or ""
    raw=await file.read(5*1024*1024+1)
    if len(raw)>5*1024*1024:
        raise HTTPException(422,"预览文件最大5MiB")
    try:
        if name.lower().endswith(".csv"):
            try:
                frame=pd.read_csv(io.BytesIO(raw),encoding="utf-8-sig")
            except UnicodeDecodeError:
                frame=pd.read_csv(io.BytesIO(raw),encoding="gb18030")
        elif name.lower().endswith(".xlsx"):
            frame=pd.read_excel(io.BytesIO(raw))
        else:
            raise ValueError("仅支持CSV和XLSX")
        rows=json.loads(frame.head(50).to_json(orient="records",force_ascii=False,date_format="iso"))
        return {"filename":name,"columns":list(map(str,frame.columns)),"rows":rows,"total":len(frame),
                "conversion_status":"planned","description":"仅文件预览，未转换为节点模型"}
    except Exception as exc:
        raise HTTPException(422,"无法预览："+str(exc)) from exc
