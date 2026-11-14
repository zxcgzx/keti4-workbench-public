"""Isolated bounded construction of exactly the displayed generated module."""
from __future__ import annotations
import copy, hashlib, importlib.util, json, subprocess, sys, tempfile, time
from pathlib import Path

DRIVER = """import importlib.util,json,sys,time
import pyomo.environ as pyo
source,datafile,specfile=sys.argv[1:4]
module_spec=importlib.util.spec_from_file_location('reviewed_model',source)
module=importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(module)
data=json.load(open(datafile,encoding='utf-8'))
spec=json.load(open(specfile,encoding='utf-8'))
start=time.perf_counter()
model=module.build_model(data,spec)
variables=list(model.component_data_objects(pyo.Var,active=True))
constraints=list(model.component_data_objects(pyo.Constraint,active=True))
print(json.dumps({'variables':len(variables),'binary':sum(v.is_binary() for v in variables),'continuous':sum(not v.is_binary() for v in variables),'constraints':len(constraints),'elapsed_ms':round((time.perf_counter()-start)*1000,3)}))
"""

def trial_build(code,spec,grid,timeout_s=60):
    sha=hashlib.sha256(code.encode("utf-8")).hexdigest()
    if any(x.get("custom_code") for x in spec.get("structural",[])):
        return {"status":"skipped","reason":"含待审核 custom_code，不自动执行外部生成代码。","code_sha256":sha,"periods":1,"n_minus_1":False}
    if importlib.util.find_spec("pyomo") is None:
        return {"status":"skipped","reason":"未安装 Pyomo，保留语法与模板计数检查。","code_sha256":sha,"periods":1,"n_minus_1":False}
    trial_spec=copy.deepcopy(spec);trial_spec["omega"]["periods"]=1;trial_spec["omega"]["n_minus_1"]=False
    payload={k:grid[k] for k in ("bus","gen","branch","meta","meshed")}
    if grid.get("period_data") is not None:
        frame=grid["period_data"].get(1,grid["period_data"].get("1"))
        if frame is None:return {"status":"error","reason":"缺少第1时段实际断面","code_sha256":sha,"periods":1,"n_minus_1":False}
        payload["period_data"]={"1":{k:frame[k] for k in ("bus","gen","branch","meta","meshed")}}
    with tempfile.TemporaryDirectory(prefix="keti4-build-") as folder:
        folder=Path(folder)
        for name,content in [("model.py",code),("driver.py",DRIVER),("data.json",json.dumps(payload,ensure_ascii=False)),("spec.json",json.dumps(trial_spec,ensure_ascii=False))]:
            (folder/name).write_text(content,encoding="utf-8")
        started=time.perf_counter()
        try:
            proc=subprocess.run([sys.executable,"-I",str(folder/"driver.py"),str(folder/"model.py"),str(folder/"data.json"),str(folder/"spec.json")],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=timeout_s,cwd=str(folder))
            if proc.returncode:
                return {"status":"error","reason":proc.stderr[-4000:] or proc.stdout[-4000:],"exit_code":proc.returncode,"code_sha256":sha,"periods":1,"n_minus_1":False}
            result=json.loads(proc.stdout.strip().splitlines()[-1])
            return {"status":"pass",**result,"process_ms":round((time.perf_counter()-started)*1000,3),"code_sha256":sha,"periods":1,"n_minus_1":False}
        except subprocess.TimeoutExpired:
            return {"status":"error","reason":f"试构建超过 {timeout_s} 秒，子进程已结束。","code_sha256":sha,"periods":1,"n_minus_1":False}
        except (ValueError,OSError) as exc:
            return {"status":"error","reason":str(exc),"code_sha256":sha,"periods":1,"n_minus_1":False}
