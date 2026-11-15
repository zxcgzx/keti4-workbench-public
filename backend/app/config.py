import json
import os
from pathlib import Path
from uuid import uuid4
import yaml

ROOT = Path(__file__).resolve().parents[2]
# Optional local environment file. Values never leave the backend configuration.
_ENV_KEYS = {"LLM_MODE","LLM_BASE_URL","LLM_MODEL","LLM_API_KEY","METHOD_ENDPOINT","RISK_ENDPOINT",
             "SOLVER_MODE","SOLVER_CMD","SNAPSHOT_DIR","WORKBENCH_HOST","WORKBENCH_PORT",
             "PROOPF_BASE_URL","PROOPF_MODEL","PROOPF_API_KEY","TOPIC2_MODEL_ENDPOINT",
             "HALLUCINATION_ENDPOINT","REWARD_ENDPOINT"}
def _load_env_file(path, environ=None):
    """Load the allowlisted local settings without changing Windows path escaping."""
    path = Path(path)
    if not path.is_file():
        return
    environ = os.environ if environ is None else environ
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if key not in _ENV_KEYS:
                continue
            # Strip one whole-value pair, not the quotes of separate command arguments.
            # A single-quoted command can contain double-quoted executable/script paths.
            if (len(value) >= 2 and value[0] in ("\"", "'")
                    and value[-1] == value[0] and value[0] not in value[1:-1]):
                value = value[1:-1]
            environ.setdefault(key, value)

_load_env_file(ROOT / ".env")
RUNS_DIR = Path(os.getenv("WORKBENCH_RUNS_DIR", str(ROOT / "runs")))
STATIC_DIR = Path(__file__).parent / "static"

def read_yaml(name):
    path = ROOT / "config" / name
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}

def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

def dump_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)
