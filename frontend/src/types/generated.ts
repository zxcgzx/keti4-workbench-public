/* 自动生成：来源为后端 Pydantic ModelSpec JSON Schema，请运行 npm run typegen 更新。 */

export type TaskId = string;
export type Version = number;
export type ParentVersion = number | null;
export type ChangeReason = string | null;
export type Instruction = string;
export type Id = string;
export type Files = string[];
export type Start = string;
export type Periods = number;
export type StepMin = number;
export type SingleSnapshot = boolean;
export type System = string;
export type Problem = "multi_period_ac_ots" | "multi_period_acopf";
export type Periods1 = number;
export type StepMin1 = number;
export type NMinus1 = boolean;
export type ContingencyRule = "all_in_service" | "meshed_in_service" | "meshed_kv_ge_110";
export type SwitchableRule = "all_in_service" | "meshed_in_service" | "meshed_kv_ge_110";
export type Id1 = string;
export type Component = "bus" | "gen" | "branch";
export type Target = string;
export type Param = string;
export type Operation = ("scale" | "set" | "add") | null;
export type Direction = ("increase" | "decrease" | "set_zero") | null;
export type Value = number | null;
export type Periods2 = number[];
export type Source = "explicit" | "inferred";
export type Evidence = string;
export type InferenceChain = string[] | null;
export type Confirmed = boolean;
export type DeltaPi = DeltaPi1[];
export type Id2 = string;
export type Kind = "problem" | "constraint" | "objective";
export type OpId = string;
export type Source1 = "explicit" | "inferred";
export type Evidence1 = string;
export type CustomCode = string | null;
export type Structural = StructuralOp[];
export type Solver = "mock" | "ipopt" | "copt" | "gurobi" | "external";
export type TimeLimitS = number;
export type Tolerance = number;
export type Outputs = string[];
export type Level = "L1" | "L2" | "L3" | "L4";
export type Reason = string;

export interface ModelSpec {
  task_id: TaskId;
  version?: Version;
  parent_version?: ParentVersion;
  change_reason?: ChangeReason;
  instruction: Instruction;
  snapshot?: SnapshotRef;
  omega?: BaseModelDef;
  delta_pi?: DeltaPi;
  structural?: Structural;
  solver_req?: SolverReq;
  level_override?: LevelOverride | null;
}
export interface SnapshotRef {
  id?: Id;
  files?: Files;
  start?: Start;
  periods?: Periods;
  step_min?: StepMin;
  single_snapshot?: SingleSnapshot;
  [k: string]: unknown;
}
export interface BaseModelDef {
  system?: System;
  problem?: Problem;
  periods?: Periods1;
  step_min?: StepMin1;
  n_minus_1?: NMinus1;
  contingency_rule?: ContingencyRule;
  switchable_rule?: SwitchableRule;
}
export interface DeltaPi1 {
  id: Id1;
  component: Component;
  target: Target;
  param: Param;
  operation?: Operation;
  direction?: Direction;
  value?: Value;
  periods?: Periods2;
  source?: Source;
  evidence: Evidence;
  inference_chain?: InferenceChain;
  confirmed?: Confirmed;
}
export interface StructuralOp {
  id: Id2;
  kind: Kind;
  op_id: OpId;
  params?: Params;
  source?: Source1;
  evidence: Evidence1;
  custom_code?: CustomCode;
}
export interface Params {
  [k: string]: unknown;
}
export interface SolverReq {
  solver?: Solver;
  time_limit_s?: TimeLimitS;
  tolerance?: Tolerance;
  outputs?: Outputs;
}
export interface LevelOverride {
  level: Level;
  reason: Reason;
}
