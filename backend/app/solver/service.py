"""Black-box boundary. This module never performs optimization."""
import json
import math
import os
import shlex
import signal
import subprocess
import threading
import time
from uuid import uuid4
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from app.config import ROOT, dump_json
from app.data.grid import snapshot_dir
from app.storage import store

class FlowSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pf: list[float]
    qf: list[float]
    pt: list[float]
    qt: list[float]

class ResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["optimal", "feasible", "infeasible", "error", "mock"]
    solver: str = "external"
    objective: float | None = None
    periods: int = Field(default=16, ge=1, le=16)
    timings_s: dict[str, float] = Field(default_factory=dict)
    branch_status: dict[str, list[int]] = Field(default_factory=dict)
    gen_p: dict[str, list[float]] = Field(default_factory=dict)
    bus_vm: dict[str, list[float]] = Field(default_factory=dict)
    branch_flow: dict[str, FlowSeries] = Field(default_factory=dict)
    messages: list[str] = Field(default_factory=list)

def validate_result(payload, grid, periods):
    result = ResultModel.model_validate(payload).model_dump()
    if result["periods"] != periods:
        raise ValueError(f"结果时段数必须等于规格的 {periods}，当前为 {result['periods']}")
    for field, component in [("branch_status", "branch"), ("gen_p", "gen"), ("bus_vm", "bus"), ("branch_flow", "branch")]:
        for target, series in result[field].items():
            if target not in grid[component]:
                raise ValueError(f"结果对象不存在：{target}")
            arrays = series.values() if field == "branch_flow" else [series]
            for values in arrays:
                if len(values) != periods:
                    raise ValueError(f"{target}.{field} 长度应为 {periods}，当前为 {len(values)}")
                if any(not math.isfinite(float(v)) for v in values):
                    raise ValueError(f"{target}.{field} 含非有限数值")
                if field == "branch_status" and any(v not in (0, 1) for v in values):
                    raise ValueError(f"{target} 的支路状态只能为0或1")
    if result["objective"] is not None and not math.isfinite(result["objective"]):
        raise ValueError("目标值必须为有限数值")
    if any(not math.isfinite(v) or v < 0 for v in result["timings_s"].values()):
        raise ValueError("耗时必须为非负有限数值")
    if result["status"] == "mock" and any(result[x] for x in ("branch_status","gen_p","bus_vm","branch_flow")):
        raise ValueError("mock 结果不得包含伪造的调整后数据")
    return result

JOBS = {}
JOB_LOCK = threading.RLock()

def get_job(job_id):
    with JOB_LOCK:
        if job_id not in JOBS:
            raise KeyError("作业不存在；服务重启后可在版本记录查看结果")
        return dict(JOBS[job_id])

def _patch(job_id, **values):
    with JOB_LOCK:
        JOBS[job_id].update(values)

def _log(job_id, text):
    with JOB_LOCK:
        JOBS[job_id]["logs"].append(str(text))
        JOBS[job_id]["logs"] = JOBS[job_id]["logs"][-3000:]

def _store_result(task_id, version, result, source):
    with store.LOCK:
        task = store.get_task(task_id)
        result = dict(result, source=source, version=version)
        saved_spec = store.get_version(task_id, version)["spec"]
        dump_json(store.version_dir(task_id, version) / "result.json", result)
        if task.get("confirmed_version") == version and task.get("draft") == saved_spec:
            store.update_task(task_id, result=result, metrics=None)
        store.log(task_id, f"v{version} 黑盒结果已记录：{result['status']}（{source}）。")
    return result

def start_job(task_id, spec, grid, mode):
    if mode not in ("mock", "external"):
        raise ValueError("请使用mock/external；导入结果使用单独的上传入口")
    if mode == "external" and not os.getenv("SOLVER_CMD", "").strip():
        raise ValueError("外部求解器接口预留：尚未配置 SOLVER_CMD")
    job_id = "job-" + uuid4().hex[:12]
    version = spec["version"]
    with JOB_LOCK:
        JOBS[job_id] = {"job_id":job_id, "task_id":task_id, "version":version, "status":"queued",
                        "logs":[], "result":None, "error":None, "mode":mode}
    if mode == "mock":
        result = validate_result({"status":"mock","solver":"mock","periods":spec["omega"]["periods"],
                                  "messages":["黑盒占位：未调用求解器；结果页仅展示调整前合成演示断面。"]},
                                 grid, spec["omega"]["periods"])
        result = _store_result(task_id, version, result, "mock")
        _patch(job_id, status="completed", result=result)
        _log(job_id, result["messages"][0])
        return get_job(job_id)
    def worker():
        started = time.perf_counter()
        _patch(job_id, status="running")
        folder = None
        process = None
        try:
            folder = store.version_dir(task_id, version) / "solver_jobs" / job_id
            folder.mkdir(parents=True, exist_ok=True)
            spec_path, out_path = folder/"spec.json", folder/"result.json"
            dump_json(spec_path, spec)
            command = os.environ["SOLVER_CMD"]
            snapshots = str(snapshot_dir())
            values = {"spec":str(spec_path), "dir":snapshots,
                      "snapshots":snapshots, "out":str(out_path)}
            tokens = shlex.split(command, posix=os.name != "nt")
            args = [token.strip('"').format(**values) for token in tokens]
            if not args:
                raise ValueError("SOLVER_CMD为空")
            _log(job_id, "调用已配置的外部命令；本工作台不选择算法、不调参、不求解。")
            process = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace", shell=False,
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=="nt" else 0,
                                       start_new_session=os.name!="nt")
            def reader():
                for line in process.stdout:
                    _log(job_id, line.rstrip())
            thread = threading.Thread(target=reader, daemon=True)
            thread.start()
            timeout = max(1, min(3600, int(spec["solver_req"]["time_limit_s"])))
            code = process.wait(timeout=timeout)
            thread.join(timeout=2)
            if code != 0:
                raise RuntimeError(f"外部命令退出码 {code}")
            if not out_path.exists():
                raise ValueError("外部命令未生成结果JSON")
            if out_path.stat().st_size > 32*1024*1024:
                raise ValueError("外部结果超过32MiB限制")
            result = validate_result(json.loads(out_path.read_text(encoding="utf-8")), grid, spec["omega"]["periods"])
            result = _store_result(task_id, version, result, "external")
            _patch(job_id, status="completed", result=result)
        except subprocess.TimeoutExpired:
            if process is not None:
                if os.name == "nt":
                    subprocess.run(["taskkill","/PID",str(process.pid),"/T","/F"],capture_output=True,timeout=10)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            _patch(job_id, status="error", error="外部求解器超过时限，已停止本次子进程。")
        except Exception as exc:
            _patch(job_id, status="error", error=str(exc))
        finally:
            _patch(job_id, elapsed_s=round(time.perf_counter()-started, 3))
            job = get_job(job_id)
            if job["error"]:
                _log(job_id, job["error"])
                store.log(task_id, job["error"])
            if folder is not None and folder.exists():
                (folder/"log.txt").write_text("\n".join(get_job(job_id)["logs"]), encoding="utf-8")
                dump_json(folder/"job.json", get_job(job_id))
    threading.Thread(target=worker, daemon=True).start()
    return get_job(job_id)

def import_result(task_id, spec, grid, payload):
    result = validate_result(payload, grid, spec["omega"]["periods"])
    return _store_result(task_id, spec["version"], result, "import")

def baseline_result(grid, periods=16):
    frames=grid.get("period_data")
    def frame(t):
        if frames is None:
            return grid
        value=frames.get(t,frames.get(str(t)))
        if value is None:
            raise ValueError(f"第{t}时段基态断面缺失")
        return value
    return {
        "branch_status":{k:[int(frame(t)["branch"][k]["BR_STATUS"]) for t in range(1,periods+1)] for k in grid["branch"]},
        "gen_p":{k:[frame(t)["gen"][k]["PG"] for t in range(1,periods+1)] for k in grid["gen"]},
        "bus_vm":{k:[frame(t)["bus"][k]["VM"] for t in range(1,periods+1)] for k in grid["bus"]},
        "branch_flow":{k:{f.lower():[frame(t)["branch"][k][f] for t in range(1,periods+1)] for f in ("PF","QF","PT","QT")} for k in grid["branch"]}
    }
