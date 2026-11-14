"""SQLite task/version records plus inspectable, version-bound artifacts."""
import json
import threading
from datetime import datetime, timezone
from uuid import uuid4
from sqlmodel import SQLModel, Field, Session, create_engine, select
from app.config import RUNS_DIR, dump_json

LOCK = threading.RLock()
_ENGINE = None

def now():
    return datetime.now(timezone.utc).isoformat()

class TaskRow(SQLModel, table=True):
    id: str = Field(primary_key=True)
    instruction: str
    engine_name: str = "demo"
    snapshot_id: str | None = None
    created_at: str
    payload: str = "{}"

class VersionRow(SQLModel, table=True):
    id: str = Field(primary_key=True)
    task_id: str = Field(index=True)
    version: int
    parent_version: int | None = None
    change_reason: str = ""
    created_at: str
    spec_json: str
    artifacts_json: str

def engine():
    global _ENGINE
    if _ENGINE is None:
        with LOCK:
            if _ENGINE is None:
                RUNS_DIR.mkdir(parents=True, exist_ok=True)
                _ENGINE = create_engine("sqlite:///" + str(RUNS_DIR / "workbench.sqlite"),
                                        connect_args={"check_same_thread": False, "timeout": 30})
                SQLModel.metadata.create_all(_ENGINE)
    return _ENGINE

def create_task(instruction, engine_name="demo", snapshot_id=None, settings=None):
    task_id = "task-" + uuid4().hex[:12]
    payload = {"settings": settings or {}, "logs": [], "draft": None, "parse": None,
               "check": None, "result": None, "metrics": None, "confirmed_version": None}
    row = TaskRow(id=task_id, instruction=instruction, engine_name=engine_name,
                  snapshot_id=snapshot_id, created_at=now(), payload=json.dumps(payload, ensure_ascii=False))
    with Session(engine()) as session:
        session.add(row)
        session.commit()
    log(task_id, "任务已创建；合成演示断面只读，等待解析。")
    return get_task(task_id)

def get_task(task_id):
    with Session(engine()) as session:
        row = session.get(TaskRow, task_id)
        if row is None:
            raise KeyError("任务不存在")
        return {"task_id": row.id, "instruction": row.instruction, "engine": row.engine_name,
                "snapshot_id": row.snapshot_id, "created_at": row.created_at, **json.loads(row.payload)}

def update_task(task_id, **changes):
    with LOCK, Session(engine()) as session:
        row = session.get(TaskRow, task_id)
        if row is None:
            raise KeyError("任务不存在")
        payload = json.loads(row.payload)
        payload.update(changes)
        row.payload = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        session.add(row)
        session.commit()
    return get_task(task_id)

def log(task_id, message):
    with LOCK:
        task = get_task(task_id)
        logs = task.get("logs", []) + [{"time": now(), "message": str(message)}]
        update_task(task_id, logs=logs[-2000:])
        folder = RUNS_DIR / task_id
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "log.txt").open("a", encoding="utf-8") as stream:
            stream.write(f"{logs[-1]['time']} {message}\n")

def list_versions(task_id):
    get_task(task_id)
    with Session(engine()) as session:
        rows = session.exec(select(VersionRow).where(VersionRow.task_id == task_id).order_by(VersionRow.version)).all()
        return [{"version": row.version, "parent_version": row.parent_version, "change_reason": row.change_reason,
                 "created_at": row.created_at, "status": "confirmed"} for row in rows]

def get_version(task_id, version):
    with Session(engine()) as session:
        row = session.get(VersionRow, f"{task_id}:{version}")
        if row is None:
            raise KeyError("规格版本不存在")
        return {"spec": json.loads(row.spec_json), "artifacts": json.loads(row.artifacts_json),
                "created_at": row.created_at}

def version_dir(task_id, version):
    get_task(task_id)
    version = int(version)
    if version < 1:
        raise ValueError("版本号必须为正整数")
    path = RUNS_DIR / task_id / f"v{version}"
    path.mkdir(parents=True, exist_ok=True)
    return path

def save_version(task_id, spec, artifacts):
    with LOCK:
        versions = list_versions(task_id)
        expected = max([v["version"] for v in versions], default=0) + 1
        spec = dict(spec, task_id=task_id, version=expected,
                    parent_version=versions[-1]["version"] if versions else None)
        row = VersionRow(id=f"{task_id}:{expected}", task_id=task_id, version=expected,
                         parent_version=spec["parent_version"], change_reason=spec.get("change_reason") or "用户确认规格",
                         created_at=now(), spec_json=json.dumps(spec, ensure_ascii=False),
                         artifacts_json=json.dumps(artifacts, ensure_ascii=False))
        folder = version_dir(task_id, expected)
        dump_json(folder / "spec.json", spec)
        (folder / "model.tex").write_text(artifacts.get("latex_text", ""), encoding="utf-8")
        (folder / "model.py").write_text(artifacts.get("code", ""), encoding="utf-8")
        with Session(engine()) as session:
            session.add(row)
            session.commit()
        update_task(task_id, draft=spec, confirmed_version=expected, result=None, metrics=None, check=None)
        log(task_id, f"规格 v{expected} 已确认，公式和代码已保存。")
        (folder / "log.txt").write_text("\n".join(x["time"]+" "+x["message"] for x in get_task(task_id)["logs"]), encoding="utf-8")
    return spec

def save_evidence(task_id, kind, payload, spec=None):
    with LOCK:
        task = get_task(task_id)
        bound = spec if spec is not None else task.get("draft")
        version = bound.get("version") if bound else None
        saved = None
        if version:
            try:
                saved = get_version(task_id, version)["spec"]
            except KeyError:
                pass
        if version and bound == saved:
            folder = version_dir(task_id, version)
        else:
            import hashlib
            digest = hashlib.sha256(json.dumps(bound,sort_keys=True).encode()).hexdigest()[:16]
            folder = RUNS_DIR / task_id / "drafts" / digest
        dump_json(folder / f"{kind}.json", payload)

def spec_diff(a, b):
    changes = []
    def walk(left, right, path=""):
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                p = f"{path}.{key}".lstrip(".")
                if key not in left:
                    changes.append({"path": p, "kind": "added", "before": None, "after": right[key]})
                elif key not in right:
                    changes.append({"path": p, "kind": "removed", "before": left[key], "after": None})
                else:
                    walk(left[key], right[key], p)
        elif left != right:
            changes.append({"path": path, "kind": "changed", "before": left, "after": right})
    walk(a, b)
    return changes
