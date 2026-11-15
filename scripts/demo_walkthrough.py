"""Run the D1/D7 modeling walkthrough against an already-running workbench.

Default: offline rule parsing -> two explicit answers -> confirmed model/code ->
five-layer check -> mock blackbox -> baseline metrics/risk. No optimizer is run.
All HTTP responses and generated artifacts are written into one new evidence folder.

Examples (run from the project root):
  python scripts/demo_walkthrough.py
  python scripts/demo_walkthrough.py --case D1
  python scripts/demo_walkthrough.py --solver baseline
  python scripts/demo_walkthrough.py --engine openai --solver baseline
  python scripts/demo_walkthrough.py --result path/to/normalized_result.json
  python scripts/demo_walkthrough.py --solver external

The openai option requires a backend configured with LLM_BASE_URL/LLM_MODEL and
optional LLM_API_KEY. A demo fallback is an error here, never LLM success.
The external option invokes the backend's configured SOLVER_CMD; it is explicit.
Imported result identity, model compatibility and physical validity must be
established by its adapter/provenance; a successful JSON import proves none of them.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def reject_constant(value):
    raise ValueError("JSON contains a non-finite number: " + value)


class Api:
    def __init__(self, base, directory, events):
        self.base, self.directory, self.events = base.rstrip("/"), directory, events
        self.sequence = 0

    def call(self, method, path, payload=None, label="response", timeout=120):
        self.sequence += 1
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = Request(self.base + path, data=body, method=method,
                          headers={"Content-Type": "application/json", "Accept": "application/json"})
        started = time.perf_counter()
        event = {"method": method, "path": path, "started_at": now()}
        try:
            with urlopen(request, timeout=timeout) as response:
                data = response.read(64 * 1024 * 1024 + 1)
                if len(data) > 64 * 1024 * 1024:
                    raise RuntimeError("接口响应超过 64 MiB，已停止保存。")
                result = json.loads(data.decode("utf-8-sig"), parse_constant=reject_constant)
                event["http_status"] = response.status
        except HTTPError as exc:
            raw = exc.read(16000).decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw).get("detail", raw)
            except ValueError:
                detail = raw
            event.update(http_status=exc.code, error=str(detail))
            write_json(self.directory / f"{self.sequence:02d}_{label}_error.json", event)
            raise RuntimeError(f"{method} {path} 返回 HTTP {exc.code}：{str(detail)[:1500]}") from exc
        except URLError as exc:
            event["error"] = str(exc.reason)
            raise RuntimeError("无法连接工作台。请先启动 python run.py --port 8014，并检查 --base-url。") from exc
        finally:
            event["elapsed_s"] = round(time.perf_counter() - started, 3)
            self.events.append(event)
        artifact = f"{self.sequence:02d}_{label}.json"
        write_json(self.directory / artifact, result)
        event["artifact"] = artifact
        return result


def check_example_spec(spec, expected):
    """Fail closed when a model's interpretation does not match this fixed example."""
    omega = spec.get("omega", {})
    if any(omega.get(k) != expected[k] for k in ("periods", "step_min", "n_minus_1")):
        raise RuntimeError("解析后的基础时段或 N−1 要求与本例不一致，请先人工核对规格。")
    if omega.get("problem") != "multi_period_ac_ots":
        raise RuntimeError("本例要求多时段 AC-OTS，解析结果不一致。")
    delta = spec.get("delta_pi", [])
    if len(delta) != 1:
        raise RuntimeError("本例应只有一项指定参数修改；未自动接受额外修改。")
    item = delta[0]
    for key, value in expected["delta"].items():
        actual = sorted(item.get(key, [])) if key == "periods" else item.get(key)
        if actual != value:
            raise RuntimeError(f"本例规格不匹配：delta_pi[0].{key}={actual!r}，应为 {value!r}。")
    if not item.get("confirmed"):
        raise RuntimeError("本例参数值尚未确认。")
    structural = spec.get("structural", [])
    expected_ops = [expected["structural_op"]] if expected.get("structural_op") else []
    if [item.get("op_id") for item in structural] != expected_ops:
        raise RuntimeError("本例结构算子不匹配；未自动接受额外或缺失的算子。")


def arguments():
    parser = argparse.ArgumentParser(description="D1/D7 自然语言到风险评估的可复现实例；默认不执行真实求解。")
    parser.add_argument("--case", choices=("D1", "D7"), default="D7", help="D7含两项追问；D1为明确数值的简短例子")
    parser.add_argument("--base-url", default="http://127.0.0.1:8014", help="已启动的工作台根地址")
    parser.add_argument("--engine", choices=("demo", "openai"), default="demo",
                        help="openai 必须实际调用成功，回落到 demo 将停止")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--solver", choices=("baseline", "mock", "external"),
                       help="默认 mock；baseline 跳过求解；external 调用服务端 SOLVER_CMD")
    group.add_argument("--result", type=Path, help="导入已规范化的外部结果 JSON；不是重新求解")
    parser.add_argument("--snapshot-id", help="默认使用服务端当前默认断面")
    parser.add_argument("--output-dir", type=Path, help="新的证据目录；必须不存在")
    parser.add_argument("--wait-seconds", type=int, default=120, help="外部作业等待上限，默认120秒")
    parser.add_argument("--try-build", action="store_true", help="请求已有 T=1 Pyomo 试构建，不执行优化")
    parser.add_argument("--with-adjustment-draft", action="store_true", help="追加第一条风险建议为待补数值的新草稿")
    args = parser.parse_args()
    address = urlsplit(args.base_url)
    if address.scheme not in ("http", "https") or not address.hostname or address.username or address.password or address.path not in ("", "/") or address.query or address.fragment:
        parser.error("--base-url 应为无凭据、无路径的 http(s) 根地址，例如 http://127.0.0.1:8014")
    if not 1 <= args.wait_seconds <= 7200:
        parser.error("--wait-seconds 应在 1–7200 内")
    if args.result and (not args.result.is_file() or args.result.stat().st_size > 32 * 1024 * 1024):
        parser.error("--result 必须是存在且不超过32MiB的结果JSON")
    return args


def run(args):
    scenario_path = ROOT / "examples" / "demo-workflow.json"
    definition = json.loads(scenario_path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    scenario = definition["cases"][args.case]
    mode = "import" if args.result else args.solver or "mock"
    folder = args.output_dir or ROOT / "runs" / "demo_walkthrough" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex[:8])
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    evidence = folder / "evidence"
    evidence.mkdir()
    events = []
    api = Api(args.base_url, evidence, events)
    report = {
        "status": "running", "started_at": now(), "example_id": scenario["example_id"],
        "instruction": scenario["instruction"], "specified_answers": scenario["answers"],
        "requested_engine": args.engine, "solver_path": mode, "base_url": api.base,
        "evidence_dir": str(folder), "source_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scenario_sha256": hashlib.sha256(scenario_path.read_bytes()).hexdigest(),
        "try_build": args.try_build, "external_command_called": False,
        "notes": [
            scenario["scope"],
            "规格/格式校验通过和Pyomo试构建都不等于交流物理可行性或最优性。",
            "地理地图没有设备经纬度映射，N−1指标为直流近似筛查。"
        ]
    }
    write_json(folder / "scenario.json", scenario)
    task_id = None
    stage = "health"
    try:
        health = api.call("GET", "/api/health", label="health")
        if health.get("app") != "keti4-workbench":
            raise RuntimeError("目标地址不是 keti4-workbench，未创建任务。")
        snapshots = api.call("GET", "/api/snapshots", label="snapshots")
        snapshot_items = snapshots.get("items", [])
        snapshot_id = args.snapshot_id or snapshots.get("default_id") or (snapshot_items[0]["id"] if snapshot_items else None)
        if not snapshot_id or not any(x["id"] == snapshot_id for x in snapshot_items):
            raise RuntimeError("未找到指定的演示断面。")
        report["snapshot_id"] = snapshot_id
        api.call("GET", "/api/snapshots/" + quote(snapshot_id, safe="") + "/summary", label="snapshot_summary")
        stage = "create_task"
        task = api.call("POST", "/api/tasks", {
            "instruction": scenario["instruction"], "engine": args.engine,
            "snapshot_id": snapshot_id, "problem": scenario["problem"],
            "ask_questions": True, "max_rounds": 10
        }, "task")
        task_id = task["task_id"]
        report["task_id"] = task_id
        route = "/api/tasks/" + quote(task_id, safe="")
        report["task_api_url"] = api.base + route
        report["browser_url"] = api.base + "/workbench?task=" + quote(task_id, safe="")
        print("[1/7] 已创建任务：" + task_id, flush=True)
        stage = "parse"
        state = api.call("POST", route + "/parse", {}, "parse")
        report["actual_engine"] = state.get("engine")
        report["initial_level"] = state.get("level")
        if args.engine == "openai" and state.get("engine") != "openai":
            raise RuntimeError("LLM未实际解析成功，实际引擎为 " + str(state.get("engine")) + "；本脚本不把离线回落记作LLM成功。")
        print("[2/7] 指令解析完成，实际引擎：" + str(state.get("engine")) + "；层级：" + str(state.get("level")), flush=True)

        stage = "answer"
        answers = []
        for _ in range(10):
            pending = [q for q in state.get("questions", []) if not q.get("answered")]
            if not pending:
                break
            # Periods first, then the precise reduction ratio, using returned IDs.
            question = next((q for q in pending if q.get("kind") == "periods"), pending[0])
            kind = question.get("kind")
            if kind not in scenario["answers"]:
                raise RuntimeError("出现本例未预设的追问，需人工处理：" + str(question.get("text", kind)))
            if kind == "amplitude":
                target = next((x for x in state["spec"]["delta_pi"] if x["id"] == question.get("target_id")), {})
                expected_delta = scenario["expected"]["delta"]
                if target.get("target") != expected_delta["target"] or target.get("param") != expected_delta["param"]:
                    raise RuntimeError("幅度追问不对应本例指定参数，未自动套用答案。")
            answer = scenario["answers"][kind]
            state = api.call("POST", route + "/answer", {"question_id": question["id"], "answer": answer}, "answer")
            answers.append({"question_id": question["id"], "kind": kind, "question": question.get("text"), "answer": answer})
        if any(not q.get("answered") for q in state.get("questions", [])):
            raise RuntimeError("追问尚未全部解决，未确认模型。")
        report["answers_applied"] = answers
        report["actual_engine_after_answers"] = state.get("engine")
        check_example_spec(state["spec"], scenario["expected"])
        print("[3/7] 条件已确认：" + scenario["answer_summary"], flush=True)

        stage = "confirm"
        spec = state["spec"]
        if mode in ("import", "external"):
            spec["solver_req"]["solver"] = "external"
            spec = api.call("PUT", route + "/spec", spec, "solver_requirement")
        confirmed = api.call("POST", route + "/confirm", {"spec": spec}, "confirmed")
        report["confirmed_version"] = confirmed["version"]
        artifacts = confirmed["artifacts"]
        if not artifacts.get("code") or not artifacts.get("latex_text"):
            raise RuntimeError("确认后缺少模型代码或方程。")
        (folder / "model.py").write_text(artifacts["code"], encoding="utf-8")
        (folder / "model.tex").write_text(artifacts["latex_text"], encoding="utf-8")
        write_json(folder / "spec.json", confirmed["spec"])
        print("[4/7] 方程与代码已确认并归档，版本 v" + str(confirmed["version"]), flush=True)
        stage = "check"
        checked = api.call("POST", route + "/check", {"try_build": args.try_build}, "check")
        report["check"] = {"errors": checked.get("errors"), "warnings": checked.get("warnings"), "build": checked.get("build")}
        if checked.get("errors", 0):
            raise RuntimeError("五层校验仍有错误，未进入求解或导入。")
        warning_messages = [x.get("message") for x in checked.get("findings", []) if x.get("severity") in ("warning", "warn")]
        report["acknowledged_warnings"] = warning_messages
        print("[5/7] 校验错误 0；警告 " + str(checked.get("warnings", 0)) + "，全部保留在证据中。", flush=True)
        for message in warning_messages:
            print("  警告：" + str(message), flush=True)

        stage = "result"
        if mode == "import":
            original = args.result.read_bytes()
            supplied = json.loads(original.decode("utf-8-sig"), parse_constant=reject_constant)
            payload = supplied.get("result", supplied)
            if not isinstance(payload, dict):
                raise RuntimeError("导入文件应为结果JSON对象，或包含result字段的对象。")
            report["import"] = {"file": str(args.result.resolve()), "sha256": hashlib.sha256(original).hexdigest(),
                                "bytes": len(original), "physical_validation": "not_performed_by_walkthrough",
                                "scenario_compatibility": "must_be_established_by_result_adapter"}
            report["notes"].append("导入是已有结果回放，字段/编号/时段验证不证明结果对应本例；请依据适配器的来源和模型匹配记录核对。")
            api.call("POST", route + "/results/import", {"result": payload, "acknowledge_warnings": True}, "import_result")
            print("[6/7] 已导入外部结果文件；本次未重新求解。", flush=True)
        elif mode in ("mock", "external"):
            job = api.call("POST", route + "/solve", {"mode": mode, "acknowledge_warnings": True}, "solver_job")
            report["job_id"] = job["job_id"]
            report["job_api_url"] = api.base + "/api/jobs/" + quote(job["job_id"], safe="")
            report["external_command_called"] = mode == "external"
            deadline = time.monotonic() + args.wait_seconds
            while job.get("status") not in ("completed", "error"):
                if time.monotonic() >= deadline:
                    raise RuntimeError("客户端等待达到上限；已保留作业ID。服务端可能仍在运行，请查看作业，勿重复提交。")
                time.sleep(min(1, max(0, deadline - time.monotonic())))
                job = api.call("GET", "/api/jobs/" + quote(job["job_id"], safe=""), label="job_status")
            report["job_status"] = job.get("status")
            if job.get("status") == "error":
                raise RuntimeError("求解作业错误：" + str(job.get("error")))
            if mode == "mock":
                print("[6/7] mock黑盒完成；未调用优化器，没有调整后方案。", flush=True)
            else:
                print("[6/7] 已配置外部程序返回结果；实际求解范围需以该程序证据核对。", flush=True)
        else:
            print("[6/7] 已选择直接评估基态，跳过求解黑盒。", flush=True)
        result = api.call("GET", route + "/results", label="results")
        write_json(folder / "result.json", result.get("result"))
        report["result_status"] = (result.get("result") or {}).get("status")
        report["result_source"] = (result.get("result") or {}).get("source")
        if mode in ("mock", "baseline") and report["result_status"] not in ("mock", None):
            raise RuntimeError("默认演示返回了意外的非mock结果，已停止。")
        if mode == "external" and report["result_status"] in ("mock", "error", None):
            raise RuntimeError("外部程序未返回可用的求解结论；不记作真实结果接入成功。")
        stage = "evaluate"
        metrics = api.call("POST", route + "/evaluate", {}, "metrics")
        write_json(folder / "metrics.json", metrics)
        report["evaluation_stage"] = metrics.get("evaluation_stage")
        report["after_present"] = metrics.get("after") is not None
        report["risk"] = metrics.get("risk")
        report["n1_summary"] = {key: metrics.get("n1", {}).get(key) for key in ("method", "evaluated", "passed", "pass_rate", "islanding_count")}
        if mode in ("mock", "baseline") and (report["after_present"] or report["evaluation_stage"] != "baseline"):
            raise RuntimeError("基态演示出现意外调整后数据，不能记为通过。")
        print("[7/7] 指标与风险已生成，评估阶段：" + str(report["evaluation_stage"]), flush=True)
        if args.with_adjustment_draft:
            stage = "adjustment"
            suggestions = metrics.get("suggestions", [])
            if not suggestions:
                raise RuntimeError("当前评估没有协商建议，无法生成协商草稿。")
            adjusted = api.call("POST", route + "/adjust", {"suggestion_ids": [suggestions[0]["id"]],
                               "reason": args.case + "演示：保留风险建议为待人工补数值的下一版草稿"}, "adjustment_draft")
            report["adjustment_draft"] = {"version": adjusted["spec"]["version"], "parent_version": adjusted["spec"]["parent_version"],
                                          "confirmed": False, "requires_manual_values": True}
            print("附加步骤：已生成待补数值的协商草稿；未自动确认或求解。", flush=True)
        api.call("GET", route + "/versions", label="versions")
        api.call("GET", route + "/logs", label="logs")
        report["status"] = "passed"
        report["workflow_complete"] = True
        report["scope"] = "baseline_demo_no_optimization" if mode in ("baseline", "mock") else "imported_result_replay" if mode == "import" else "configured_external_command"
        return_code = 0
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                      workflow_complete=False, failed_stage=stage, error=str(exc))
        print("演示未完成：" + str(exc), file=sys.stderr, flush=True)
        return_code = 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        report["finished_at"] = now()
        report["http_events"] = events
        summary_lines = [
            "建模工作台 " + scenario["example_id"] + " 演示记录",
            "运行结论：" + report["status"],
            "输入：" + scenario["instruction"],
            "给定条件：" + scenario["answer_summary"],
            "实际引擎：" + str(report.get("actual_engine", "未解析")),
            "结果路径：" + mode,
            "文件/程序声明状态：" + str(report.get("result_status")),
            "评估阶段：" + str(report.get("evaluation_stage", "未评估")),
            "有调整后数据：" + str(report.get("after_present", False)),
            "浏览器查看：" + report.get("browser_url", "尚未创建任务"),
            "错误：" + str(report.get("error", "无")),
            "", "范围说明：", *report["notes"],
            "默认mock与baseline均未执行真实优化；导入属于已有结果回放。",
            "完整请求记录、方程、代码、规格、结果与指标见本目录及 REPORT.json。"
        ]
        (folder / "SUMMARY.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
        report["artifacts"] = [
            {"path": path.relative_to(folder).as_posix(), "bytes": path.stat().st_size,
             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(folder.rglob("*")) if path.is_file() and path.name != "REPORT.json"
        ]
        write_json(folder / "REPORT.json", report)
        print("证据目录：" + str(folder), flush=True)
        if task_id:
            print("浏览器查看：" + report["browser_url"], flush=True)
            print("任务记录：" + report["task_api_url"], flush=True)
        print("运行结论：" + report["status"] + "；查看 REPORT.json 了解演示/导入/外部求解范围。", flush=True)
    return return_code


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(run(arguments()))
