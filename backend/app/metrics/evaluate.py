"""Metrics over observed data; DC screening is never AC N-1 certification."""
from __future__ import annotations

import hashlib
import json
import math
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import httpx
import networkx as nx
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import splu
import yaml

from app.data.grid import ROOT, branch_kv, bridge_ids, graph_for, island_details

_LODF_CACHE: dict[str, dict] = {}


def _thresholds() -> tuple[float, float]:
    try:
        config = yaml.safe_load((ROOT / "config/app.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        config = {}
    thresholds = config.get("thresholds", {})
    return (float(thresholds.get("heavy", config.get("heavy_threshold", 0.8))),
            float(thresholds.get("overload", config.get("overload_threshold", 1.0))))


def _modified(base: float, param: str, target: str, period: int, spec: dict | None) -> float:
    value = base
    for delta in (spec or {}).get("delta_pi", []):
        if delta.get("target") != target or delta.get("param") != param:
            continue
        periods = delta.get("periods") or []
        if periods and period not in periods:
            continue
        number = delta.get("value")
        if number is None or (delta.get("source") == "inferred" and not delta.get("confirmed")):
            continue
        if delta.get("operation") == "scale":
            value *= float(number)
        elif delta.get("operation") == "set":
            value = float(number)
        elif delta.get("operation") == "add":
            value += float(number)
    return value


def snapshot_metrics(grid: dict, *, flows: dict | None = None, vm: dict | None = None,
                     status: dict | None = None, spec: dict | None = None,
                     period: int = 1) -> dict:
    """Only calculate all-network metrics when all required observations exist."""
    heavy, overload = _thresholds()
    loading_items = []
    missing_flows = []
    for key, row in grid["branch"].items():
        active = (status or {}).get(key, row["BR_STATUS"])
        rate = _modified(row["RATE_A"], "RATE_A", key, period, spec)
        if rate <= 0 or not active:
            continue
        observed = row if flows is None else flows.get(key)
        if observed is None:
            missing_flows.append(key)
            continue
        values = {name: observed.get(name, observed.get(name.lower())) for name in ("PF", "QF", "PT", "QT")}
        if any(value is None or not math.isfinite(float(value)) for value in values.values()):
            missing_flows.append(key)
            continue
        apparent = max(math.hypot(values["PF"], values["QF"]), math.hypot(values["PT"], values["QT"]))
        loading_items.append({"id": key, "ratio": apparent / rate, "mva": apparent, "rate_a": rate, "kv": branch_kv(grid, row)})
    loading_items.sort(key=lambda item: item["ratio"], reverse=True)
    voltage_items = []
    missing_vm = []
    for key, row in grid["bus"].items():
        value = row["VM"] if vm is None else vm.get(key)
        if value is None or not math.isfinite(float(value)):
            missing_vm.append(key)
            continue
        low = _modified(row["VMIN"], "VMIN", key, period, spec)
        high = _modified(row["VMAX"], "VMAX", key, period, spec)
        voltage_items.append({"id": key, "vm": value, "deviation": abs(value - 1), "vmin": low, "vmax": high, "violation": value < low or value > high, "kv": row["BASE_KV"]})
    voltage_items.sort(key=lambda item: item["deviation"], reverse=True)
    loading = None if missing_flows else {
        "max": max((item["ratio"] for item in loading_items), default=0.0),
        "mean": float(np.mean([item["ratio"] for item in loading_items])) if loading_items else 0.0,
        "heavy_count": sum(item["ratio"] >= heavy for item in loading_items),
        "overload_count": sum(item["ratio"] >= overload for item in loading_items),
        "count": len(loading_items), "heavy_threshold": heavy, "overload_threshold": overload,
        "items": loading_items,
    }
    voltage = None if missing_vm else {
        "max": max((item["deviation"] for item in voltage_items), default=0.0),
        "mean": float(np.mean([item["deviation"] for item in voltage_items])) if voltage_items else 0.0,
        "violations": sum(item["violation"] for item in voltage_items),
        "high_count": sum(item["vm"] > item["vmax"] for item in voltage_items),
        "low_count": sum(item["vm"] < item["vmin"] for item in voltage_items),
        "count": len(voltage_items), "items": voltage_items,
    }
    return {"loading": loading, "voltage": voltage,
            "availability": {"loading": not missing_flows, "voltage": not missing_vm,
                             "missing_branch_flow_count": len(missing_flows), "missing_bus_vm_count": len(missing_vm),
                             "reason": "缺少完整结果，未用基态补造调整后指标" if missing_flows or missing_vm else None}}


def _lodf_data(grid: dict, states: dict) -> dict:
    line_ids = [key for key, row in grid["branch"].items() if states.get(key, row["BR_STATUS"])]
    signature = hashlib.sha256(json.dumps({
        "buses": list(grid["bus"]), "lines": [
            [key, grid["branch"][key]["F_BUS"], grid["branch"][key]["T_BUS"],
             grid["branch"][key]["BR_X"], grid["branch"][key]["TAP"]]
            for key in line_ids]}, separators=(",", ":")).encode()).hexdigest()
    if signature in _LODF_CACHE:
        return _LODF_CACHE[signature]
    graph = graph_for(grid, states)
    if not nx.is_connected(graph):
        raise ValueError("当前拓扑已成岛，不能对整体网络应用单平衡节点 LODF")
    bridges = set(bridge_ids(graph))
    meshed = [key for key in line_ids if key not in bridges]
    buses = list(grid["bus"])
    indexes = {key: index for index, key in enumerate(buses)}
    ref = next((index for index, key in enumerate(buses) if grid["bus"][key].get("BUS_TYPE") == 3), 0)
    rows, cols, entries, susceptance = [], [], [], []
    for index, key in enumerate(line_ids):
        row = grid["branch"][key]
        x = row["BR_X"]
        tap = row["TAP"] or 1.0
        if abs(x) < 1e-12 or tap <= 0:
            raise ValueError(f"{key} 电抗为零或变比非法，直流筛查未执行")
        rows.extend((index, index))
        cols.extend((indexes[f"B{row['F_BUS']}"], indexes[f"B{row['T_BUS']}"]))
        entries.extend((1.0, -1.0))
        susceptance.append(1.0 / (x * tap))
    incidence = sparse.csr_matrix((entries, (rows, cols)), shape=(len(line_ids), len(buses)))
    b = np.asarray(susceptance)
    nonref = [index for index in range(len(buses)) if index != ref]
    a_reduced = incidence[:, nonref]
    bbus = a_reduced.T @ sparse.diags(b) @ a_reduced
    factor = splu(bbus.tocsc())
    line_index = {key: index for index, key in enumerate(line_ids)}
    candidates = [line_index[key] for key in meshed]
    if candidates:
        injection = a_reduced[candidates, :].T.toarray()
        theta = factor.solve(injection)
        transfer = b[:, None] * (a_reduced @ theta)
        denominator = 1 - transfer[np.asarray(candidates), np.arange(len(candidates))]
        stable = np.abs(denominator) > 1e-9
        lodf = np.zeros_like(transfer)
        lodf[:, stable] = transfer[:, stable] / denominator[stable]
        for index, line_index_value in enumerate(candidates):
            lodf[line_index_value, index] = -1.0
    else:
        lodf = np.empty((len(line_ids), 0))
        stable = np.array([], dtype=bool)
    value = {"line_ids": line_ids, "line_index": line_index, "meshed": meshed,
             "bridges": sorted(bridges, key=lambda key: int(key[1:])), "lodf": lodf, "stable": stable}
    if len(_LODF_CACHE) >= 12:
        _LODF_CACHE.pop(next(iter(_LODF_CACHE)))
    _LODF_CACHE[signature] = value
    return value


def screen_n1(grid: dict, contingency_rule="meshed_in_service", *,
              flow: dict | None = None, status: dict | None = None,
              spec: dict | None = None, period: int = 1) -> dict:
    """Outage distribution using b=1/(x*tap), observed PF and positive RATE_A."""
    states = {key: (status or {}).get(key, row["BR_STATUS"]) for key, row in grid["branch"].items()}
    base = {"method": "直流 PTDF/LODF 近似筛查，非交流 N−1 校核", "contingency_rule": contingency_rule,
            "passed": 0, "evaluated": 0, "failed": 0, "pass_rate": None, "islanding_count": 0,
            "islanding_mw": 0.0, "islanding": [], "details": [], "unsupported_count": 0,
            "flow_basis": "合成演示断面 PF" if flow is None else "导入结果 PF"}
    try:
        factors = _lodf_data(grid, states)
    except (ValueError, RuntimeError) as exc:
        base.update({"status": "unavailable", "reason": str(exc)})
        return base
    active = factors["line_ids"]
    missing = [key for key in active if flow is not None and key not in flow]
    if missing:
        base.update({"status": "unavailable", "reason": f"结果缺少 {len(missing)} 条在运支路有功，未填充基态", "missing_count": len(missing)})
        return base
    f = np.asarray([grid["branch"][key]["PF"] if flow is None else flow[key] for key in active], dtype=float)
    if not np.all(np.isfinite(f)):
        base.update({"status": "unavailable", "reason": "有功潮流含非有限值"})
        return base
    rates = np.asarray([_modified(grid["branch"][key]["RATE_A"], "RATE_A", key, period, spec) for key in active])
    bounded = rates > 0
    candidate_indexes = []
    for index, key in enumerate(factors["meshed"]):
        if contingency_rule == "meshed_kv_ge_110" and branch_kv(grid, grid["branch"][key]) < 110:
            continue
        if not factors["stable"][index]:
            base["unsupported_count"] += 1
            continue
        candidate_indexes.append(index)
    if contingency_rule == "all_in_service":
        base["islanding_count"] = len(factors["bridges"])
        for key in factors["bridges"]:
            islands = island_details(grid, [key], states)
            base["islanding"].append({"contingency": key, "islands": islands,
                                     "net_load_mw": sum(item["net_load_mw"] for item in islands)})
        base["islanding_mw"] = sum(abs(item["net_load_mw"]) for item in base["islanding"])
        base["islanding_note"] = "净负荷绝对值按事故分别累计，可能重复涉及同一母线，不是独立失负荷总量"
    overload_branch_ids = set()
    for index in candidate_indexes:
        key = factors["meshed"][index]
        row_index = factors["line_index"][key]
        post = f + factors["lodf"][:, index] * f[row_index]
        post[row_index] = 0.0
        exceeded = np.flatnonzero(bounded & (np.abs(post) > rates + 1e-7))
        passing = len(exceeded) == 0
        base["passed"] += int(passing)
        base["evaluated"] += 1
        overloads = sorted([{"id": active[j], "flow_mw": float(post[j]), "limit_mva": float(rates[j]), "ratio": float(abs(post[j]) / rates[j])} for j in exceeded], key=lambda item: item["ratio"], reverse=True)
        overload_branch_ids.update(item["id"] for item in overloads)
        max_ratio = float(np.max(np.abs(post[bounded]) / rates[bounded])) if np.any(bounded) else 0.0
        base["details"].append({"contingency": key, "passed": passing, "max_ratio": max_ratio,
                                "overload_count": len(overloads), "overloads": overloads[:20]})
    base["failed"] = base["evaluated"] - base["passed"]
    base["pass_rate"] = base["passed"] / base["evaluated"] if base["evaluated"] else None
    base["overload_branch_ids"] = sorted(overload_branch_ids, key=lambda key: int(key[1:]))
    base["status"] = "evaluated"
    base["units_note"] = "以有功 MW 对比 RATE_A 数值限额；忽略无功和电压约束，不等于交流安全认证"
    return base


def _result_period(result: dict, period_index: int, grid: dict) -> tuple[dict, dict, dict, dict]:
    states = {key: result.get("branch_status", {}).get(key, [row["BR_STATUS"]] * result["periods"])[period_index]
              for key, row in grid["branch"].items()}
    flows = {}
    for key, values in result.get("branch_flow", {}).items():
        if all(field in values and len(values[field]) > period_index for field in ("pf", "qf", "pt", "qt")):
            flows[key] = {field: values[field][period_index] for field in ("pf", "qf", "pt", "qt")}
    vm = {key: values[period_index] for key, values in result.get("bus_vm", {}).items() if len(values) > period_index}
    pf = {key: values["pf"] for key, values in flows.items()}
    return states, flows, vm, pf


def _local_risk(metrics: dict, n1: dict) -> dict:
    loading = metrics.get("loading") or {}
    voltage = metrics.get("voltage") or {}
    reasons = []
    high = False
    medium = False
    if loading.get("overload_count", 0):
        high = True
        reasons.append(f"{loading['overload_count']} 条支路负载率达到或超过 100%")
    if n1.get("failed", 0):
        high = True
        reasons.append(f"直流 N−1 筛查有 {n1['failed']} / {n1['evaluated']} 个事故不通过，非交流校核")
    if n1.get("islanding_count", 0):
        high = True
        reasons.append(f"事故集包含 {n1['islanding_count']} 个孤岛事故，需独立评估")
    if loading.get("heavy_count", 0):
        medium = True
        reasons.append(f"{loading['heavy_count']} 条支路负载率达到重载阈值")
    if voltage.get("violations", 0):
        medium = True
        reasons.append(f"{voltage['violations']} 个母线电压超出原始上下限")
    if n1.get("status") != "evaluated":
        reasons.append(n1.get("reason", "N−1 筛查尚未执行"))
        return {"risk_level": "高" if high else "中", "reasons": reasons, "reward": None,
                "source": "本地规则模拟，课题 3 接口预留", "assessment_complete": False}
    if not reasons:
        reasons.append("本次已计算指标未触发本地规则阈值；不代表全交流安全认证")
    return {"risk_level": "高" if high else "中" if medium else "低", "reasons": reasons, "reward": None,
            "source": "本地规则模拟，课题 3 接口预留", "assessment_complete": True}


def _suggestions(metrics: dict, n1: dict) -> list[dict]:
    suggestions = []
    branches = n1.get("overload_branch_ids", [])[:5]
    if branches:
        suggestions.append({
            "id": "risk-section", "kind": "structural", "title": "追加风险断面有功限额",
            "reason": "这些支路在直流 N−1 筛查中越限，请由调度人员填写限额",
            "structural": {"id": "s-risk-section", "kind": "constraint", "op_id": "section_limit",
                           "params": {"branches": branches, "limit_mw": None, "direction": "forward"},
                           "source": "explicit", "evidence": "", "custom_code": None},
        })
    suggestions.append({
        "id": "risk-switching", "kind": "structural", "title": "限制每时段开关操作次数",
        "reason": "减少协商方案中的操作扰动，次数须人工确定",
        "structural": {"id": "s-risk-switching", "kind": "constraint", "op_id": "switching_limit_per_period",
                       "params": {"k": None}, "source": "explicit", "evidence": "", "custom_code": None},
    })
    violating = next((item for item in (metrics.get("voltage") or {}).get("items", []) if item["violation"]), None)
    if violating:
        suggestions.append({
            "id": "risk-voltage", "kind": "delta", "title": f"复核 {violating['id']} 的电压边界",
            "reason": "基态已有电压越界；请确认业务边界后填写，不自动修正原始数据",
            "delta": {"id": "dp-risk-voltage", "component": "bus", "target": violating["id"],
                      "param": "VMIN" if violating["vm"] < violating["vmin"] else "VMAX", "operation": "set", "direction": None, "value": None,
                      "periods": [], "source": "explicit", "evidence": "", "inference_chain": None, "confirmed": False},
        })
    return suggestions


def evaluate(spec: dict, result: dict | None, grid: dict) -> dict:
    before = snapshot_metrics(grid)
    rule = spec.get("omega", {}).get("contingency_rule", "meshed_in_service")
    n1 = screen_n1(grid, rule)
    after = None
    valid_result = bool(result and result.get("status") in ("optimal", "feasible"))
    if valid_result:
        periods = []
        for index in range(result.get("periods", 16)):
            frames = grid.get("period_data")
            frame = grid if frames is None else frames.get(index + 1, frames.get(str(index + 1)))
            if frame is None:
                raise ValueError(f"E301：缺少第 {index + 1} 时段实际断面数据")
            states, flows, vm, pf = _result_period(result, index, frame)
            observed = snapshot_metrics(frame, flows=flows, vm=vm, status=states, spec=spec, period=index + 1)
            observed["period"] = index + 1
            observed["snapshot_id"] = frame.get("meta", {}).get("id")
            observed["n1"] = screen_n1(frame, rule, flow=pf, status=states, spec=spec, period=index + 1)
            periods.append(observed)
        first = periods[0] if periods else {}
        after = {**first, "periods": periods, "basis": "导入或外部求解结果，未证明交流可行性"}
    observed_periods = (after or {}).get("periods", [])
    complete_periods = [item for item in observed_periods if item.get("loading") is not None and item.get("voltage") is not None]
    usable_after = bool(complete_periods)
    ranks = {"低": 0, "中": 1, "高": 2}
    if usable_after:
        risks = [(item, _local_risk(item, item["n1"])) for item in complete_periods]
        risk_basis, risk = max(risks, key=lambda pair: (ranks[pair[1]["risk_level"]], pair[0]["loading"]["max"], pair[0]["voltage"]["violations"]))
        risk_n1 = risk_basis["n1"]
        risk["basis"] = f"调整后全部 {len(observed_periods)} 个时段（最严重：第 {risk_basis['period']} 时段）"
        risk["period_risks"] = [{"period": item["period"], "risk_level": item_risk["risk_level"], "reasons": item_risk["reasons"]} for item, item_risk in risks]
        risk["assessment_complete"] = len(complete_periods) == len(observed_periods) and all(item_risk["assessment_complete"] for _, item_risk in risks)
        if len(complete_periods) != len(observed_periods):
            risk["reasons"].append("部分时段结果不完整，已计算时段的风险等级不能代表完整窗口")
    else:
        risk_basis, risk_n1 = before, n1
        risk = _local_risk(before, n1)
        risk["basis"] = "调整前合成演示断面"
        if after:
            risk["reasons"].append("调整后数据不完整，风险等级仍基于调整前合成演示断面")
    endpoint = os.getenv("RISK_ENDPOINT", "").strip()
    if endpoint:
        payload = {"task_id": spec.get("task_id"), "version": spec.get("version"),
                   "metrics": {"before": before, "after": after, "n1": n1},
                   "result_summary": {"status": (result or {}).get("status"), "periods": (result or {}).get("periods")}}
        try:
            response = httpx.post(endpoint, json=payload, timeout=15.0)
            response.raise_for_status()
            external = response.json()
            if external.get("risk_level") not in ("低", "中", "高") or not isinstance(external.get("reasons"), list):
                raise ValueError("风险服务返回格式不合法")
            reward = external.get("reward")
            if reward is not None and (not isinstance(reward, (int, float)) or not math.isfinite(reward)):
                raise ValueError("风险服务 reward 非有效数值")
            risk = {**external, "source": "课题 3 风险接口", "basis": risk["basis"], "assessment_complete": True}
        except (httpx.HTTPError, ValueError) as exc:
            risk["source"] = "外部风险接口不可用，回落到本地规则模拟"
            risk["integration_error"] = type(exc).__name__
    return {"before": before, "after": after, "n1": n1, "risk": risk,
            "suggestions": _suggestions(risk_basis, risk_n1),
            "basis": {"before": "合成演示断面 PF/QF/PT/QT/VM", "after": "—（黑盒占位，未调用求解器）" if not valid_result else "导入或外部结果",
                      "single_snapshot": spec.get("snapshot", {}).get("single_snapshot", True),
                      "static_voltage_margin": "待开发：需要连续潮流",
                      "renewable_utilization": "暂不计算：没有新能源机组信息",
                      "n1": "非交流 N−1 校核，仅直流近似筛查"},
            "thresholds": {"heavy": _thresholds()[0], "overload": _thresholds()[1]}}
