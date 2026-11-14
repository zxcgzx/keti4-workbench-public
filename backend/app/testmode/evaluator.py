"""Specification-level evaluation; no objective-value or solver claims."""
import csv
import io
import json
import math
import time
from collections import defaultdict
from app.config import ROOT, RUNS_DIR, dump_json, read_json
from app.modeling.engine import parse_instruction
from app.spec.models import derived_level
from app.checks.validator import run_checks

def cases_of(value):
    if isinstance(value, list):
        return value
    return value.get("cases", value.get("items", []))

def builtins():
    return cases_of(read_json(ROOT/"data"/"testsets"/"demo_cases.json", []))

def normalize_cases(payload):
    cases = cases_of(payload)
    if not isinstance(cases, list) or not cases or len(cases) > 200:
        raise ValueError("测试集需要1至200条用例")
    for i, case in enumerate(cases):
        if not isinstance(case, dict) or not str(case.get("instruction", "")).strip():
            raise ValueError(f"第{i+1}条缺少instruction")
        if len(str(case["instruction"])) > 8000:
            raise ValueError("单条指令最多8000字符")
        case.setdefault("id", f"C{i+1}")
    return cases

def parse_upload(raw, filename):
    text = raw.decode("utf-8-sig")
    if filename.lower().endswith(".csv"):
        cases = list(csv.DictReader(io.StringIO(text)))
        for c in cases:
            for k in ("expected", "expected_spec", "delta_pi", "structural", "must_fail"):
                if c.get(k):
                    try:
                        c[k] = json.loads(c[k])
                    except json.JSONDecodeError:
                        if k not in ("must_fail",):
                            raise ValueError(f"{k}列需要有效JSON")
        return normalize_cases(cases)
    return normalize_cases(json.loads(text))

def same_value(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int,float)) and isinstance(b, (int,float)):
        return math.isclose(a,b, rel_tol=0.01, abs_tol=1e-10)
    if isinstance(b, dict):
        return isinstance(a,dict) and all(k in a and same_value(a[k],v) for k,v in b.items())
    if isinstance(b,list):
        return isinstance(a,list) and len(a)==len(b) and all(same_value(x,y) for x,y in zip(a,b))
    return a == b

def delta_match(actual, expected, periods):
    keys = ("target","param","operation","direction","value","source")
    if any(k in expected and not same_value(actual.get(k),expected[k]) for k in keys):
        return False
    if "periods" in expected:
        a = actual.get("periods") or list(range(1,periods+1))
        b = expected.get("periods") or list(range(1,periods+1))
        if sorted(a) != sorted(b):
            return False
    return True

def matched_count(actual, expected, predicate):
    used = set()
    for wanted in expected:
        for i, item in enumerate(actual):
            if i not in used and predicate(item,wanted):
                used.add(i)
                break
    return len(used)

def expectation(case):
    ref = case.get("expected_spec") or case.get("expected") or {}
    if not isinstance(ref,dict):
        ref = {}
    return {
        "level":case.get("expected_level",ref.get("level",case.get("level","待追问"))),
        "delta_pi":ref.get("delta_pi",case.get("delta_pi",[])),
        "structural":ref.get("structural",case.get("structural",[])),
        "must_fail":case.get("must_fail",ref.get("must_fail",False)),
        "codes":case.get("expected_codes",ref.get("codes",ref.get("finding_codes",[])))
    }

def run_cases(cases, grid, engine="demo"):
    results=[]
    totals=defaultdict(lambda:{"total":0,"passed":0})
    tp=predicted=reference=hallucinations=0
    for case in cases:
        started=time.perf_counter()
        expected=expectation(case)
        try:
            parsed=parse_instruction(case["instruction"],grid, "test-"+str(case["id"]), engine=engine)
            spec=parsed["spec"]
            actual_level=derived_level(spec)
            report=run_checks(spec,grid,parse_state=parsed,try_build=False)
            findings=report.get("findings",[])
            codes={f["code"] for f in findings}
            actual_delta=spec.get("delta_pi",[])
            wanted=expected["delta_pi"]
            hit=matched_count(actual_delta,wanted,lambda a,b:delta_match(a,b,spec["omega"]["periods"]))
            tp+=hit; predicted+=len(actual_delta); reference+=len(wanted)
            structure=spec.get("structural",[])
            sh=matched_count(structure,expected["structural"],lambda a,b:a.get("op_id")==b.get("op_id") and same_value(a.get("params",{}),b.get("params",{})))
            errors=[]
            if actual_level!=expected["level"]:
                errors.append(f"层级期望{expected['level']}，实际{actual_level}")
            if hit!=len(wanted) or len(actual_delta)!=len(wanted):
                errors.append("参数修改与参考规格不一致")
            if sh!=len(expected["structural"]) or len(structure)!=len(expected["structural"]):
                errors.append("结构修改与参考规格不一致")
            must=expected["must_fail"]
            required=list(expected["codes"])+list(case.get("expected_errors",[]))
            if isinstance(must,str) and must.startswith("E"):
                required.append(must)
            elif isinstance(must,list):
                required.extend(must)
            if must and not any(f.get("severity")=="error" for f in findings):
                errors.append("must_fail用例未触发错误")
            if required and not set(required).issubset(codes):
                errors.append("缺少预期校验代码："+",".join(set(required)-codes))
            if not must and report.get("errors",0):
                errors.append("出现非预期校验错误")
            forbidden=set(case.get("forbidden_codes",[]))
            if forbidden & codes:
                errors.append("出现禁止的校验代码："+",".join(forbidden & codes))
            expected_questions=case.get("questions",[])
            if isinstance(expected_questions,list):
                actual_questions={q.get("kind") for q in parsed.get("questions",[])}
                if not set(expected_questions).issubset(actual_questions):
                    errors.append("缺少预期追问："+",".join(set(expected_questions)-actual_questions))
            hallucinations+=sum(f["code"] in ("W501","E101") for f in findings)
            result={"id":case["id"],"instruction":case["instruction"],"expected_level":expected["level"],
                    "actual_level":actual_level,"passed":not errors,"errors":errors,"parse":parsed,"check":report}
        except Exception as exc:
            result={"id":case["id"],"instruction":case["instruction"],"expected_level":expected["level"],
                    "actual_level":"错误","passed":False,"errors":[str(exc)],"parse":None,"check":None}
        result["elapsed_ms"]=round((time.perf_counter()-started)*1000,2)
        totals[str(expected["level"])]["total"]+=1
        totals[str(expected["level"])]["passed"]+=int(result["passed"])
        results.append(result)
    passed=sum(r["passed"] for r in results)
    return {"basis":"规格级比对；非ProOPF目标值比对，不代表真实求解或物理校核",
            "total":len(results),"passed":passed,"accuracy":passed/len(results) if results else 0,
            "by_level":[{"level":k,**v,"accuracy":v["passed"]/v["total"]} for k,v in totals.items()],
            "precision":tp/predicted if predicted else (1 if not reference else 0),
            "recall":tp/reference if reference else 1,"hallucinations":hallucinations,
            "mean_ms":sum(r["elapsed_ms"] for r in results)/len(results) if results else 0,"cases":results}

def export_report(report, fmt):
    if fmt=="csv":
        stream=io.StringIO()
        writer=csv.writer(stream)
        writer.writerow(["编号","原始指令","期望层级","程序层级","规格比对通过","耗时ms","说明"])
        for c in report["cases"]:
            writer.writerow([c["id"],c["instruction"],c["expected_level"],c["actual_level"],c["passed"],c["elapsed_ms"],"；".join(c["errors"])])
        return "\ufeff"+stream.getvalue()
    lines=["# 建模工作台规格级测评报告","",report["basis"],"",
           f"通过 {report['passed']} / {report['total']}，准确率 {report['accuracy']:.1%}。",
           f"Δπ精确率 {report['precision']:.1%}，召回率 {report['recall']:.1%}，疑似幻觉记录 {report['hallucinations']}，平均耗时 {report['mean_ms']:.1f} ms。","",
           "疑似幻觉计数包含用于负例测试的E101，不能作为引擎真实部署错误率。","",
           "|层级|通过|总数|准确率|","|---|---:|---:|---:|"]
    lines += [f"|{x['level']}|{x['passed']}|{x['total']}|{x['accuracy']:.1%}|" for x in report["by_level"]]
    lines += ["","|用例|程序层级|通过|耗时ms|说明|","|---|---|---|---:|---|"]
    lines += [f"|{c['id']}|{c['actual_level']}|{'是' if c['passed'] else '否'}|{c['elapsed_ms']}|{'；'.join(c['errors']) or '符合参考规格'}|" for c in report["cases"]]
    return "\n".join(lines)
