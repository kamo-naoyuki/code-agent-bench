"""Explicit Docker integration check: oracle interruption/resume plus nop baseline."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def command(*args: str) -> list[str]:
    return ["bash", "bench/run", *args]


def main() -> None:
    name = "p2-resume-" + uuid.uuid4().hex[:8]
    directory = ROOT / "jobs" / name
    args = command("--config", "configs/dev-oracle.json", "--job-name", name,
                   "--task", "smoke-fix-calc", "--task", "fix-pagination",
                   "--task", "feature-ttl-cache")
    # One concurrency slot guarantees remaining work when the first result is saved.
    config = json.loads((ROOT / "configs/dev-oracle.json").read_text())
    config["concurrency"] = 1
    config_path = ROOT / "jobs" / f"{name}-request.json"
    config_path.write_text(json.dumps(config))
    args[args.index("configs/dev-oracle.json")] = str(config_path)
    log_path = ROOT / "jobs" / f"{name}-driver.log"
    try:
        with log_path.open("w") as log:
            process = subprocess.Popen(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        first = None
        deadline = time.monotonic() + 600
        try:
            while time.monotonic() < deadline:
                results = list((directory / "harbor").glob("*/result.json"))
                complete = [p for p in results if json.loads(p.read_text()).get("exception_info") is None]
                if complete:
                    first = complete[0]
                    break
                if process.poll() is not None:
                    raise RuntimeError(f"Runner exited before first trial: {log_path.read_text()}")
                time.sleep(0.25)
            if first is None:
                raise TimeoutError("No completed trial within 600 seconds")
            first_hash = hashlib.sha256(first.read_bytes()).hexdigest()
            os.kill(process.pid, signal.SIGTERM)
            assert process.wait(timeout=90) == 130
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        interrupted = json.loads((directory / "manifest.json").read_text())
        assert interrupted["state"] == "interrupted"
        subprocess.run(command("--resume", name), cwd=ROOT, check=True, timeout=600)
        complete = json.loads((directory / "manifest.json").read_text())
        assert complete["state"] == "completed"
        assert complete["summary"] == {"n_trials": 3, "successes": 3, "issues": []}
        assert hashlib.sha256(first.read_bytes()).hexdigest() == first_hash
        before = {p.parent.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (directory / "harbor").glob("*/result.json")}
        subprocess.run(command("--resume", name), cwd=ROOT, check=True, timeout=60)
        after = {p.parent.name: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (directory / "harbor").glob("*/result.json")}
        assert before == after
        nop_name = name + "-nop"
        subprocess.run(command("--config", "configs/dev-nop.json", "--job-name", nop_name,
                               "--task", "smoke-fix-calc"), cwd=ROOT, check=True, timeout=300)
        nop = json.loads((ROOT / "jobs" / nop_name / "manifest.json").read_text())
        assert nop["state"] == "completed" and nop["summary"]["successes"] == 0
        print(f"PASS: interrupted/resumed oracle and idempotent resume: {directory}")
        print(f"PASS: nop reward zero without pipeline error: {ROOT / 'jobs' / nop_name}")
    finally:
        config_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
