import hashlib
from pathlib import Path
import pytest
from app.data.grid import ROOT, load_grid, snapshot_ref, island_details, parse_snapshot


@pytest.fixture(scope="module")
def grid():
    return load_grid()


def test_source_unchanged(grid):
    assert grid["meta"]["sha256"] == "85f724d78dfed663da29127f0da1b1cab71ace41e72c6e6cf6acea697d08509c"


def test_data_facts(grid):
    s = grid["summary"]
    assert s["counts"] == {"bus": 905, "gen": 237, "branch": 1000}
    assert s["bus_types"] == {"PQ": 834, "PV": 70, "REF": 1}
    assert s["reference_buses"] == ["B175"]
    assert {x["kv"]: x["count"] for x in s["voltage_levels"]} == {500:17,220:60,110:262,35:354,10:200,6:12}
    assert s["in_service"] == {"branch":1000,"gen":237}
    assert s["multi_gen_buses"] == 61


def test_topology_and_parallel_circuits(grid):
    t = grid["summary"]["topology"]
    assert t["connected"]
    assert (t["bridges"],t["meshed"],t["meshed_kv_ge_110"]) == (775,225,107)
    assert t["meshed_by_kv"] == {"35":118,"220":63,"110":33,"500":11}
    assert (t["parallel_groups"],t["parallel_lines"]) == (67,136)
    assert len(grid["topology"]["nodes"]) == 905
    assert len(grid["topology"]["links"]) == 1000
    assert "L376" in grid["meshed"]
    assert "L442" in grid["bridges"]


def test_quality_and_units(grid):
    q = {x["code"]:x["count"] for x in grid["summary"]["quality"]}
    assert {k:q[k] for k in ["BASE_VM_HIGH","BASE_VM_LOW","BASE_PG_RANGE","ZERO_RATE_A","NEGATIVE_PD"]} == {"BASE_VM_HIGH":231,"BASE_VM_LOW":2,"BASE_PG_RANGE":101,"ZERO_RATE_A":5,"NEGATIVE_PD":248}
    assert grid["meta"]["baseMVA"] == 100
    assert grid["meta"]["angle_unit"] == "rad"
    assert grid["meta"]["time_mismatch"]
    assert grid["meta"]["time"] == "2026-10-15T18:23:06"
    assert abs(grid["summary"]["totals"]["pd_mw"] - 1014) < 1
    assert abs(grid["summary"]["totals"]["pg_mw"] - 1693) < 1


def test_single_snapshot_slots_and_stable_ids(grid):
    ref = snapshot_ref()
    assert ref["single_snapshot"] and len(ref["files"]) == 16
    assert len(set(ref["files"])) == 1
    assert ref["slots"][1]["time"] == "2026-10-15T18:38:06"
    assert grid["gen"]["G237"]["GEN_BUS"] == 314
    assert grid["branch"]["L155"]["F_BUS"] == 174
    assert grid["branch"]["L155"]["T_BUS"] == 172
    assert "B99999" not in grid["bus"]


def test_d2_and_d10_islands(grid):
    assert island_details(grid, ["L376"]) == []
    islands = island_details(grid, ["L442"])
    assert len(islands) == 1 and islands[0]["buses"] == ["B774"]
    assert islands[0]["net_injection_mw"] == pytest.approx(135.5, abs=0.2)


def test_malformed_snapshot_rejected(tmp_path):
    bad = tmp_path / "pasExp_20261004_153500_1.txt"
    bad.write_text("#####Begin Dict Info#####\nbaseMVA=100\n#####End Dict Info#####", encoding="utf-8")
    with pytest.raises(ValueError, match="E301"):
        parse_snapshot(bad)

@pytest.fixture
def two_snapshot_library(tmp_path, monkeypatch):
    from app.data import grid as module
    monkeypatch.setattr(module, "ROOT", tmp_path)
    folder = tmp_path / "snapshots"
    folder.mkdir()
    monkeypatch.setenv("SNAPSHOT_DIR", str(folder))
    names = ["pasExp_20260101_000000_1.txt", "pasExp_20260101_001500_1.txt"]
    for index, name in enumerate(names):
        rows = {
            "Bus": [
                [1,3,0,0,0,0,1,1,0,110,1,1.05,.9,0],
                [2,1,20+index*15,4,0,0,1,1,0,110,1,1.05,.9,0],
            ],
            "Gen": [[1,20+index*15,4,100,-100,1,100,1,100,0]],
            "Branch": [[1,2,.01,.1,0,100,100,100,0,0,1,-360,360,20+index*15,4,-20-index*15,-4]],
        }
        parts = ["#####Begin Dict Info#####", f"time = 2026-01-01_00-{index*15:02d}-00", "baseMVA = 100", "#####End Dict Info#####"]
        for kind, values in rows.items():
            parts += [f"#####Begin Matrix {kind}#####", ",".join(module.COLUMNS[kind])]
            parts += [",".join(map(str,row)) for row in values]
            parts += [f"#####End Matrix {kind}#####"]
        (folder/name).write_text("\n".join(parts),encoding="utf-8")
    return module, names


def test_task_grid_assembles_actual_period_files(two_snapshot_library):
    import json
    module, names = two_snapshot_library
    ref = module.snapshot_ref(names[0],periods=2)
    assert ref["files"] == names
    task_grid = module.load_task_grid(ref)
    assert task_grid["bus"]["B2"]["PD"] == 20
    assert task_grid["period_data"][1]["bus"]["B2"]["PD"] == 20
    assert task_grid["period_data"][2]["bus"]["B2"]["PD"] == 35
    assert task_grid["period_data"][2]["gen"]["G1"]["PG"] == 35
    assert task_grid["period_files"] == {1:names[0],2:names[1]}
    assert task_grid["data_consistency"]["unique_snapshots"] == 2
    assert "period_data" not in module.load_grid(names[0])
    assert all("period_data" not in frame for frame in task_grid["period_data"].values())
    json.dumps(task_grid,allow_nan=False)  # no circular references


def test_task_grid_single_file_frames_share_readonly_cache(two_snapshot_library):
    module, names = two_snapshot_library
    ref = {"id":names[0],"files":[names[0]]*16,"periods":16,"single_snapshot":True}
    task_grid = module.load_task_grid(ref)
    assert task_grid["period_data"][1] is task_grid["period_data"][16]
    assert task_grid is not task_grid["period_data"][1]
    assert "period_data" not in task_grid["period_data"][1]


@pytest.mark.parametrize("issue",["gen_mapping","branch_mapping","object_ids","columns","base_mva"])
def test_task_grid_rejects_cross_snapshot_identity_mismatch(two_snapshot_library,monkeypatch,issue):
    from copy import deepcopy
    module, names = two_snapshot_library
    first, second = module.load_grid(names[0]), deepcopy(module.load_grid(names[1]))
    if issue == "gen_mapping":
        second["gen"]["G1"]["GEN_BUS"] = 2
    elif issue == "branch_mapping":
        second["branch"]["L1"]["F_BUS"], second["branch"]["L1"]["T_BUS"] = 2,1
    elif issue == "object_ids":
        second["bus"]["B999"] = second["bus"].pop("B2")
    elif issue == "columns":
        second["branch"]["L1"].pop("PF")
    elif issue == "base_mva":
        second["meta"]["baseMVA"] = 200
    monkeypatch.setattr(module,"load_grid",lambda key=None: second if key == names[1] else first)
    with pytest.raises(ValueError,match="E301"):
        module.load_task_grid({"id":names[0],"files":names,"periods":2,"single_snapshot":False})


def test_task_grid_rejects_incomplete_file_mapping(two_snapshot_library):
    module,names = two_snapshot_library
    with pytest.raises(ValueError,match="E301"):
        module.load_task_grid({"id":names[0],"files":names,"periods":3,"single_snapshot":False})

