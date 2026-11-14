"""Canonical model-family selection; omega.problem is the only executable source."""
from __future__ import annotations
import re
from app.spec.models import derived_level

PROBLEM_NAMES = {
    "multi_period_ac_ots": "AC-OTS · 可调整拓扑",
    "multi_period_acopf": "AC-OPF · 固定拓扑",
}

def detect_problem(text):
    patterns = [
        ("multi_period_acopf", r"(?<![A-Za-z])AC[\s_−–-]*OPF(?![A-Za-z0-9])|(?<![A-Za-z_−–-])OPF(?![A-Za-z0-9])|不开断|固定拓扑"),
        ("multi_period_ac_ots", r"(?<![A-Za-z])AC[\s_−–-]*OTS(?![A-Za-z0-9])|(?<![A-Za-z_−–-])OTS(?![A-Za-z0-9])|可调整拓扑"),
    ]
    matches = [(m.start(), problem, m.group()) for problem, pattern in patterns
               for m in re.finditer(pattern, text, re.I)]
    if not matches:
        return None
    _, problem, evidence = max(matches, key=lambda item: item[0])
    return {"problem": problem, "evidence": evidence}

def synchronize_problem(spec):
    """Remove an obsolete fixed-topology operator after an explicit OTS selection."""
    if spec.get("omega", {}).get("problem") != "multi_period_acopf":
        spec["structural"] = [op for op in spec.get("structural", [])
                              if op.get("op_id") != "multi_period_acopf"]
    return spec

def apply_problem_choice(state, selected=None, source=None):
    if selected is not None and selected not in PROBLEM_NAMES:
        raise ValueError("仅支持 AC-OTS 或 AC-OPF")
    spec = state["spec"]
    detected = detect_problem(spec.get("instruction", ""))
    actual = selected or (detected["problem"] if detected else spec["omega"]["problem"])
    conflict = bool(selected and detected and selected != detected["problem"])
    spec["omega"]["problem"] = actual
    synchronize_problem(spec)
    state["problem_selection"] = {
        "selected": selected,
        "detected": detected["problem"] if detected else None,
        "actual": actual,
        "source": source or ("用户选择" if selected else "自动识别"),
        "conflict": conflict,
    }
    warnings = [message for message in state.get("warnings", [])
                if not message.startswith("模型选择提示：")]
    if conflict:
        warnings.append(f"模型选择提示：原文识别为{PROBLEM_NAMES[detected['problem']]}，"
                        f"当前采用用户选择的{PROBLEM_NAMES[actual]}；请在方程确认时核对。")
    state["warnings"] = warnings
    state["level"] = derived_level(spec)
    return state
