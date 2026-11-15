"""Environment-to-command regression tests; no external solver or child process runs."""
import io
import json
from pathlib import Path
import time

import pytest

from app import config
from app.data import grid as grid_module
from app.solver import service


def test_local_env_keeps_existing_settings_and_only_unwraps_whole_values(tmp_path):
    env_file = tmp_path / "settings.env"
    env_file.write_text(
        "# Local configuration fixture; these are not credentials.\n"
        "LLM_MODE=demo\n"
        "LLM_MODEL='Qwen'\n"
        'LLM_BASE_URL="http://127.0.0.1:8001/v1"\n'
        "LLM_API_KEY=''\n"
        'WORKBENCH_HOST="mismatched\'\n'
        "UNRECOGNIZED_SETTING=ignored\n",
        encoding="utf-8",
    )
    environ = {"LLM_MODE": "already-set"}
    config._load_env_file(env_file, environ)
    assert environ == {
        "LLM_MODE": "already-set",
        "LLM_MODEL": "Qwen",
        "LLM_BASE_URL": "http://127.0.0.1:8001/v1",
        "LLM_API_KEY": "",
        "WORKBENCH_HOST": '"mismatched\'',
    }


@pytest.mark.parametrize("whole_value_quotes", [False, True], ids=["argument-quotes", "outer-single-quotes"])
@pytest.mark.parametrize("absolute_snapshot_dir", [False, True], ids=["relative-snapshots", "absolute-snapshots"])
def test_env_command_preserves_spaced_paths_and_uses_grid_snapshot_directory(
    monkeypatch, tmp_path, whole_value_quotes, absolute_snapshot_dir
):
    root = tmp_path / "workbench with spaces"
    root.mkdir()
    configured_dir = (
        str(tmp_path / "outside workbench" / "snapshot inputs")
        if absolute_snapshot_dir else "custom inputs/snapshot collection"
    )
    expected_dir = Path(configured_dir) if absolute_snapshot_dir else root / configured_dir
    expected_dir.mkdir(parents=True)
    executable = tmp_path / "Solver environment" / "python.exe"
    adapter = tmp_path / "905 project" / "adapter with spaces.py"
    command = (
        f'"{executable}" "{adapter}" --spec "{{spec}}" '
        '--snapshots "{snapshots}" --legacy-dir "{dir}" --out "{out}"'
    )
    value = f"'{command}'" if whole_value_quotes else command
    env_file = tmp_path / "integration.env"
    env_file.write_text(
        f"SOLVER_CMD={value}\nSNAPSHOT_DIR='{configured_dir}'\n",
        encoding="utf-8",
    )
    environ = {}
    config._load_env_file(env_file, environ)
    assert environ["SOLVER_CMD"] == command
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(grid_module, "ROOT", root)
    monkeypatch.setattr(service, "ROOT", root)
    monkeypatch.setattr(service, "JOBS", {})
    version_dir = tmp_path / "runs with spaces" / "configuration-test" / "v1"
    monkeypatch.setattr(service.store, "version_dir", lambda task_id, version: version_dir)
    monkeypatch.setattr(service.store, "log", lambda *args: None)
    monkeypatch.setattr(
        service, "_store_result",
        lambda task_id, version, result, source: dict(result, source=source, version=version),
    )
    captured = []

    class ProcessDouble:
        def __init__(self):
            self.stdout = io.StringIO("Configuration test only; no optimizer ran.\n")

        def wait(self, timeout=None):
            return 0

    def capture_popen(args, **kwargs):
        # Check the actual argv passed to the subprocess boundary, including both aliases.
        assert args[:2] == [str(executable), str(adapter)]
        options = dict(zip(args[2::2], args[3::2]))
        assert set(options) == {"--spec", "--snapshots", "--legacy-dir", "--out"}
        assert options["--snapshots"] == str(expected_dir)
        assert options["--legacy-dir"] == str(expected_dir)
        assert options["--snapshots"] == str(grid_module.snapshot_dir())
        assert kwargs["cwd"] == root
        assert kwargs["shell"] is False
        spec_path, out_path = Path(options["--spec"]), Path(options["--out"])
        assert spec_path.parent == out_path.parent
        assert spec_path.parent.parent == version_dir / "solver_jobs"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        assert spec["version"] == 1
        out_path.write_text(json.dumps({
            "status": "feasible", "periods": spec["omega"]["periods"],
            "solver": "configuration-test-double",
            "messages": ["Test transport fixture only; no power-flow result is claimed."],
        }), encoding="utf-8")
        captured.append(args)
        return ProcessDouble()

    monkeypatch.setattr(service.subprocess, "Popen", capture_popen)
    spec = {"version": 1, "omega": {"periods": 1}, "solver_req": {"time_limit_s": 5}}
    job_id = service.start_job(
        "configuration-test", spec, {"bus": {}, "gen": {}, "branch": {}}, "external"
    )["job_id"]
    job_file = version_dir / "solver_jobs" / job_id / "job.json"
    deadline = time.monotonic() + 5
    while not job_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert job_file.exists(), service.get_job(job_id)
    job = service.get_job(job_id)
    assert job["status"] == "completed", job
    assert len(captured) == 1
    assert job["result"]["source"] == "external"
    assert job["result"]["solver"] == "configuration-test-double"
