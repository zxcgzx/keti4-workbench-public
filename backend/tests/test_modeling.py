import copy, hashlib, json
import pytest
from app.data.grid import load_grid
from app.spec.models import ModelSpec, ROOT, derived_level
from app.modeling.engine import parse_instruction, answer_parse
from app.modeling.generator import generate_artifacts, estimate_size
from app.checks.validator import run_checks

@pytest.fixture(scope="module")
def grid():
    return load_grid()

CASES=json.loads((ROOT/"data/testsets/demo_cases.json").read_text(encoding="utf-8"))

@pytest.mark.parametrize("case",CASES,ids=lambda c:c["id"])
def test_reference_cases(case,grid):
    parsed=parse_instruction(case["instruction"],grid,case["id"])
    assert parsed["level"]==case["expected_level"]
    actual=parsed["spec"]
    assert len(actual["delta_pi"])==len(case["expected"]["delta_pi"])
    assert len(actual["structural"])==len(case["expected"]["structural"])
    for expected in case["expected"]["delta_pi"]:
        found=next(d for d in actual["delta_pi"] if d["target"]==expected["target"] and d["param"]==expected["param"])
        for key,value in expected.items():
            if isinstance(value,float):assert found[key]==pytest.approx(value)
            else:assert found[key]==value
    for expected in case["expected"]["structural"]:
        found=next(d for d in actual["structural"] if d["op_id"]==expected["op_id"])
        for key,value in expected["params"].items():assert found["params"][key]==value
    check=run_checks(actual,grid,parsed)
    codes={f["code"] for f in check["findings"]}
    assert set(case.get("expected_codes",[])).issubset(codes)
    assert set(case.get("expected_errors",[])).issubset(codes)
    assert not set(case.get("forbidden_codes",[])).intersection(codes)
    assert set(case.get("questions",[])).issubset({q["kind"] for q in parsed["questions"]})
    if case["id"]=="D8":
        assert actual["delta_pi"][0]["target"]=="B99999"
        assert check["can_solve"] is False
    if case["id"]=="D9":
        assert not actual["delta_pi"] and not actual["structural"]
    if case["id"]=="D10":
        island=next(f for f in check["findings"] if f["code"]=="W206")
        assert island["isolated_buses"]==["B774"]
        assert island["net_injection_mw"]==pytest.approx(135.5,abs=0.2)

def test_confirmation_no_guessed_amplitude(grid):
    state=parse_instruction(CASES[6]["instruction"],grid,"confirm")
    assert state["spec"]["delta_pi"][0]["value"] is None
    assert not run_checks(state["spec"],grid,state)["can_solve"]
    amplitude=next(q for q in state["questions"] if q["kind"]=="amplitude")
    assert next(x for x in amplitude["options"] if x["value"]=="default")["disabled"]
    with pytest.raises(ValueError):answer_parse(state,amplitude["id"],{"operation":"scale","value":1.2},grid)
    state=answer_parse(state,amplitude["id"],{"operation":"scale","value":0.8},grid)
    assert not run_checks(state["spec"],grid,state)["can_solve"]
    period=next(q for q in state["questions"] if q["kind"]=="periods")
    state=answer_parse(state,period["id"],[5,6,7,8,9,10,11,12],grid)
    assert state["spec"]["delta_pi"][0]["confirmed"] is True
    assert run_checks(state["spec"],grid,state)["can_solve"] is True
    assert derived_level(state["spec"])=="L4"
    assert not any(x["op_id"].lower().startswith("n") for x in state["spec"]["structural"])

def test_manual_level_does_not_change_program_level(grid):
    spec=parse_instruction(CASES[0]["instruction"],grid,"override")["spec"]
    spec["level_override"]={"level":"L4","reason":"用户演示"}
    assert ModelSpec.model_validate(spec).level_override.level=="L4"
    assert derived_level(spec)=="L1"

def test_formula_code_counts_and_trial_identity(grid):
    state=parse_instruction(CASES[4]["instruction"],grid,"build")
    artifacts=generate_artifacts(state["spec"],grid)
    assert artifacts["size"]["switching_counts"]=={"all_in_service":16000,"meshed_in_service":3600,"meshed_kv_ge_110":1712}
    assert "# >>> [dp-1]" in artifacts["code"]
    assert "# >>> [s-1]" in artifacts["code"]
    assert next(x for x in artifacts["latex"] if x["id"]=="B1")["changed"]
    check=run_checks(state["spec"],grid,state,try_build=True,code=artifacts["code"])
    assert check["build"]["status"]=="pass"
    assert check["build"]["code_sha256"]==hashlib.sha256(artifacts["code"].encode()).hexdigest()
    trial=copy.deepcopy(state["spec"]);trial["omega"]["periods"]=1;trial["omega"]["n_minus_1"]=False
    estimated=estimate_size(trial,grid)
    for key in ("variables","binary","continuous","constraints"):
        assert check["build"][key]==estimated[key]

@pytest.mark.parametrize("mutation,code",[
    (lambda s:s["delta_pi"][0].update(param="NOT_ALLOWED"),"E102"),
    (lambda s:s["delta_pi"][0].update(value=None),"E103"),
    (lambda s:s["delta_pi"][0].update(value=4),"E104"),
    (lambda s:s["delta_pi"][0].update(periods=[17]),"E105"),
    (lambda s:s["delta_pi"][0].update(evidence="并不存在的原文"),"W501"),
    (lambda s:s["delta_pi"].clear(),"W502"),
])
def test_layers_reject_mutations(grid,mutation,code):
    spec=parse_instruction(CASES[0]["instruction"],grid,"mutate")["spec"]
    mutation(spec)
    assert code in {x["code"] for x in run_checks(spec,grid)["findings"]}

def test_conflict_invalid_operator_and_bound_inversion(grid):
    spec=parse_instruction(CASES[0]["instruction"],grid,"conflict")["spec"]
    second=copy.deepcopy(spec["delta_pi"][0]);second.update(id="dp-2",value=0.9)
    spec["delta_pi"].append(second)
    assert "E201" in {x["code"] for x in run_checks(spec,grid)["findings"]}
    spec=parse_instruction("母线43电压上限设为0.8。",grid,"bounds")["spec"]
    assert "E204" in {x["code"] for x in run_checks(spec,grid)["findings"]}
    spec=parse_instruction(CASES[4]["instruction"],grid,"operator")["spec"]
    spec["structural"][0]["params"]["k"]=0
    assert "E108" in {x["code"] for x in run_checks(spec,grid)["findings"]}

def test_custom_code_never_executed(grid,tmp_path):
    spec=parse_instruction(CASES[0]["instruction"],grid,"custom")["spec"]
    sentinel=tmp_path/"should_not_exist"
    spec["structural"].append({"id":"s-1","kind":"constraint","op_id":"custom","params":{},"source":"explicit","evidence":"母线 43","custom_code":f"open({str(sentinel)!r}, 'w').write('bad')"})
    report=run_checks(spec,grid,try_build=True)
    assert report["build"]["status"]=="skipped"
    assert "W107" in {x["code"] for x in report["findings"]}
    assert not sentinel.exists()

def test_bad_syntax_is_reported(grid):
    spec=parse_instruction(CASES[0]["instruction"],grid,"syntax")["spec"]
    report=run_checks(spec,grid,code="def missing(")
    assert "E401" in {x["code"] for x in report["findings"]}

def test_openai_unavailable_falls_back(grid,monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL","http://127.0.0.1:1/v1")
    result=parse_instruction(CASES[0]["instruction"],grid,"offline","openai")
    assert result["engine"]=="demo_fallback"
    assert result["level"]=="L1"
    assert any("回落" in x for x in result["warnings"])

def test_repeated_object_conflict_is_not_silently_dropped(grid):
    state=parse_instruction("母线43负荷提高10%；母线43负荷降低20%。",grid,"repeat")
    assert len(state["spec"]["delta_pi"])==2
    assert "E201" in {x["code"] for x in run_checks(state["spec"],grid,state)["findings"]}

def test_reverse_period_range_requires_clarification(grid):
    state=parse_instruction("第12–5时段，母线43负荷提高10%。",grid,"period")
    assert any(q["kind"]=="periods" for q in state["questions"])
    assert not run_checks(state["spec"],grid,state)["can_solve"]

def test_extra_real_object_still_requires_source_evidence(grid):
    spec=parse_instruction(CASES[0]["instruction"],grid,"hallucination")["spec"]
    extra=copy.deepcopy(spec["delta_pi"][0]);extra.update(id="dp-2",target="B44")
    assert "B44" in grid["bus"]
    spec["delta_pi"].append(extra)
    assert "W501" in {x["code"] for x in run_checks(spec,grid)["findings"]}

def test_parallel_selection_keeps_confirmation_questions_for_every_branch(grid):
    groups={}
    for lid,row in grid["branch"].items():
        pair=tuple(sorted((int(row["F_BUS"]),int(row["T_BUS"]))))
        groups.setdefault(pair,[]).append(lid)
    pair,candidates=next((p,ls) for p,ls in groups.items() if len(ls)>1)
    state=parse_instruction(f"高温，线路{pair[0]}–{pair[1]}热稳能力下降。",grid,"parallel")
    obj=next(q for q in state["questions"] if q["kind"]=="object")
    state=answer_parse(state,obj["id"],candidates,grid)
    assert {d["target"] for d in state["spec"]["delta_pi"]}==set(candidates)
    pending={q["target_id"] for q in state["questions"] if q["kind"]=="amplitude" and not q["answered"]}
    assert pending=={d["id"] for d in state["spec"]["delta_pi"]}

def test_two_period_build_uses_each_actual_frame(grid):
    import pyomo.environ as pyo
    first={k:v for k,v in grid.items() if k!="period_data"}
    second=copy.deepcopy(first)
    second["bus"]["B43"]["PD"]+=10.0
    second["gen"]["G237"]["PMAX"]+=7.0
    second["branch"]["L155"]["RATE_A"]=0.0
    second["branch"]["L376"]["BR_STATUS"]=0
    timeline=dict(first,period_data={1:first,"2":second})
    state=parse_instruction(CASES[0]["instruction"],grid,"two-period")
    spec=state["spec"];spec["omega"]["periods"]=2;spec["omega"]["n_minus_1"]=False
    artifacts=generate_artifacts(spec,timeline)
    scope={}
    exec(compile(artifacts["code"],"displayed_model.py","exec"),scope)
    model=scope["build_model"](timeline,spec)
    # D1 scales the actual PD of each frame: a 10 MW frame difference becomes 12 MW.
    difference=pyo.value(model.active_balance["B43",2].body)-pyo.value(model.active_balance["B43",1].body)
    # Pyomo canonicalizes a constant-only left-hand side by reversing the body.
    assert abs(difference)==pytest.approx(12.0)
    assert pyo.value(model.pg_upper["G237",2].upper)-pyo.value(model.pg_upper["G237",1].upper)==pytest.approx(7.0)
    assert ("L376",1) in model.z and ("L376",2) not in model.z
    assert pyo.value(model.status["L376",2])==0
    variables=list(model.component_data_objects(pyo.Var,active=True))
    constraints=list(model.component_data_objects(pyo.Constraint,active=True))
    assert len(variables)==artifacts["size"]["variables"]
    assert sum(v.is_binary() for v in variables)==artifacts["size"]["binary"]
    assert len(constraints)==artifacts["size"]["constraints"]
    assert artifacts["size"]["binary"]==449
    assert artifacts["size"]["constraints"]==22559*2-2
    assert grid["bus"]["B43"]["PD"]==first["bus"]["B43"]["PD"]


