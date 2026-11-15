from copy import deepcopy
import numpy as np
import pytest
from app.data.grid import load_grid
from app.metrics.evaluate import evaluate, snapshot_metrics, screen_n1, _lodf_data


def toy_grid(tap=1.0):
    buses = {f"B{i}":{"BUS_I":i,"BUS_TYPE":3 if i == 1 else 1,"PD":0.0,"VM":1.0,"VMIN":0.9,"VMAX":1.1,"BASE_KV":110.0} for i in range(1,4)}
    branch = {}
    for k, pair in enumerate([(1,2),(2,3),(1,3)],1):
        branch[f"L{k}"] = {"F_BUS":pair[0],"T_BUS":pair[1],"BR_X":1.0,"TAP":tap if k == 1 else 0.0,
                            "BR_STATUS":1,"RATE_A":100.0,"PF":0.0,"QF":0.0,"PT":0.0,"QT":0.0}
    return {"bus":buses,"branch":branch,"gen":{},"meta":{"sha256":"toy"}}


def test_dc_lodf_matches_direct_recalculation_with_transformer_tap():
    grid = toy_grid(tap=1.3)
    a = np.array([[1.,-1,0],[0,1,-1],[1,0,-1]])
    b = np.array([1/1.3,1,1])
    p = np.array([90.,-30.,-60.])
    theta = np.r_[0., np.linalg.solve((a.T @ np.diag(b) @ a)[1:,1:],p[1:])]
    flow = b * (a @ theta)
    factors = _lodf_data(grid,{})
    for c in range(3):
        post_b = b.copy()
        post_b[c] = 0
        post_theta = np.r_[0.,np.linalg.solve((a.T @ np.diag(post_b) @ a)[1:,1:],p[1:])]
        direct = post_b * (a @ post_theta)
        predicted = flow + factors["lodf"][:,c] * flow[c]
        assert predicted == pytest.approx(direct,abs=1e-8)


def test_n1_omits_outaged_line_and_unlimited_rating():
    grid = toy_grid()
    for key,value in zip(grid["branch"],[40,10,50]):
        grid["branch"][key]["PF"] = value
    n1 = screen_n1(grid)
    assert (n1["passed"],n1["evaluated"],n1["pass_rate"]) == (3,3,1)
    grid["branch"]["L1"]["RATE_A"] = 0
    assert screen_n1(grid)["pass_rate"] == 1
    grid["branch"]["L3"]["RATE_A"] = 45
    assert screen_n1(grid)["failed"] > 0


def test_real_baseline_metrics_and_mock_integrity():
    grid = load_grid()
    spec = {"task_id":"test","version":1,"omega":{"contingency_rule":"meshed_in_service"},"snapshot":{"single_snapshot":True}}
    result = evaluate(spec,{"status":"mock"},grid)
    assert result["after"] is None
    assert result["before"]["loading"]["count"] == 995
    assert result["before"]["voltage"]["violations"] == 233
    assert result["n1"]["evaluated"] == 225
    assert result["n1"]["islanding_count"] == 0
    assert "非交流" in result["n1"]["method"]
    assert result["risk"]["source"] == "本地规则模拟，课题 3 接口预留"
    for suggestion in result["suggestions"]:
        if "structural" in suggestion:
            params = suggestion["structural"]["params"]
            assert params.get("k", params.get("limit_mw")) is None
        if "delta" in suggestion:
            assert suggestion["delta"]["value"] is None


def test_islanding_domain_reported_separately():
    n1 = screen_n1(load_grid(),"all_in_service")
    assert n1["islanding_count"] == 775
    assert n1["evaluated"] == 225
    island = next(item for item in n1["islanding"] if item["contingency"] == "L442")
    assert island["islands"][0]["buses"] == ["B774"]


def test_incomplete_results_never_filled_from_snapshot():
    grid = toy_grid()
    partial = {"status":"feasible","periods":1,"branch_status":{},"bus_vm":{"B1":[1.0]},"branch_flow":{}}
    result = evaluate({"omega":{}},partial,grid)
    assert result["after"]["loading"] is None
    assert result["after"]["voltage"] is None
    assert result["after"]["availability"]["missing_bus_vm_count"] == 2
    assert result["after"]["n1"]["status"] == "unavailable"


def test_post_result_limits_and_topology_recomputed_without_mutation():
    grid = toy_grid()
    before = deepcopy(grid)
    flows = {key:{"pf":[20.],"qf":[0.],"pt":[-20.],"qt":[0.]} for key in grid["branch"]}
    result = {"status":"feasible","periods":1,"branch_status":{"L1":[0]},"bus_vm":{key:[1.01] for key in grid["bus"]},"branch_flow":flows}
    spec = {"omega":{},"delta_pi":[{"target":"L3","param":"RATE_A","operation":"scale","value":0.5,"periods":[],"source":"explicit"}]}
    report = evaluate(spec,result,grid)
    assert report["after"]["loading"]["max"] == pytest.approx(.4)
    assert report["after"]["n1"]["evaluated"] == 0
    assert grid == before

def test_later_period_risk_is_not_hidden_by_first_period():
    grid = toy_grid()
    result = {"status":"feasible","periods":2,"branch_status":{},
              "bus_vm":{key:[1.,1.] for key in grid["bus"]},
              "branch_flow":{key:{"pf":[20.,200.],"qf":[0.,0.],"pt":[-20.,-200.],"qt":[0.,0.]} for key in grid["branch"]}}
    report = evaluate({"omega":{}},result,grid)
    assert report["after"]["loading"]["max"] == pytest.approx(.2)
    assert report["risk"]["risk_level"] == "高"
    assert "第 2 时段" in report["risk"]["basis"]
    assert len(report["risk"]["period_risks"]) == 2

def test_multisnapshot_limits_and_voltage_bounds_are_period_specific():
    first = toy_grid()
    second = deepcopy(first)
    for row in second["branch"].values():
        row["RATE_A"] = 50
    for row in second["bus"].values():
        row["VMAX"] = .99
    grid = dict(first,period_data={1:first,2:second})
    result = {"status":"feasible","periods":2,"branch_status":{},
              "bus_vm":{key:[1.,1.] for key in first["bus"]},
              "branch_flow":{key:{"pf":[20.,20.],"qf":[0.,0.],"pt":[-20.,-20.],"qt":[0.,0.]} for key in first["branch"]}}
    report = evaluate({"omega":{}},result,grid)
    assert report["before"]["loading"]["max"] == 0
    assert report["before"]["voltage"]["violations"] == 0
    assert report["after"]["periods"][0]["loading"]["max"] == pytest.approx(.2)
    assert report["after"]["periods"][1]["loading"]["max"] == pytest.approx(.4)
    assert report["after"]["periods"][1]["voltage"]["violations"] == 3
    # JSON round trips turn the period keys into strings.
    grid["period_data"] = {"1":first,"2":second}
    assert evaluate({"omega":{}},result,grid)["after"]["periods"][1]["loading"]["max"] == pytest.approx(.4)


def test_missing_period_frame_is_an_explicit_error():
    first = toy_grid()
    grid = dict(first,period_data={1:first})
    result = {"status":"feasible","periods":2,"branch_status":{},"bus_vm":{},"branch_flow":{}}
    with pytest.raises(ValueError,match="第 2 时段"):
        evaluate({"omega":{}},result,grid)


