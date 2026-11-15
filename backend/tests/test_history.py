"""Regression boundaries for source-backed history, with no solve invocation."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import history
from app.storage import store
from app.solver import service

client = TestClient(app)
RECORD_ID = "905-e2-rolling"

def test_archived_905_api_is_read_only_and_keeps_real_failures(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Historical reads must not create tasks or run solvers")
    monkeypatch.setattr(store, "create_task", forbidden)
    monkeypatch.setattr(service, "start_job", forbidden)
    catalog = client.get("/api/history")
    assert catalog.status_code == 200
    assert [x["id"] for x in catalog.json()["items"]] == [RECORD_ID]
    assert catalog.json()["integrations"]["ipopt"]["ready"] is False
    response = client.get("/api/history/" + RECORD_ID)
    assert response.status_code == 200, response.text
    record = response.json()
    assert record["kind"] == "historical_replay"
    assert record["counts"]["buses"] == 905 and record["counts"]["generators"] == 237
    assert record["period_count"] == 16 and len(record["rows"]) == 16
    assert record["summary"]["selected_branches"] == ["L151"]
    assert record["summary"]["objective_before"] == pytest.approx(1181.4988775031625)
    assert record["summary"]["objective_after"] == pytest.approx(1181.003631125388)
    assert record["validation"]["physical_revalidated"] is False
    for row in record["rows"]:
        assert row["state_index"] == 7 and row["opened_branch"] == "L151"
        assert (row["n1_before_total"], row["n1_before_failed"]) == (107, 4)
        assert (row["n1_after_total"], row["n1_after_failed"]) == (106, 4)
        assert row["n1_after_pct"] == pytest.approx(102 / 106 * 100)
    assert len(record["series"]["after"]["bus_vm"]) == 905
    assert len(record["series"]["after"]["gen_p"]) == 237
    assert client.post("/api/history/" + RECORD_ID).status_code == 405

def test_history_download_is_exactly_the_served_record():
    detail = client.get("/api/history/" + RECORD_ID)
    download = client.get("/api/history/" + RECORD_ID + "/download")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert download.json() == detail.json()

@pytest.mark.parametrize("identity", ["missing", "../e2_rolling", "e2_rolling.json"])
def test_history_identity_is_an_allowlist(identity):
    with pytest.raises(KeyError):
        history.record_path(identity)

def test_truncated_device_series_is_rejected_instead_of_displayed(tmp_path, monkeypatch):
    record = deepcopy(history.load_history(RECORD_ID))
    record["series"]["after"]["bus_vm"]["B43"] = [1.0] * 15
    corrupt = tmp_path / "bad-series.json"
    corrupt.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(history, "HISTORY_FILES", {RECORD_ID: corrupt})
    assert client.get("/api/history/" + RECORD_ID).status_code == 503
    assert client.get("/api/history").status_code == 503
    assert client.get("/api/history/" + RECORD_ID + "/download").status_code == 503

def test_n1_counts_cannot_be_hidden_behind_a_false_hundred_percent(tmp_path, monkeypatch):
    record = deepcopy(history.load_history(RECORD_ID))
    record["rows"][0]["n1_after_pct"] = 100.0
    corrupt = tmp_path / "bad-n1.json"
    corrupt.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(history, "HISTORY_FILES", {RECORD_ID: corrupt})
    assert client.get("/api/history/" + RECORD_ID).status_code == 503

def test_absent_history_remains_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_FILES", {RECORD_ID: tmp_path / "absent.json"})
    response = client.get("/api/history")
    assert response.status_code == 200 and response.json()["items"] == []
    assert client.get("/api/history/" + RECORD_ID).status_code == 404
