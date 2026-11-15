"""Teacher-discussion regressions: dynamic model identity and pre-solve negotiation.

These tests build the displayed Pyomo source but never optimize or fabricate
operating results. API tasks and SQLite files are isolated in pytest tmp_path.
"""
from copy import deepcopy
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app.data.grid import load_grid
from app.main import app
from app.modeling.engine import parse_instruction
from app.modeling.generator import estimate_size
from app.modeling.trial import trial_build
from app.solver import service
from app.storage import store


@pytest.fixture(scope="module")
def grid():
    return load_grid()


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(store, "_ENGINE", None)
    monkeypatch.setattr(service, "JOBS", {})
    monkeypatch.delenv("SOLVER_CMD", raising=False)
    monkeypatch.delenv("RISK_ENDPOINT", raising=False)
    with TestClient(app) as value:
        yield value
    if store._ENGINE is not None:
        store._ENGINE.dispose()


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def parsed_task(client, instruction, **settings):
    task = ok(client.post("/api/tasks", json={
        "instruction": instruction, "engine": "demo", **settings,
    }))
    ident = task["task_id"]
    return ident, ok(client.post(f"/api/tasks/{ident}/parse"))


@pytest.mark.parametrize("instruction,expected", [
    ("使用 AC-OPF，母线43负荷提高10%。", "multi_period_acopf"),
    ("使用 acopf，母线43负荷提高10%。", "multi_period_acopf"),
    ("固定拓扑，母线43负荷提高10%。", "multi_period_acopf"),
    ("使用 AC-OTS，母线43负荷提高10%。", "multi_period_ac_ots"),
    ("使用 acots，母线43负荷提高10%。", "multi_period_ac_ots"),
])
def test_problem_type_is_read_from_explicit_instruction(grid, instruction, expected):
    parsed = parse_instruction(instruction, grid, "teacher-type")
    assert parsed["spec"]["omega"]["problem"] == expected
    assert parsed["spec"]["delta_pi"][0]["target"] == "B43"
    assert parsed["spec"]["delta_pi"][0]["value"] == pytest.approx(1.1)


@pytest.mark.parametrize("problem", [None, "multi_period_acopf", "multi_period_ac_ots"])
def test_task_model_choice_is_applied_before_confirmation(client, problem):
    instruction = "使用 AC-OPF，母线43负荷提高10%。"
    ident, parsed = parsed_task(client, instruction, problem=problem)
    expected = problem or "multi_period_acopf"
    assert parsed["spec"]["omega"]["problem"] == expected
    assert ok(client.get(f"/api/tasks/{ident}/spec"))["omega"]["problem"] == expected


@pytest.mark.parametrize("chosen,text_model,phrase", [
    ("multi_period_ac_ots", "multi_period_acopf", "固定拓扑 AC-OPF"),
    ("multi_period_acopf", "multi_period_ac_ots", "AC-OTS"),
])
def test_explicit_task_choice_overrides_text_with_visible_conflict(client, chosen, text_model, phrase):
    _, parsed = parsed_task(client, f"采用{phrase}，母线43负荷提高10%。", problem=chosen)
    assert parsed["spec"]["omega"]["problem"] == chosen
    selection = parsed.get("problem_selection")
    assert isinstance(selection, dict) and selection, parsed
    recorded = json.dumps(selection, ensure_ascii=False)
    assert chosen in recorded and text_model in recorded
    assert parsed.get("warnings"), "A conflicting model selection must be visible to the user"
    if chosen == "multi_period_ac_ots":
        assert not any(op["op_id"] == "multi_period_acopf" for op in parsed["spec"]["structural"])


def test_unsupported_problem_type_is_rejected(client):
    response = client.post("/api/tasks", json={
        "instruction": "母线43负荷提高10%。", "engine": "demo", "problem": "arbitrary_problem",
    })
    assert response.status_code == 422


def test_opf_ots_roundtrip_keeps_preview_equations_and_actual_build_consistent(client, grid):
    ident, parsed = parsed_task(client, "固定拓扑 AC-OPF，母线43负荷提高20%。")
    original_delta = deepcopy(parsed["spec"]["delta_pi"])
    b7 = []
    prior_version = None
    sequence = [
        ("multi_period_acopf", 0),
        ("multi_period_ac_ots", 225),
        ("multi_period_acopf", 0),
    ]
    for position, (problem, expected_binary) in enumerate(sequence):
        spec = ok(client.get(f"/api/tasks/{ident}/spec"))
        if position:
            spec["omega"]["problem"] = problem
            spec = ok(client.put(f"/api/tasks/{ident}/spec", json=spec))
        assert spec["omega"]["problem"] == problem
        assert spec["delta_pi"] == original_delta
        if problem == "multi_period_ac_ots":
            assert not any(op["op_id"] == "multi_period_acopf" for op in spec["structural"])
        preview = ok(client.get(f"/api/tasks/{ident}/preview"))
        assert preview["size"]["binary"] == expected_binary * 16
        b7.append(next(block for block in preview["latex"] if block["id"] == "B7")["latex"])
        confirmed = ok(client.post(f"/api/tasks/{ident}/confirm", json={"spec": spec}))
        saved = confirmed["spec"]
        assert saved["parent_version"] == prior_version
        if position:
            assert saved.get("change_reason"), "Model-type transitions need an auditable reason"
        prior_version = saved["version"]
        source = confirmed["artifacts"]["code"]
        built = trial_build(source, saved, grid, timeout_s=60)
        assert built["status"] == "pass", built
        assert built["code_sha256"] == hashlib.sha256(source.encode()).hexdigest()
        assert built["binary"] == expected_binary
        one_period = deepcopy(saved)
        one_period["omega"].update(periods=1, n_minus_1=False)
        estimate = estimate_size(one_period, grid)
        for key in ("binary", "continuous", "variables", "constraints"):
            assert built[key] == estimate[key]
    assert b7[0] != b7[1], "B7 must explain fixed versus switchable topology"
    assert b7[0] == b7[2]
    assert service.JOBS == {}


def test_confirm_check_baseline_risk_and_negotiation_need_no_solve(client):
    ident, parsed = parsed_task(client, "母线43负荷提高10%。")
    confirmed = ok(client.post(f"/api/tasks/{ident}/confirm", json={}))
    checked = ok(client.post(f"/api/tasks/{ident}/check", json={"try_build": False}))
    assert checked["errors"] == 0
    before = ok(client.get(f"/api/tasks/{ident}"))
    assert before["result"] is None
    assert service.JOBS == {}

    metrics = ok(client.post(f"/api/tasks/{ident}/evaluate"))
    assert metrics["evaluation_stage"] == "baseline"
    assert metrics["after"] is None
    assert metrics["before"]["voltage"]["violations"] == 233
    assert metrics["risk"]["basis"] == "调整前真实断面"
    suggestions = metrics["suggestions"]
    assert suggestions
    chosen = [s for s in suggestions if s["id"] in ("risk-switching", "risk-voltage")]
    assert {s["id"] for s in chosen} == {"risk-switching", "risk-voltage"}
    for suggestion in chosen:
        if suggestion.get("delta"):
            assert suggestion["delta"]["value"] is None
        if suggestion.get("structural"):
            assert suggestion["structural"]["params"]["k"] is None

    adjusted = ok(client.post(f"/api/tasks/{ident}/adjust", json={
        "suggestion_ids": [s["id"] for s in chosen],
    }))["spec"]
    assert adjusted["parent_version"] == confirmed["version"]
    assert adjusted["version"] == confirmed["version"] + 1
    assert adjusted["change_reason"]
    new_delta = adjusted["delta_pi"][len(parsed["spec"]["delta_pi"]):]
    new_ops = adjusted["structural"][len(parsed["spec"]["structural"]):]
    assert new_delta and all(d["value"] is None and not d["confirmed"] for d in new_delta)
    assert any(op["op_id"] == "switching_limit_per_period" and op["params"]["k"] is None for op in new_ops)
    assert client.post(f"/api/tasks/{ident}/confirm", json={}).status_code == 409
    after = ok(client.get(f"/api/tasks/{ident}"))
    assert after["result"] is None
    assert service.JOBS == {}


def test_structural_only_risk_suggestion_requires_a_value_before_confirmation(client):
    ident, parsed = parsed_task(client, "母线43负荷提高10%。")
    original = ok(client.post(f"/api/tasks/{ident}/confirm", json={}))
    assert ok(client.post(f"/api/tasks/{ident}/check", json={}))["errors"] == 0
    metrics = ok(client.post(f"/api/tasks/{ident}/evaluate"))
    assert any(s["id"] == "risk-switching" for s in metrics["suggestions"])
    draft = ok(client.post(f"/api/tasks/{ident}/adjust", json={
        "suggestion_ids": ["risk-switching"],
    }))["spec"]
    assert draft["delta_pi"] == parsed["spec"]["delta_pi"], "No empty delta may mask the structural gate"
    operator = next(op for op in draft["structural"] if op["op_id"] == "switching_limit_per_period")
    assert operator["params"]["k"] is None
    assert client.post(f"/api/tasks/{ident}/confirm", json={}).status_code == 409

    operator["params"]["k"] = 2
    saved = ok(client.put(f"/api/tasks/{ident}/spec", json=draft))
    confirmed = ok(client.post(f"/api/tasks/{ident}/confirm", json={"spec": saved}))
    assert confirmed["spec"]["parent_version"] == original["version"]
    assert next(op for op in confirmed["spec"]["structural"]
                if op["op_id"] == "switching_limit_per_period")["params"]["k"] == 2
    assert service.JOBS == {}


@pytest.mark.parametrize("field,missing", [("branches", []), ("limit_mw", None)])
def test_incomplete_section_operator_cannot_be_confirmed(client, field, missing):
    ident, parsed = parsed_task(client, "由L428、L429组成的断面，有功限额设为300 MW。")
    draft = parsed["spec"]
    assert not draft["delta_pi"], "This gate must be exercised without delta-value validation"
    operator = next(op for op in draft["structural"] if op["op_id"] == "section_limit")
    original_value = deepcopy(operator["params"][field])
    operator["params"][field] = missing
    saved = ok(client.put(f"/api/tasks/{ident}/spec", json=draft))
    assert client.post(f"/api/tasks/{ident}/confirm", json={"spec": saved}).status_code == 409
    repaired = ok(client.get(f"/api/tasks/{ident}/spec"))
    next(op for op in repaired["structural"] if op["op_id"] == "section_limit")["params"][field] = original_value
    ok(client.put(f"/api/tasks/{ident}/spec", json=repaired))
    assert client.post(f"/api/tasks/{ident}/confirm", json={}).status_code == 200


def test_defaultable_objective_beta_remains_confirmable(client):
    ident, parsed = parsed_task(client, "母线43负荷提高10%，尽量少动开关。")
    penalty = next(op for op in parsed["spec"]["structural"] if op["op_id"] == "switching_penalty")
    assert penalty["params"]["beta"] is None
    confirmed = ok(client.post(f"/api/tasks/{ident}/confirm", json={}))
    saved_penalty = next(op for op in confirmed["spec"]["structural"] if op["op_id"] == "switching_penalty")
    assert saved_penalty["params"]["beta"] is None, "Configured template fallback must not rewrite the user's specification"
    assert confirmed["artifacts"]["code"]
