"""Read-only, source-backed historical calculation records.

These records are independent of ModelSpec tasks. Reading a saved result never
parses a new instruction, imports into a task, or invokes an optimizer.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Any
from app.config import ROOT

HISTORY_FILES = {"905-e2-rolling": ROOT / "data" / "history_905" / "e2_rolling.json"}
IPOPT_BUNDLE = {
    "name": "IPOPT 905 代码更新包",
    "ready": False,
    "mode": "待补输入",
    "reason": "当前提供的是 v0.6 更新包，缺少匹配的真实输入和历史解库；尚未配置为新任务求解器。",
    "required_files": ["data/network_16period.npz", "data/config.json", "data/time_spec.json",
                       "data/model_scope.json", "data/history/library.json"],
    "scope": "原包固定拓扑、BASE+B876+G192；不能直接视作任意 AC-OTS/N−1 需求的求解链。",
    "archive_sha256": "8aca98e84fa536b31d7fae3565358d60b04b4c0370e793a4e913cab42086852d",
}

def _reject_constant(value):
    raise ValueError("历史记录包含非有限数值：" + value)

def _finite_tree(value: Any):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("历史记录包含非有限数值")
    if isinstance(value, dict):
        for child in value.values():
            _finite_tree(child)
    elif isinstance(value, list):
        for child in value:
            _finite_tree(child)

def validate_history(record: dict, record_id: str) -> dict:
    if not isinstance(record, dict) or record.get("id") != record_id:
        raise ValueError("历史记录身份不一致")
    if record.get("kind") != "historical_replay":
        raise ValueError("历史入口只能展示明确标注的历史回放")
    periods = record.get("period_count")
    if type(periods) is not int or not 1 <= periods <= 16:
        raise ValueError("历史记录时段数无效")
    rows = record.get("rows")
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows) or [r.get("period") for r in rows] != list(range(1, periods + 1)):
        raise ValueError("历史记录时段行不完整或顺序错误")
    counts = record.get("counts") or {}
    series = record.get("series") or {}
    for field, count_key, prefix in [("bus_vm", "buses", "B"), ("gen_p", "generators", "G")]:
        before = (series.get("before") or {}).get(field)
        after = (series.get("after") or {}).get(field)
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise ValueError("历史记录缺少成对状态：" + field)
        if not before or set(before) != set(after) or len(before) != counts.get(count_key):
            raise ValueError("历史记录对象编号或数量不匹配：" + field)
        for collection in (before, after):
            for identity, values in collection.items():
                if not identity.startswith(prefix) or not identity[len(prefix):].isdigit():
                    raise ValueError("历史记录对象编号无效")
                if not isinstance(values, list) or len(values) != periods:
                    raise ValueError("历史记录时序长度不匹配：" + identity)
                if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                    raise ValueError("历史记录状态包含缺失或非有限数值：" + identity)
    for row in rows:
        for side in ("before", "after"):
            total, failed, rate = (row.get("n1_" + side + suffix) for suffix in ("_total", "_failed", "_pct"))
            if total is None and failed is None and rate is None:
                continue
            if type(total) is not int or type(failed) is not int or not 0 <= failed <= total or total <= 0:
                raise ValueError("历史 N−1 事故计数不一致")
            expected = (total - failed) / total * 100
            if type(rate) not in (int, float) or not math.isclose(rate, expected, abs_tol=1e-5):
                raise ValueError("历史 N−1 通过率与分母不一致")
    provenance = record.get("provenance") or {}
    if not provenance.get("source_files") or not provenance.get("snapshot_sha256"):
        raise ValueError("历史记录缺少原件来源")
    if not (record.get("scope") or {}).get("limitations"):
        raise ValueError("历史记录缺少适用范围")
    _finite_tree(record)
    return record

def record_path(record_id: str) -> Path:
    if record_id not in HISTORY_FILES:
        raise KeyError("历史计算记录不存在")
    path = HISTORY_FILES[record_id]
    if not path.is_file():
        raise FileNotFoundError("历史计算记录尚未准备")
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("历史计算记录超过大小限制")
    return path

def load_history(record_id: str) -> dict:
    path = record_path(record_id)
    payload = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=_reject_constant)
    return validate_history(payload, record_id)

def history_catalog() -> dict:
    items = []
    for record_id, path in HISTORY_FILES.items():
        if not path.is_file():
            continue
        record = load_history(record_id)
        items.append({key: record.get(key) for key in
                      ("id", "title", "kind", "solver", "period_count", "step_minutes", "counts", "summary")})
    return {
        "items": items,
        "integrations": {"ipopt": IPOPT_BUNDLE},
        "notice": "已有计算的只读回放；没有运行新优化，也不替代当前自然语言任务的结果。",
    }
