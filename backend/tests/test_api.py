import json
from fastapi.testclient import TestClient
from app.main import app
from app.solver.service import validate_result
from app.data.grid import load_grid
import pytest

client=TestClient(app)

def task(instruction="第 1–8 时段，母线 43 的有功负荷提高 20%。"):
    r=client.post("/api/tasks",json={"instruction":instruction,"engine":"demo"})
    assert r.status_code==200,r.text
    ident=r.json()["task_id"]
    r=client.post(f"/api/tasks/{ident}/parse")
    assert r.status_code==200,r.text
    return ident,r.json()

@pytest.mark.parametrize("enabled,maximum",[(False,5),(True,1)])
def test_question_settings_require_manual_completion(enabled,maximum):
    r=client.post("/api/tasks",json={"instruction":"午高峰高温，220kV线路L155热稳限额下降，尽量少动开关。",
         "engine":"demo","ask_questions":enabled,"max_rounds":maximum})
    ident=r.json()["task_id"]
    parsed=client.post(f"/api/tasks/{ident}/parse").json()
    if enabled:
        question=next(q for q in parsed["questions"] if q["kind"]=="periods")
        parsed=client.post(f"/api/tasks/{ident}/answer",json={"question_id":question["id"],"answer":list(range(5,13))}).json()
    assert parsed["manual_completion"] is True
    question=next(q for q in parsed["questions"] if not q.get("answered"))
    assert client.post(f"/api/tasks/{ident}/answer",json={"question_id":question["id"],"answer":{"operation":"scale","value":0.8}}).status_code==409
    assert client.post(f"/api/tasks/{ident}/confirm",json={}).status_code==409
    spec=parsed["spec"]
    for item in spec["delta_pi"]:
        item.update(operation="scale",value=0.8,confirmed=True,periods=list(range(5,13)))
    r=client.put(f"/api/tasks/{ident}/spec",json=spec)
    assert r.status_code==200,r.text
    state=client.get(f"/api/tasks/{ident}").json()["parse"]
    assert all(q["answered"] for q in state["questions"])
    assert state["manual_completion"] is False
    assert client.post(f"/api/tasks/{ident}/confirm",json={}).status_code==200

def test_import_cannot_bypass_check_or_warning_acknowledgement():
    ident,state=task()
    assert client.post(f"/api/tasks/{ident}/confirm",json={}).status_code==200
    result={"status":"mock","solver":"fixture","periods":16,"messages":["测试导入边界，不是求解"]}
    assert client.post(f"/api/tasks/{ident}/results/import",json=result).status_code==409
    assert client.post(f"/api/tasks/{ident}/check",json={}).status_code==200
    assert client.post(f"/api/tasks/{ident}/results/import",json=result).status_code==409
    response=client.post(f"/api/tasks/{ident}/results/import",json={"result":result,"acknowledge_warnings":True})
    assert response.status_code==200,response.text
    invalid,state=task("母线 99999 的负荷提高 10%。")
    assert client.post(f"/api/tasks/{invalid}/confirm",json={}).status_code==200
    assert client.post(f"/api/tasks/{invalid}/check",json={}).json()["errors"]>0
    assert client.post(f"/api/tasks/{invalid}/results/import",json={"result":result,"acknowledge_warnings":True}).status_code==409

def test_baseline_uses_each_period_snapshot():
    from copy import deepcopy
    from app.solver.service import baseline_result
    grid=load_grid()
    later=deepcopy(grid)
    later["gen"]["G1"]["PG"]+=7
    later["bus"]["B43"]["VM"]=1.02
    contextual=dict(grid,period_data={1:grid,2:later})
    result=baseline_result(contextual,2)
    assert result["gen_p"]["G1"]==[grid["gen"]["G1"]["PG"],grid["gen"]["G1"]["PG"]+7]
    assert result["bus_vm"]["B43"]==[grid["bus"]["B43"]["VM"],1.02]

def test_api_snapshot_features_and_schema():
    assert client.get("/api/health").json()["status"]=="ok"
    snaps=client.get("/api/snapshots").json()
    summary=client.get("/api/snapshots/"+snaps["default_id"]+"/summary").json()
    assert summary["counts"]=={"bus":905,"gen":237,"branch":1000}
    assert len(client.get("/api/features").json()["items"])>=40
    assert "schema" in client.get("/api/catalog").json()

def test_d7_cannot_generate_before_confirmation():
    ident,state=task("午高峰高温，220kV 线路 L155 热稳限额下降，要求满足 N−1，尽量少动开关。")
    assert state["level"]=="L4"
    assert client.post(f"/api/tasks/{ident}/confirm",json={}).status_code==409
    assert client.get(f"/api/tasks/{ident}/preview").json()["code"]==""

def test_d8_unknown_object_retained_and_cannot_solve():
    ident,state=task("母线 99999 的负荷提高 10%。")
    assert state["spec"]["delta_pi"][0]["target"]=="B99999"
    checked=client.post(f"/api/tasks/{ident}/check",json={})
    assert checked.status_code==200,checked.text
    assert any(x["code"]=="E101" for x in checked.json()["findings"])
    assert client.post(f"/api/tasks/{ident}/solve",json={"mode":"mock"}).status_code==409

def test_version_blackbox_gate_and_persistence():
    ident,state=task()
    confirmed=client.post(f"/api/tasks/{ident}/confirm",json={})
    assert confirmed.status_code==200,confirmed.text
    assert confirmed.json()["version"]==1
    assert client.post(f"/api/tasks/{ident}/solve",json={"mode":"mock"}).status_code==409
    checked=client.post(f"/api/tasks/{ident}/check",json={})
    assert checked.status_code==200,checked.text
    assert checked.json()["errors"]==0,checked.text
    if checked.json()["warnings"]:
        assert client.post(f"/api/tasks/{ident}/solve",json={"mode":"mock"}).status_code==409
    solved=client.post(f"/api/tasks/{ident}/solve",json={"mode":"mock","acknowledge_warnings":True})
    assert solved.status_code==200,solved.text
    result=client.get(f"/api/tasks/{ident}/results").json()["result"]
    assert result["status"]=="mock" and not result["branch_status"] and not result["gen_p"]
    metrics=client.post(f"/api/tasks/{ident}/evaluate").json()
    assert metrics["after"] is None
    draft=client.get(f"/api/tasks/{ident}/spec").json()
    draft["delta_pi"][0]["value"]=1.25
    assert client.put(f"/api/tasks/{ident}/spec",json=draft).status_code==200
    assert client.post(f"/api/tasks/{ident}/solve",json={"mode":"mock","acknowledge_warnings":True}).status_code==409
    assert client.post(f"/api/tasks/{ident}/confirm",json={}).status_code==200
    changes=client.get(f"/api/tasks/{ident}/diff?a=1&b=2").json()["changes"]
    assert any(x["path"]=="delta_pi" for x in changes)

def test_import_rejects_unknown_nonfinite_and_short_arrays():
    grid=load_grid()
    for result in [
        {"status":"feasible","periods":16,"bus_vm":{"B99999":[1]*16}},
        {"status":"feasible","periods":16,"gen_p":{"G1":[1]*2}},
        {"status":"feasible","periods":16,"bus_vm":{"B43":[float("nan")]*16}},
        {"status":"mock","periods":16,"gen_p":{"G1":[1]*16}}
    ]:
        with pytest.raises(ValueError):
            validate_result(result,grid,16)

def test_measurement_preview_and_imported_testset():
    r=client.post("/api/measurements/preview",files={"file":("sample.csv","节点,电压\nB43,1.01\n".encode(),"text/csv")})
    assert r.status_code==200 and r.json()["conversion_status"]=="planned"
    r=client.post("/api/testsets",json={"name":"负例","cases":[{"id":"negative","instruction":"母线 99999 的负荷提高 10%。",
         "expected_level":"L1","expected":{"delta_pi":[{"target":"B99999","param":"PD","operation":"scale","value":1.1,"periods":[]}],"structural":[]},"must_fail":"E101"}]})
    assert r.status_code==200,r.text
    report=client.post(f"/api/testsets/{r.json()['id']}/run",json={})
    assert report.status_code==200,report.text
    assert report.json()["passed"]==1,report.text
