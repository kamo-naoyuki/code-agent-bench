"""Versioned, frozen-input Harbor runs with durable manifests and explicit resume."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tomllib
from typing import Iterator
import uuid

from harbor.models.job.config import JobConfig
from harbor.models.task.task import Task

from bench.taskkit.validate_tasks import copy_task

ROOT = Path(__file__).resolve().parents[1]
AGENTS = {
    "oracle": "oracle",
    "nop": "nop",
    "copilot-host": "bench.agents.copilot_host:CopilotHostAgent",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ValueError(f"Symlinks are not supported in frozen inputs: {path}")
        if path.is_file():
            name = path.relative_to(root).as_posix().encode()
            digest.update(len(name).to_bytes(8, "big") + name)
            digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def observation(command: list[str]) -> dict[str, object]:
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"value": None, "error": f"{type(error).__name__}: {error}"}
    if result.returncode:
        return {"value": None, "error": result.stderr.strip() or f"exit {result.returncode}"}
    return {"value": result.stdout.strip(), "error": None}


def load_config(path: Path) -> tuple[dict, dict]:
    config = json.loads(path.read_text())
    allowed = {"schema_version", "suite", "agent", "model", "attempts", "concurrency"}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError("Unknown configuration fields; credentials and arbitrary Harbor options are not accepted")
    if config.get("schema_version") != 1 or config.get("agent") not in AGENTS:
        raise ValueError("Expected schema_version=1 and agent oracle, nop, or copilot-host")
    for name in ("attempts", "concurrency"):
        if type(config.get(name)) is not int or config[name] < 1:
            raise ValueError(f"{name} must be a positive integer")
    if config["concurrency"] > 6:
        raise ValueError("concurrency must not exceed 6")
    model = config.get("model")
    if config["agent"] == "copilot-host":
        if not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9._/-]+", model):
            raise ValueError("copilot-host requires an explicit model ID")
    elif model is not None:
        raise ValueError("oracle/nop do not accept a model")
    if not isinstance(config.get("suite"), str):
        raise ValueError("suite must be a path under suites/")
    suite_path = (ROOT / config["suite"]).resolve()
    if not suite_path.is_relative_to(ROOT / "suites"):
        raise ValueError("Suite must be a registered file under suites/")
    suite = json.loads(suite_path.read_text())
    names = [entry["name"] for entry in suite["tasks"]]
    if not names or len(set(names)) != len(names):
        raise ValueError("Suite must have unique nonempty tasks")
    for name in names:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
            raise ValueError(f"Invalid task directory: {name}")
    return config, suite


@contextmanager
def locked(directory: Path) -> Iterator[None]:
    with (directory / ".pipeline.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"Another process is using {directory}") from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def prepare(directory: Path, config: dict, suite: dict, names: list[str]) -> dict:
    write_json(directory / "request.json", config)
    write_json(directory / "suite.json", suite)
    tasks = []
    for name in names:
        source = ROOT / "tasks/dev" / name
        tree_digest(source)
        frozen = directory / "inputs" / name
        copy_task(source, frozen)
        task = tomllib.loads((frozen / "task.toml").read_text())
        from_lines = []
        for dockerfile in (frozen / "environment/Dockerfile", frozen / "tests/Dockerfile"):
            from_lines.extend(line.strip() for line in dockerfile.read_text().splitlines()
                              if line.strip().upper().startswith("FROM "))
        tasks.append({
            "directory": name, "name": task["task"]["name"],
            "version": task["task"]["version"], "sha256": tree_digest(frozen),
            "harbor_checksum": Task(frozen).checksum,
            "base_images": from_lines,
        })
    effective = {
        "job_name": "harbor", "jobs_dir": str(directory),
        "tasks": [{"path": str(directory / "inputs" / name)} for name in names],
        "agents": [{"name": AGENTS[config["agent"]], "model_name": config.get("model")}],
        "n_attempts": config["attempts"], "n_concurrent_trials": config["concurrency"],
        "retry": {"max_retries": 0},
    }
    JobConfig.model_validate(effective)
    write_json(directory / "effective.json", effective)
    code = {
        relative: sha256(ROOT / relative)
        for relative in (
            "bench/pipeline.py", "bench/taskkit/validate_tasks.py",
            "bench/agents/copilot_host.py", "bench/agents/sandbox_mcp.py",
        )
    }
    return {
        "schema_version": 1, "created_at": now(), "state": "prepared",
        "suite": {"name": suite["name"], "version": suite["version"]},
        "tasks": tasks, "agent": config["agent"], "model_requested": config.get("model"),
        "model_snapshot": None, "model_snapshot_note": "No immutable provider snapshot is verified",
        "attempts": config["attempts"], "concurrency": config["concurrency"],
        "expected_trials": len(names) * config["attempts"],
        "effective_sha256": sha256(directory / "effective.json"),
        "request_sha256": sha256(directory / "request.json"),
        "suite_sha256": sha256(directory / "suite.json"),
        "code_sha256": code,
        "versions": {
            "harbor": observation(["harbor", "--version"]),
            "agent_cli": observation(["copilot", "--version"]) if config["agent"] == "copilot-host" else None,
            "git_commit": observation(["git", "rev-parse", "HEAD"]),
        },
        "host": {"os": platform.platform(), "kernel": platform.release(),
                 "python": platform.python_version(),
                 "docker": observation(["docker", "version", "--format", "{{.Server.Version}}"])},
        "telemetry": "off", "budget": None,
        "budget_note": "No hard billing cap. Paid run/resume requires explicit confirmation.",
        "price_table": None, "gateway": None, "executions": [],
    }


def verify_inputs(directory: Path, manifest: dict) -> None:
    for file, key in (("effective.json", "effective_sha256"), ("request.json", "request_sha256"),
                      ("suite.json", "suite_sha256")):
        if sha256(directory / file) != manifest[key]:
            raise ValueError(f"Saved configuration changed: {file}")
    for relative, expected in manifest["code_sha256"].items():
        if sha256(ROOT / relative) != expected:
            raise ValueError(f"Runner/adapter source changed since preparation: {relative}")
    for task in manifest["tasks"]:
        frozen = directory / "inputs" / task["directory"]
        if tree_digest(frozen) != task["sha256"]:
            raise ValueError(f"Frozen task changed: {task['directory']}")
    version = observation(["harbor", "--version"])
    if version != manifest["versions"]["harbor"]:
        raise ValueError("Harbor version changed; do not resume with another version")
    if manifest["agent"] == "copilot-host":
        if observation(["copilot", "--version"]) != manifest["versions"]["agent_cli"]:
            raise ValueError("Copilot version changed; cannot resume the same run")
    saved = directory / "harbor/config.json"
    if saved.exists():
        actual = JobConfig.model_validate_json(saved.read_text())
        expected = JobConfig.model_validate_json((directory / "effective.json").read_text())
        if actual != expected:
            raise ValueError("Harbor's saved config differs from prepared configuration")


def archive_interrupted(directory: Path) -> list[str]:
    moved = []
    harbor = directory / "harbor"
    if not harbor.exists():
        return moved
    for trial in sorted(harbor.iterdir()):
        if not trial.is_dir():
            continue
        result = trial / "result.json"
        interrupted = not result.exists()
        if result.exists():
            record = json.loads(result.read_text())
            interrupted = (record.get("exception_info") or {}).get("exception_type") == "CancelledError"
        if interrupted:
            archive = directory / "interrupted" / uuid.uuid4().hex
            archive.mkdir(parents=True)
            shutil.move(str(trial), str(archive / trial.name))
            moved.append(str((archive / trial.name).relative_to(directory)))
    return moved


def summarize(directory: Path, manifest: dict) -> dict:
    rows = []
    issues = []
    counts: Counter[str] = Counter()
    tasks = {task["name"]: task for task in manifest["tasks"]}
    for path in sorted((directory / "harbor").glob("*/result.json")):
        result = json.loads(path.read_text())
        name = result["task_name"]
        counts[name] += 1
        error = (result.get("exception_info") or {}).get("exception_type")
        reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        if name not in tasks or result["task_checksum"] != tasks[name]["harbor_checksum"]:
            issues.append(f"{path.parent.name}: unexpected task/checksum")
        artifacts = path.parent / "artifacts/manifest.json"
        artifact_rows = json.loads(artifacts.read_text()) if artifacts.exists() else []
        if not any(item["source"] == "/app" and item["status"] == "ok" for item in artifact_rows):
            issues.append(f"{path.parent.name}: missing /app artifacts")
        if any(item["status"] != "ok" for item in artifact_rows):
            issues.append(f"{path.parent.name}: artifact collection failure")
        if error or reward not in (0, 1):
            issues.append(f"{path.parent.name}: exception={error}, reward={reward}")
        if (path.parent / "agent/exit-code.txt").exists():
            issues.append(f"{path.parent.name}: oracle command failed")
        usage = result.get("agent_result") or {}
        if manifest["agent"] == "copilot-host":
            if not (path.parent / "agent/trajectory.json").exists() or not usage.get("n_input_tokens"):
                issues.append(f"{path.parent.name}: missing trajectory/usage")
        rows.append({
            "trial": path.parent.name, "task": name, "reward": reward, "exception": error,
            "agent": result.get("agent_info"), "usage": usage,
            "started_at": result.get("started_at"), "finished_at": result.get("finished_at"),
        })
    expected = Counter({task["name"]: manifest["attempts"] for task in manifest["tasks"]})
    if counts != expected:
        issues.append(f"Expected trials {dict(expected)}, found {dict(counts)}")
    return {"trials": rows, "issues": issues, "n_trials": len(rows),
            "successes": sum(row["reward"] == 1 for row in rows)}


def execute(command: list[str], log: Path) -> tuple[int, bool]:
    interrupted = False
    with log.open("a") as stream:
        process = subprocess.Popen(command, cwd=ROOT, start_new_session=True,
                                   stdout=stream, stderr=subprocess.STDOUT)
    def stop(signum, frame):
        nonlocal interrupted
        interrupted = True
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGINT)
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        while True:
            try:
                return process.wait(timeout=45 if interrupted else 1), interrupted
            except subprocess.TimeoutExpired:
                if interrupted:
                    os.killpg(process.pid, signal.SIGKILL)
                    return process.wait(), True
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run_saved(directory: Path, manifest: dict, confirm: bool) -> int:
    if manifest["agent"] == "copilot-host" and not confirm:
        raise ValueError("Paid run/resume requires --confirm-spend; no hard cost cap is implemented")
    verify_inputs(directory, manifest)
    if manifest["state"] == "completed":
        try:
            summary = summarize(directory, manifest)
        except (OSError, ValueError, KeyError, TypeError) as error:
            execution.update(finished_at=now(), returncode=code,
                             interrupted=interrupted, error=f"{type(error).__name__}: {error}")
            manifest["state"] = "interrupted" if interrupted else "failed"
            write_json(directory / "manifest.json", manifest)
            raise
        if summary["issues"]:
            raise ValueError("Completed run evidence is incomplete or altered: " + "; ".join(summary["issues"]))
        print(f"Already completed; no trials rerun: {directory}")
        return 0
    archived = archive_interrupted(directory)
    command = (["harbor", "job", "resume", "--job-path", str(directory / "harbor")]
               if (directory / "harbor/config.json").exists()
               else ["harbor", "run", "-c", str(directory / "effective.json")])
    execution = {"started_at": now(), "command": command, "archived_interrupted": archived}
    manifest["executions"].append(execution)
    manifest["state"] = "running"
    write_json(directory / "manifest.json", manifest)
    try:
        code, interrupted = execute(command, directory / "runner.log")
    except OSError as error:
        execution.update(finished_at=now(), error=f"{type(error).__name__}: {error}")
        manifest["state"] = "failed"
        write_json(directory / "manifest.json", manifest)
        raise
    summary = summarize(directory, manifest)
    write_json(directory / "summary.json", summary)
    execution.update(finished_at=now(), returncode=code, interrupted=interrupted)
    manifest["state"] = ("interrupted" if interrupted else "failed" if code else
                         "completed_with_errors" if summary["issues"] else "completed")
    manifest["finished_at"] = now()
    manifest["summary"] = {"n_trials": summary["n_trials"], "successes": summary["successes"],
                           "issues": summary["issues"]}
    write_json(directory / "manifest.json", manifest)
    print(f"{manifest['state']}: {summary['n_trials']}/{manifest['expected_trials']} trials; "
          f"successes={summary['successes']}; results={directory}", flush=True)
    for issue in summary["issues"]:
        print(issue, file=sys.stderr)
    return 130 if interrupted else 1 if code or summary["issues"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path)
    source.add_argument("--resume", help="Existing run name under jobs/")
    parser.add_argument("--job-name")
    parser.add_argument("--task", action="append", help="Registered directory name; repeatable")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-spend", action="store_true")
    args = parser.parse_args()
    os.environ["HARBOR_TELEMETRY"] = "off"
    if args.resume:
        if args.job_name or args.task or args.dry_run:
            parser.error("--resume cannot be combined with job-name, task or dry-run")
        name = args.resume
    else:
        name = args.job_name or f"bench-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}", name):
        parser.error("Invalid job name")
    directory = ROOT / "jobs" / name
    if args.resume:
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("Resume requires an existing nonsymlink run directory under jobs/")
        with locked(directory):
            return run_saved(directory, json.loads((directory / "manifest.json").read_text()),
                             args.confirm_spend)
    config, suite = load_config(args.config)
    names = args.task or [entry["name"] for entry in suite["tasks"]]
    if len(set(names)) != len(names) or not set(names) <= {entry["name"] for entry in suite["tasks"]}:
        parser.error("--task values must be unique registered directory names")
    total = len(names) * config["attempts"]
    if config["agent"] == "copilot-host" and total > 12:
        parser.error("Paid runs are limited to 12 trials")
    if args.dry_run:
        print(json.dumps({"job_name": name, "configuration": config, "tasks": names,
                          "expected_trials": total, "model_called": False}, indent=2))
        return 0
    if config["agent"] == "copilot-host" and not args.confirm_spend:
        parser.error("Paid execution requires --confirm-spend")
    if directory.exists() or directory.is_symlink():
        raise FileExistsError(f"Run exists; use --resume: {directory}")
    directory.mkdir(parents=True)
    with locked(directory):
        manifest = prepare(directory, config, suite, names)
        write_json(directory / "manifest.json", manifest)
        print(f"Prepared {total} trials; logs: {directory / 'runner.log'}", flush=True)
        return run_saved(directory, manifest, args.confirm_spend)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"bench run: {error}", file=sys.stderr)
        raise SystemExit(1)
