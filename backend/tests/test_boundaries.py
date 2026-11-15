"""Independent state and black-box boundary tests; no optimization solver runs."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import time

import pytest
from fastapi import HTTPException

from app.data.grid import load_grid, snapshot_ref
from app.storage import store
from app.solver import service
from app.spec.models import ModelSpec
from app.api import router as api_module


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(store, "_ENGINE", None)
    monkeypatch.setattr(service, "JOBS", {})
    monkeypatch.delenv("SOLVER_CMD", raising=False)
    yield tmp_path
    if store._ENGINE is not None:
        store._ENGINE.dispose()


def confirmed_task():
    grid = load_grid()
    task = store.create_task("母线 43 的有功负荷提高 20%。", snapshot_id=grid["meta"]["id"])
    spec = ModelSpec(
        task_id=task["task_id"], instruction=task["instruction"], snapshot=snapshot_ref(),
        delta_pi=[{"id":"dp-1","component":"bus","target":"B43","param":"PD","operation":"scale",
                   "value":1.2,"periods":[],"source":"explicit","evidence":"母线 43 的有功负荷提高 20%","confirmed":True}],
    ).model_dump()
    spec = store.save_version(task["task_id"], spec, {"code":"# boundary test artifact\n","latex":[],"latex_text":"","size":{}})
    store.update_task(task["task_id"], parse={"spec":spec,"questions":[],"entities":[],"level":"L1","engine":"demo"})
    return task["task_id"], spec, grid


def wait_job(job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = service.get_job(job_id)
        folder = store.version_dir(job["task_id"],job["version"]) / "solver_jobs" / job_id
        if job["status"] in ("completed","error") and (folder / "job.json").exists():
            return job
        time.sleep(.02)
    pytest.fail(f"External test stub failed to reach a terminal recorded state: {service.get_job(job_id)}")


def test_unconfirmed_metrics_cannot_overwrite_confirmed_version(isolated):
    task_id, spec, _ = confirmed_task()
    store.save_evidence(task_id,"metrics",{"basis":"v1","score":1})
    prior_path = store.version_dir(task_id,1) / "metrics.json"
    prior_bytes = prior_path.read_bytes()
    draft = deepcopy(spec)
    draft.update(version=2,parent_version=1)
    draft["delta_pi"][0]["value"] = 1.3
    store.update_task(task_id,draft=draft)
    store.save_evidence(task_id,"metrics",{"basis":"v2-draft","score":2})
    assert prior_path.read_bytes() == prior_bytes
    draft_files = list((store.RUNS_DIR / task_id / "drafts").glob("*/metrics.json"))
    assert len(draft_files) == 1
    assert json.loads(draft_files[0].read_text(encoding="utf-8"))["basis"] == "v2-draft"


@pytest.mark.parametrize("advance_version",[False,True])
def test_late_old_result_never_attaches_to_changed_draft(isolated,advance_version):
    task_id, spec, grid = confirmed_task()
    draft = deepcopy(spec)
    draft["delta_pi"][0]["value"] = 1.4
    if advance_version:
        draft.update(version=2,parent_version=1)
    store.update_task(task_id,draft=draft,result=None)
    imported = service.import_result(task_id,spec,grid,{"status":"feasible","periods":16,"solver":"test-fixture","branch_status":{"L155":[0]*16}})
    assert imported["version"] == 1
    assert store.get_task(task_id)["result"] is None
    saved = json.loads((store.version_dir(task_id,1) / "result.json").read_text(encoding="utf-8"))
    assert saved["source"] == "import"
    assert saved["branch_status"]["L155"] == [0]*16


def test_snapshot_selection_reaches_grid_loader(monkeypatch):
    calls, ref_calls, loaded_refs = [], [], []
    def fake_load(snapshot_id=None):
        calls.append(snapshot_id)
        return {"summary":{"id":snapshot_id},"topology":{"selected":snapshot_id}}
    def fake_reference(snapshot_id=None):
        ref_calls.append(snapshot_id)
        return {"id":snapshot_id,"files":[snapshot_id,"next-file"],"periods":2,"single_snapshot":False}
    def fake_task_grid(reference):
        loaded_refs.append(deepcopy(reference))
        return {"selected":reference["id"],"period_files":dict(enumerate(reference["files"],1))}
    monkeypatch.setattr(api_module,"load_grid",fake_load)
    monkeypatch.setattr(api_module,"snapshot_ref",fake_reference)
    monkeypatch.setattr(api_module,"load_task_grid",fake_task_grid)
    monkeypatch.setattr(api_module,"get_snapshots",lambda:[{"id":"first"},{"id":"chosen"}])
    assert api_module.summary("chosen") == {"id":"chosen"}
    assert api_module.topology("chosen") == {"selected":"chosen"}
    fresh = api_module._grid({"snapshot_id":"chosen"})
    assert fresh["period_files"] == {1:"chosen",2:"next-file"}
    bound = {"id":"bound","files":["bound","bound-second"],"periods":2,"single_snapshot":False}
    persisted = api_module._grid({"snapshot_id":"old-choice","draft":{"snapshot":bound}})
    assert persisted["period_files"] == {1:"bound",2:"bound-second"}
    assert calls == ["chosen","chosen"]
    assert ref_calls == ["chosen"]  # An existing task keeps its exact per-period file binding.
    assert loaded_refs == [
        {"id":"chosen","files":["chosen","next-file"],"periods":2,"single_snapshot":False},
        bound,
    ]


def test_default_snapshot_is_pinned_when_task_is_created(isolated,monkeypatch):
    monkeypatch.setattr(api_module,"get_snapshots",lambda:[{"id":"initial-default.txt"}])
    task = api_module.new_task(api_module.NewTask(instruction="母线43负荷提高20%"))
    assert task["snapshot_id"] == "initial-default.txt"


@pytest.mark.parametrize("field",["snapshot","instruction"])
def test_confirm_cannot_rewrite_data_identity_or_original_instruction(isolated,field):
    task_id,spec,_ = confirmed_task()
    payload = deepcopy(spec)
    if field == "snapshot":
        payload["snapshot"]["id"] = "different-file.txt"
    else:
        payload["instruction"] = "被改写的原文"
    with pytest.raises(HTTPException) as error:
        api_module.confirm(task_id,{"spec":payload})
    assert error.value.status_code in (409,422)
    assert len(store.list_versions(task_id)) == 1


@pytest.mark.parametrize("payload",[
    {"status":"feasible","periods":16,"bus_vm":{"B99999":[1.]*16}},
    {"status":"feasible","periods":16,"bus_vm":{"B43":[1.]*15}},
    {"status":"feasible","periods":16,"gen_p":{"G237":[float("nan")]*16}},
    {"status":"feasible","periods":16,"branch_status":{"L155":[2]*16}},
    {"status":"mock","periods":16,"branch_status":{"L155":[0]*16}},
    {"status":"feasible","periods":15},
])
def test_import_rejects_unknown_ids_dimensions_nonfinite_and_mock_fabrication(isolated,payload):
    task_id,spec,grid = confirmed_task()
    with pytest.raises(ValueError):
        service.import_result(task_id,spec,grid,payload)
    assert store.get_task(task_id)["result"] is None
    assert not (store.version_dir(task_id,1) / "result.json").exists()


def test_unconfigured_external_never_starts_job(isolated):
    task_id,spec,grid = confirmed_task()
    with pytest.raises(ValueError,match="SOLVER_CMD"):
        service.start_job(task_id,spec,grid,"external")
    assert service.JOBS == {}


@pytest.mark.parametrize("command",['python "unterminated','python {unknown_placeholder}'])
def test_malformed_external_command_has_recorded_error_terminal(isolated,monkeypatch,command):
    task_id,spec,grid = confirmed_task()
    monkeypatch.setenv("SOLVER_CMD",command)
    job = wait_job(service.start_job(task_id,spec,grid,"external")["job_id"])
    assert job["status"] == "error"
    assert job["error"]
    assert job["result"] is None
    assert store.get_task(task_id)["result"] is None


STUB = r'''
import argparse,json,pathlib,subprocess,sys,time
parser=argparse.ArgumentParser()
parser.add_argument("--spec")
parser.add_argument("--out")
parser.add_argument("--mode",default="valid")
args=parser.parse_args()
spec=json.loads(pathlib.Path(args.spec).read_text(encoding="utf-8"))
if args.mode=="child-timeout":
    marker=str(pathlib.Path(args.out).parent/"child-survived.txt")
    code="import sys,time,pathlib;time.sleep(2.5);pathlib.Path(sys.argv[1]).write_text('survived')"
    subprocess.Popen([sys.executable,"-c",code,marker])
    print("test stub child started",flush=True)
    time.sleep(20)
else:
    result={"status":"feasible","solver":"boundary-test-stub","periods":spec["omega"]["periods"],"branch_status":{},"messages":["Test fixture only; no solver was called."]}
    if args.mode=="invalid":
        result["bus_vm"]={"B99999":[1.]*spec["omega"]["periods"]}
    pathlib.Path(args.out).write_text(json.dumps(result),encoding="utf-8")
    print("test stub result written",flush=True)
'''


def stub_command(tmp_path,mode):
    stub = tmp_path / "solver boundary stub.py"
    stub.write_text(STUB,encoding="utf-8")
    return f'"{sys.executable}" "{stub}" --spec "{{spec}}" --out "{{out}}" --mode {mode}'


@pytest.mark.parametrize("mode,expected",[("valid","completed"),("invalid","error")])
def test_external_stub_uses_spec_file_and_same_result_validation(isolated,monkeypatch,mode,expected):
    task_id,spec,grid = confirmed_task()
    monkeypatch.setenv("SOLVER_CMD",stub_command(isolated,mode))
    job = wait_job(service.start_job(task_id,spec,grid,"external")["job_id"])
    assert job["status"] == expected
    if expected == "completed":
        assert job["result"]["source"] == "external"
        assert job["result"]["solver"] == "boundary-test-stub"
        assert job["result"]["branch_status"] == {}
        assert store.get_task(task_id)["result"]["version"] == 1
    else:
        assert "B99999" in job["error"]
        assert store.get_task(task_id)["result"] is None


def test_external_timeout_stops_only_its_own_child_tree(isolated,monkeypatch):
    task_id,spec,grid = confirmed_task()
    spec = deepcopy(spec)
    spec["solver_req"]["time_limit_s"] = 1
    monkeypatch.setenv("SOLVER_CMD",stub_command(isolated,"child-timeout"))
    job = wait_job(service.start_job(task_id,spec,grid,"external")["job_id"],timeout=12)
    assert job["status"] == "error"
    assert "时限" in job["error"]
    marker = store.version_dir(task_id,1) / "solver_jobs" / job["job_id"] / "child-survived.txt"
    time.sleep(2.8)
    assert not marker.exists(), "The external wrapper's child survived the timeout"
    assert store.get_task(task_id)["result"] is None
