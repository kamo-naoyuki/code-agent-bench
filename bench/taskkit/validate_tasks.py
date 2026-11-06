"""Run Q1-Q5 using only Harbor oracle/nop agents, never a model provider."""
from __future__ import annotations

import argparse
from collections import Counter
from collections import defaultdict
from datetime import datetime
import json
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[2]


def copy_task(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def create_task_snapshot(root: Path, names: list[str], stamp: str) -> Path:
    snapshot = root / "jobs" / f"p1-{stamp}-inputs"
    snapshot.mkdir(parents=True, exist_ok=False)
    for name in names:
        copy_task(root / "tasks/dev" / name, snapshot / name)
    return snapshot


def display_job_path(job: Path) -> str:
    try:
        return str(job.relative_to(ROOT))
    except ValueError:
        return str(job)


def check_job(job: Path, names: list[str], attempts: int, reward: int) -> dict:
    counts: Counter[str] = Counter()
    checksums: dict[str, set[str]] = defaultdict(set)
    errors = []
    for path in sorted(job.glob("*/result.json")):
        result = json.loads(path.read_text())
        name = result["task_name"].removeprefix("local/")
        counts[name] += 1
        checksums[name].add(result["task_checksum"])
        if result.get("exception_info"):
            errors.append(f"{path.parent.name}: {result['exception_info']['exception_type']}")
        actual = (result.get("verifier_result") or {}).get("rewards", {}).get("reward")
        if actual != reward:
            errors.append(f"{path.parent.name}: expected reward {reward}, got {actual}")
        if (path.parent / "agent/exit-code.txt").exists():
            errors.append(f"{path.parent.name}: oracle solution failed")
        manifest_path = path.parent / "artifacts/manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
        if not any(item["source"] == "/app" and item["status"] == "ok" for item in manifest):
            errors.append(f"{path.parent.name}: missing successful /app artifact")
        if any(item["status"] != "ok" for item in manifest):
            errors.append(f"{path.parent.name}: artifact collection failed")
        ctrf_path = path.parent / "verifier/ctrf.json"
        if not ctrf_path.exists():
            errors.append(f"{path.parent.name}: missing test report")
            continue
        summary = json.loads(ctrf_path.read_text())["results"]["summary"]
        if summary["tests"] == 0 or any(summary.get(key, 0) > 0 for key in ("skipped", "pending", "other")):
            errors.append(f"{path.parent.name}: empty/skipped tests")
        if reward == 0 and summary["failed"] == 0:
            errors.append(f"{path.parent.name}: nop has no failing test")
        if reward == 1 and summary["failed"] != 0:
            errors.append(f"{path.parent.name}: oracle has failing tests")
    expected = Counter({name: attempts for name in names})
    if counts != expected:
        errors.append(f"Trial counts differ: expected {dict(expected)}, got {dict(counts)}")
    for name, hashes in checksums.items():
        if len(hashes) != 1:
            errors.append(f"{name}: task checksum changed during validation")
    return {"job": display_job_path(job), "counts": dict(counts),
            "checksums": {name: sorted(hashes) for name, hashes in checksums.items()},
            "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle-runs", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--task", action="append", help="Task directory name; repeatable")
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args()
    if args.oracle_runs < 1 or args.concurrency < 1:
        parser.error("oracle-runs and concurrency must be positive")
    registry = json.loads((ROOT / "suites/dev-v1.json").read_text())
    all_names = [entry["name"] for entry in registry["tasks"]]
    names = args.task or all_names
    if len(set(names)) != len(names) or not set(names) <= set(all_names):
        parser.error("Tasks must be unique registered directory names")
    subprocess.run([sys.executable, str(ROOT / "bench/taskkit/sync_templates.py"), "--check"],
                   cwd=ROOT, check=True)
    for name in names:
        task = ROOT / "tasks/dev" / name
        config = tomllib.loads((task / "task.toml").read_text())
        metadata = config["metadata"]
        if metadata["split"] != "dev" or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", config["task"]["version"]):
            raise ValueError(f"{name}: invalid split or version")
    subprocess.run(["harbor", "lint", *(f"tasks/dev/{name}" for name in names),
                    "--extend-select", "root-agent", "--extend-select", "unpinned-image"],
                   cwd=ROOT, check=True)
    if args.static_only:
        return 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snapshot = create_task_snapshot(ROOT, names, stamp)
    reports = []
    stages = [
        ("nop", "nop", 1, 0, {}),
        ("alternative", "oracle", 1, 1, {"SOLUTION": "2"}),
        ("oracle", "oracle", args.oracle_runs, 1, {"SOLUTION": "1"}),
    ]
    for stage, agent, attempts, reward, env in stages:
        job_name = f"p1-{stamp}-{stage}"
        config = {
            "job_name": job_name,
            "n_attempts": attempts,
            "n_concurrent_trials": args.concurrency,
            "retry": {"max_retries": 0},
            "agents": [{"name": agent}],
            "environment": {"env": env},
            "tasks": [{"path": str(snapshot / name)} for name in names],
        }
        with tempfile.TemporaryDirectory(prefix="task-quality-") as directory:
            path = Path(directory) / "job.json"
            path.write_text(json.dumps(config))
            subprocess.run(["harbor", "run", "-c", str(path)], cwd=ROOT, check=True)
        report = check_job(ROOT / "jobs" / job_name, names, attempts, reward)
        if reports and report["checksums"] != reports[0]["checksums"]:
            report["errors"].append("Task checksums changed between validation stages")
        reports.append({"stage": stage, **report})
        output = ROOT / "reports" / f"phase1-validation-{stamp}.json"
        output.write_text(json.dumps({
            "suite": registry["name"], "version": registry["version"],
            "snapshot": str(snapshot.relative_to(ROOT)),
            "oracle_runs": args.oracle_runs, "tasks": names,
            "q5_satisfied": args.oracle_runs >= 10 and stage == "oracle" and not report["errors"],
            "q6": "pending independent human review and approved harbor check",
            "stages": reports,
        }, indent=2) + "\n")
        if report["errors"]:
            print("\n".join(report["errors"]), file=sys.stderr)
            print(f"Validation failed; report: {output}", file=sys.stderr)
            return 1
    print(f"Q1-Q4 passed. Q5 {'passed' if args.oracle_runs >= 10 else 'NOT complete (<10 runs)'}.")
    print(f"Q6 remains pending. Report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
