"""Read-only Dali snapshot ingestion and stable topology identifiers."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[3]
COLUMNS = {
    "Bus": "BUS_I BUS_TYPE PD QD GS BS BUS_AREA VM VA BASE_KV ZONE VMAX VMIN RENEWABLE".split(),
    "Gen": "GEN_BUS PG QG QMAX QMIN VG MBASE GEN_STATUS PMAX PMIN".split(),
    "Branch": "F_BUS T_BUS BR_R BR_X BR_B RATE_A RATE_B RATE_C TAP SHIFT BR_STATUS ANGMIN ANGMAX PF QF PT QT".split(),
}
INT_FIELDS = {"BUS_I", "BUS_TYPE", "BUS_AREA", "ZONE", "GEN_BUS", "GEN_STATUS", "F_BUS", "T_BUS", "BR_STATUS"}


def snapshot_dir() -> Path:
    path = Path(os.getenv("SNAPSHOT_DIR", "data/snapshots"))
    return path if path.is_absolute() else ROOT / path


def _time(value: str | None):
    if not value:
        return None
    for fmt in ("%Y-%m-%d_%H-%M-%S", "%Y-%m-%d_%H-%M", "%Y-%m-%d %H:%M:%S", "%Y%m%d_%H%M%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _filename_time(name: str):
    match = re.search(r"pasExp_(\d{8}_\d{6})_", name)
    return _time(match[1]) if match else None


def parse_snapshot(path: Path | str) -> dict:
    """Parse the documented section format; never silently repair input values."""
    path = Path(path)
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    info = {}
    matrices = {}
    for match in re.finditer(r"#####Begin Dict Info#####(.*?)#####End Dict Info#####", text, re.S):
        for line in match[1].splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                info[key.strip()] = value.strip()
    for name, required in COLUMNS.items():
        match = re.search(rf"#####Begin Matrix {name}#####(.*?)#####End Matrix {name}#####", text, re.S)
        if not match:
            raise ValueError(f"E301：缺少 {name} 数据段")
        rows = list(csv.reader(line for line in match[1].strip().splitlines() if line.strip()))
        headers = [h.strip() for h in rows[0]] if rows else []
        if any(col not in headers for col in required):
            raise ValueError(f"E301：{name} 缺少必需列")
        parsed = []
        for row_number, row in enumerate(rows[1:], 1):
            if len(row) != len(headers):
                raise ValueError(f"E301：{name} 第 {row_number} 行列数不一致")
            values = {}
            for key, value in zip(headers, row):
                try:
                    number = float(value.strip())
                except ValueError as exc:
                    raise ValueError(f"E301：{name} 第 {row_number} 行 {key} 非数值") from exc
                if not math.isfinite(number):
                    raise ValueError(f"E301：{name} 第 {row_number} 行 {key} 缺失或非有限值")
                if key in INT_FIELDS and number != int(number):
                    raise ValueError(f"E301：{name} 第 {row_number} 行 {key} 必须为整数")
                values[key] = int(number) if key in INT_FIELDS else number
            parsed.append(values)
        matrices[name] = parsed
    authoritative = _time(info.get("time"))
    filename_time = _filename_time(path.name)
    when = authoritative or filename_time
    if when is None:
        raise ValueError("E301：断面缺少可解析时间，文件名也不含时间")
    try:
        base = float(info["baseMVA"])
    except (ValueError, KeyError) as exc:
        raise ValueError("E301：缺少有效 baseMVA") from exc
    if not math.isfinite(base) or base <= 0:
        raise ValueError("E301：baseMVA 必须为正")
    bus = {f"B{row['BUS_I']}": row for row in matrices["Bus"]}
    if len(bus) != len(matrices["Bus"]):
        raise ValueError("E301：母线 BUS_I 存在重复")
    gen = {f"G{i}": row for i, row in enumerate(matrices["Gen"], 1)}
    branch = {f"L{i}": row for i, row in enumerate(matrices["Branch"], 1)}
    for key, row in gen.items():
        if f"B{row['GEN_BUS']}" not in bus:
            raise ValueError(f"E301：{key} 关联母线不存在")
    for key, row in branch.items():
        if f"B{row['F_BUS']}" not in bus or f"B{row['T_BUS']}" not in bus:
            raise ValueError(f"E301：{key} 首末母线不存在")
    return {
        "bus": bus, "gen": gen, "branch": branch,
        "meta": {
            "id": path.name, "filename": path.name, "time": when.isoformat(),
            "raw_time": info.get("time"), "filename_time": filename_time.isoformat() if filename_time else None,
            "time_mismatch": bool(authoritative and filename_time and authoritative != filename_time),
            "time_source": "Dict Info.time" if authoritative else "文件名",
            "baseMVA": base, "sha256": hashlib.sha256(raw).hexdigest(),
            "power_unit": "MW / Mvar / MVA", "voltage_unit": "p.u.", "angle_unit": "rad",
            "info": info,
        },
    }


def graph_for(grid: dict, status: dict | None = None) -> nx.MultiGraph:
    graph = nx.MultiGraph()
    graph.add_nodes_from(grid["bus"])
    for key, row in grid["branch"].items():
        active = (status or {}).get(key, row["BR_STATUS"])
        if active:
            graph.add_edge(f"B{row['F_BUS']}", f"B{row['T_BUS']}", key=key, id=key)
    return graph


def bridge_ids(graph: nx.MultiGraph) -> list[str]:
    simple = nx.Graph(graph)
    bridges = []
    for a, b in nx.bridges(simple):
        if graph.number_of_edges(a, b) == 1:
            bridges.extend(graph[a][b])
    return sorted(bridges, key=lambda x: int(x[1:]))


def branch_kv(grid: dict, row: dict) -> float:
    return max(grid["bus"][f"B{row['F_BUS']}"]["BASE_KV"], grid["bus"][f"B{row['T_BUS']}"]["BASE_KV"])


def island_details(grid: dict, opened: list[str], status: dict | None = None) -> list[dict]:
    states = dict(status or {})
    states.update({key: 0 for key in opened})
    graph = graph_for(grid, states)
    components = sorted(nx.connected_components(graph), key=lambda c: (-len(c), sorted(c)))
    if len(components) <= 1:
        return []
    slack = next((key for key, row in grid["bus"].items() if row["BUS_TYPE"] == 3), None)
    result = []
    for component in components:
        if slack in component or (slack is None and component is components[0]):
            continue
        pd = sum(grid["bus"][key]["PD"] for key in component)
        pg = sum(row["PG"] for row in grid["gen"].values() if f"B{row['GEN_BUS']}" in component and row["GEN_STATUS"])
        result.append({
            "buses": sorted(component, key=lambda x: int(x[1:])), "bus_count": len(component),
            "load_mw": pd, "generation_mw": pg, "net_load_mw": pd - pg,
            "net_injection_mw": pg - pd,
        })
    return result


def _layout(grid: dict, graph: nx.MultiGraph) -> dict:
    cache_dir = ROOT / "data/cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "layout.json"
    signature = grid["meta"]["sha256"]
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("sha256") == signature and set(cached.get("positions", {})) == set(grid["bus"]):
            return cached["positions"]
    except (OSError, ValueError):
        pass
    coordinates = nx.spring_layout(nx.Graph(graph), seed=42, iterations=70, scale=1000)
    positions = {key: [round(float(point[0]), 4), round(float(point[1]), 4)] for key, point in coordinates.items()}
    tmp = cache_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"sha256": signature, "seed": 42, "positions": positions}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache_path)
    return positions


def _enrich(grid: dict, with_layout=True) -> dict:
    graph = graph_for(grid)
    bridges = bridge_ids(graph)
    bridge_set = set(bridges)
    active = [key for key, row in grid["branch"].items() if row["BR_STATUS"]]
    meshed = [key for key in active if key not in bridge_set]
    by_kv = Counter(branch_kv(grid, grid["branch"][key]) for key in meshed)
    pairs = defaultdict(list)
    for key, row in grid["branch"].items():
        pairs[tuple(sorted((row["F_BUS"], row["T_BUS"])))].append(key)
    gen_buses = Counter(row["GEN_BUS"] for row in grid["gen"].values())
    high = [key for key, row in grid["bus"].items() if row["VM"] > row["VMAX"]]
    low = [key for key, row in grid["bus"].items() if row["VM"] < row["VMIN"]]
    pg_bad = [key for key, row in grid["gen"].items() if row["PG"] < row["PMIN"] or row["PG"] > row["PMAX"]]
    unlimited = [key for key, row in grid["branch"].items() if row["RATE_A"] == 0]
    negative_load = [key for key, row in grid["bus"].items() if row["PD"] < 0]
    quality = [
        {"code": "BASE_VM_HIGH", "severity": "warning", "message": "基态电压高于 VMAX；VMAX 可能为默认值，未修改原值", "count": len(high), "targets": high},
        {"code": "BASE_VM_LOW", "severity": "warning", "message": "基态电压低于 VMIN", "count": len(low), "targets": low},
        {"code": "BASE_PG_RANGE", "severity": "warning", "message": "基态机组出力超出 PMIN/PMAX", "count": len(pg_bad), "targets": pg_bad},
        {"code": "ZERO_RATE_A", "severity": "warning", "message": "RATE_A 为 0，按无限额处理，不计入负载率", "count": len(unlimited), "targets": unlimited},
        {"code": "NEGATIVE_PD", "severity": "info", "message": "PD 为负，按负负荷保留，不静默改为零", "count": len(negative_load), "targets": negative_load},
        {"code": "NO_RENEWABLE", "severity": "info", "message": "没有新能源机组信息，新能源消纳率暂不计算", "count": sum(row["RENEWABLE"] != 0 for row in grid["bus"].values()), "targets": []},
        {"code": "NO_COST", "severity": "warning", "message": "断面没有发电成本数据，基础目标采用偏离当前方式最小，待确认", "count": 1, "targets": []},
        {"code": "EQUIVALENT_GENS", "severity": "info", "message": "B176 上存在约 ±2000MW 等值机组，保留原值", "count": gen_buses.get(176, 0), "targets": [key for key, row in grid["gen"].items() if row["GEN_BUS"] == 176]},
    ]
    if grid["meta"]["time_mismatch"]:
        quality.append({"code": "SNAPSHOT_TIME_MISMATCH", "severity": "warning", "message": f"Dict Info 时间 {grid['meta']['time']} 与文件名 {grid['meta']['filename_time']} 不一致，采用 Dict Info", "count": 1, "targets": []})
    all_files = list(snapshot_dir().glob("*.txt"))
    single = len(all_files) == 1
    if single:
        quality.append({"code": "SINGLE_SNAPSHOT", "severity": "warning", "message": "单断面演示模式：同一合成演示断面复制为 16 时段，不代表预测数据", "count": 16, "targets": []})
    summary = {
        "id": grid["meta"]["id"], "filename": grid["meta"]["filename"], "time": grid["meta"]["time"],
        "time_source": grid["meta"]["time_source"], "filename_time": grid["meta"]["filename_time"],
        "sha256": grid["meta"]["sha256"], "counts": {key: len(grid[key]) for key in ("bus", "gen", "branch")},
        "bus_types": {"PQ": sum(row["BUS_TYPE"] == 1 for row in grid["bus"].values()), "PV": sum(row["BUS_TYPE"] == 2 for row in grid["bus"].values()), "REF": sum(row["BUS_TYPE"] == 3 for row in grid["bus"].values())},
        "reference_buses": [key for key, row in grid["bus"].items() if row["BUS_TYPE"] == 3],
        "voltage_levels": [{"kv": kv, "count": count} for kv, count in sorted(Counter(row["BASE_KV"] for row in grid["bus"].values()).items(), reverse=True)],
        "totals": {"pd_mw": sum(row["PD"] for row in grid["bus"].values()), "pg_mw": sum(row["PG"] for row in grid["gen"].values()), "qd_mvar": sum(row["QD"] for row in grid["bus"].values())},
        "topology": {"connected": nx.is_connected(graph), "components": nx.number_connected_components(graph), "bridges": len(bridges), "meshed": len(meshed), "meshed_kv_ge_110": sum(branch_kv(grid, grid["branch"][key]) >= 110 for key in meshed), "meshed_by_kv": {str(int(kv)): count for kv, count in by_kv.items()}, "parallel_groups": sum(len(lines) > 1 for lines in pairs.values()), "parallel_lines": sum(len(lines) for lines in pairs.values() if len(lines) > 1)},
        "multi_gen_buses": sum(count > 1 for count in gen_buses.values()),
        "in_service": {"branch": len(active), "gen": sum(row["GEN_STATUS"] == 1 for row in grid["gen"].values())},
        "quality": quality, "single_snapshot": single, "baseMVA": grid["meta"]["baseMVA"],
        "units": {"power": "MW / Mvar / MVA", "vm": "p.u.", "va": "rad", "impedance": "p.u.（baseMVA=100）"},
    }
    positions = _layout(grid, graph) if with_layout else {key: [0, 0] for key in grid["bus"]}
    nodes = [{"id": key, "name": key, "kv": row["BASE_KV"], "x": positions[key][0], "y": positions[key][1], **row} for key, row in grid["bus"].items()]
    links = []
    for key, row in grid["branch"].items():
        kv = branch_kv(grid, row)
        loading = max(math.hypot(row["PF"], row["QF"]), math.hypot(row["PT"], row["QT"])) / row["RATE_A"] if row["RATE_A"] > 0 else None
        source, target = f"B{row['F_BUS']}", f"B{row['T_BUS']}"
        links.append({"id": key, "name": key, "source": source, "target": target, "kv": kv, "is_bridge": key in bridge_set, "is_transformer": grid["bus"][source]["BASE_KV"] != grid["bus"][target]["BASE_KV"] or row["TAP"] != 0, "loading": loading, "rho": loading, "label": f"{key}（{row['F_BUS']}–{row['T_BUS']}，{kv:g}kV）", **row})
    grid.update({"bridges": bridges, "meshed": meshed, "summary": summary, "topology": {"nodes": nodes, "links": links}})
    return grid


@lru_cache(maxsize=8)
def _load_cached(path: str, stamp: int, size: int) -> dict:
    return _enrich(parse_snapshot(path))


def get_snapshots() -> list[dict]:
    snapshots = []
    for path in snapshot_dir().glob("*.txt"):
        grid = parse_snapshot(path)
        meta = dict(grid["meta"])
        meta.update({"buses": len(grid["bus"]), "gens": len(grid["gen"]), "branches": len(grid["branch"])})
        snapshots.append(meta)
    snapshots.sort(key=lambda item: (item["time"], item["id"]))
    for item in snapshots:
        item["single_snapshot"] = len(snapshots) == 1
    return snapshots


def load_grid(snapshot_id: str | None = None) -> dict:
    snapshots = get_snapshots()
    if not snapshots:
        raise ValueError("断面库为空")
    chosen = next((item for item in snapshots if item["id"] == snapshot_id), None) if snapshot_id else snapshots[0]
    if chosen is None:
        raise ValueError("断面编号不存在")
    path = snapshot_dir() / chosen["filename"]
    stat = path.stat()
    return _load_cached(str(path), stat.st_mtime_ns, stat.st_size)


def snapshot_ref(snapshot_id: str | None = None, periods: int = 16, step_min: int = 15) -> dict:
    if periods < 1 or periods > 16 or step_min < 1:
        raise ValueError("时段数须在 1–16，时间步长须为正")
    snapshots = get_snapshots()
    chosen = next((item for item in snapshots if item["id"] == snapshot_id), None) if snapshot_id else (snapshots[0] if snapshots else None)
    if chosen is None:
        raise ValueError("断面编号不存在")
    start = datetime.fromisoformat(chosen["time"])
    slots = []
    for index in range(periods):
        desired = start + timedelta(minutes=index * step_min)
        nearest = min(snapshots, key=lambda item: (abs((datetime.fromisoformat(item["time"]) - desired).total_seconds()), item["time"]))
        slots.append({"period": index + 1, "time": desired.isoformat(), "snapshot_time": nearest["time"], "file": nearest["filename"], "offset_seconds": (datetime.fromisoformat(nearest["time"]) - desired).total_seconds()})
    return {"id": chosen["id"], "files": [slot["file"] for slot in slots], "start": start.isoformat(), "periods": periods, "step_min": step_min, "single_snapshot": len(snapshots) == 1, "slots": slots, "source": "node_txt", "system": "dali_905"}

def load_task_grid(reference: dict) -> dict:
    """Bind the exact file of each task period without mutating cached snapshots.

    period_data uses one-based integer keys. Each frame is a normal snapshot dict
    without period_data, so even repeated files never create circular references.
    """
    if hasattr(reference, "model_dump"):
        reference = reference.model_dump()
    reference = dict(reference or {})
    files = list(reference.get("files") or [])
    periods = int(reference.get("periods", len(files) or 16))
    if periods < 1 or periods > 16:
        raise ValueError("E301：任务时段数必须在 1–16")
    base_id = reference.get("id") or (files[0] if files else None)
    base = load_grid(base_id)
    base_id = base["meta"]["id"]
    warnings = []
    if not files:
        files = [base_id] * periods
        warnings.append("任务没有逐时段文件引用，明确采用起始断面复制")
    if len(files) == 1 and periods > 1 and reference.get("single_snapshot") is True:
        files *= periods
    if len(files) != periods:
        raise ValueError(f"E301：任务要求 {periods} 个时段，但文件引用有 {len(files)} 项")
    if any(not isinstance(item, str) or not item for item in files):
        raise ValueError("E301：每个时段必须引用明确的断面文件编号")
    if files[0] != base_id:
        raise ValueError("E301：起始断面编号与第1时段文件引用不一致")
    if reference.get("single_snapshot") is True and len(set(files)) > 1:
        raise ValueError("E301：单断面标记与多个实际断面引用冲突")
    frames_by_file = {}
    for file_id in dict.fromkeys(files):
        loaded = base if file_id == base_id else load_grid(file_id)
        frame = {key: value for key, value in loaded.items() if key != "period_data"}
        if abs(frame["meta"]["baseMVA"] - base["meta"]["baseMVA"]) > 1e-12:
            raise ValueError(f"E301：{file_id} 的 baseMVA 与起始断面不一致")
        for component in ("bus", "gen", "branch"):
            if set(frame[component]) != set(base[component]):
                raise ValueError(f"E301：{file_id} 的 {component} 对象编号集合与起始断面不一致")
            for target, row in frame[component].items():
                prior = base[component][target]
                if set(row) != set(prior):
                    raise ValueError(f"E301：{file_id} 的 {target} 数据列与起始断面不一致")
                identity_fields = {"bus": ("BUS_I", "BASE_KV"), "gen": ("GEN_BUS",),
                                   "branch": ("F_BUS", "T_BUS")}[component]
                if any(row[field] != prior[field] for field in identity_fields):
                    raise ValueError(f"E301：{file_id} 的 {target} 对象关联或电压基准发生变化，不能按行号静默匹配")
        frames_by_file[file_id] = frame
    if len(frames_by_file) > 1:
        warnings.append("跨断面使用 G/L 原始行号作为设备身份；同母线多机组、同端点并联回路的原始行序须由数据来源保持一致")
    result = dict(base)
    result["period_data"] = {index: frames_by_file[file_id] for index, file_id in enumerate(files, 1)}
    result["period_files"] = {index: file_id for index, file_id in enumerate(files, 1)}
    result["data_consistency"] = {"status": "checked", "periods": periods,
                                "unique_snapshots": len(frames_by_file),
                                "identity_checks": ["object_ids", "column_names", "BUS_I", "BASE_KV", "GEN_BUS", "F_BUS", "T_BUS", "baseMVA"],
                                "warnings": warnings}
    return result

