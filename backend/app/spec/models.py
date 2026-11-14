"""Single model specification contract; physical validity is checked separately."""
from __future__ import annotations
from typing import Any, Literal
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[3]
WHITELIST = {
    "bus": {"PD": ["scale", "set", "add"], "QD": ["scale", "set", "add"], "VMAX": ["set"], "VMIN": ["set"]},
    "gen": {"PMAX": ["scale", "set"], "PMIN": ["scale", "set"], "GEN_STATUS": ["set"]},
    "branch": {"RATE_A": ["scale", "set"], "BR_STATUS": ["set"]},
}
INFERABLE = {"bus": ["PD", "QD"], "gen": ["PMAX", "PMIN"], "branch": ["RATE_A"]}

class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class DeltaPi(Contract):
    id: str
    component: Literal["bus", "gen", "branch"]
    target: str
    param: str
    operation: Literal["scale", "set", "add"] | None = None
    direction: Literal["increase", "decrease", "set_zero"] | None = None
    value: float | None = None
    periods: list[int] = Field(default_factory=list)
    source: Literal["explicit", "inferred"] = "explicit"
    evidence: str
    inference_chain: list[str] | None = None
    confirmed: bool = False

class StructuralOp(Contract):
    id: str
    kind: Literal["problem", "constraint", "objective"]
    op_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    source: Literal["explicit", "inferred"] = "explicit"
    evidence: str
    custom_code: str | None = None

class BaseModelDef(Contract):
    system: str = "dali_905"
    problem: Literal["multi_period_ac_ots", "multi_period_acopf"] = "multi_period_ac_ots"
    periods: int = 16
    step_min: int = 15
    n_minus_1: bool = True
    contingency_rule: Literal["all_in_service", "meshed_in_service", "meshed_kv_ge_110"] = "meshed_in_service"
    switchable_rule: Literal["all_in_service", "meshed_in_service", "meshed_kv_ge_110"] = "meshed_in_service"

class SolverReq(Contract):
    solver: Literal["mock", "ipopt", "copt", "gurobi", "external"] = "mock"
    time_limit_s: int = 60
    tolerance: float = 1e-6
    outputs: list[str] = Field(default_factory=lambda: ["branch_status", "gen_p", "bus_vm", "branch_flow"])

class SnapshotRef(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = "pasExp_20261004_153500_1"
    files: list[str] = Field(default_factory=list)
    start: str = "2026-10-04T15:35:00"
    periods: int = 16
    step_min: int = 15
    single_snapshot: bool = True

class LevelOverride(Contract):
    level: Literal["L1", "L2", "L3", "L4"]
    reason: str = Field(min_length=1)

class ModelSpec(Contract):
    task_id: str
    version: int = 1
    parent_version: int | None = None
    change_reason: str | None = None
    instruction: str
    snapshot: SnapshotRef = Field(default_factory=SnapshotRef)
    omega: BaseModelDef = Field(default_factory=BaseModelDef)
    delta_pi: list[DeltaPi] = Field(default_factory=list)
    structural: list[StructuralOp] = Field(default_factory=list)
    solver_req: SolverReq = Field(default_factory=SolverReq)
    level_override: LevelOverride | None = None

def as_dict(spec):
    return spec.model_dump() if isinstance(spec, BaseModel) else spec

def derived_level(spec) -> str:
    spec = as_dict(spec)
    changes, structural = spec.get("delta_pi", []), spec.get("structural", [])
    if not changes and not structural:
        return "待追问"
    inferred = any(x.get("source") == "inferred" for x in changes)
    return ("L4" if inferred else "L3") if structural else ("L2" if inferred else "L1")

def load_config(name):
    import yaml
    with (ROOT / "config" / name).open(encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}
