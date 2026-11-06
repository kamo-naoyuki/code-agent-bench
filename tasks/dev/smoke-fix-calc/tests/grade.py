"""Trusted verifier entry point; copied into each task's verifier image."""
from __future__ import annotations

import json
import errno
import os
from pathlib import Path
import shutil
import stat
import subprocess


class SubmissionError(ValueError):
    pass


def quarantine(source: Path, destination: Path, allowed: list[str]) -> None:
    destination.mkdir(mode=0o755)
    for name in allowed:
        if Path(name).name != name or name in (".", ".."):
            raise SubmissionError(f"Only top-level source files are allowed: {name!r}")
        path = source / name
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            continue
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise SubmissionError(f"Symlink is not allowed: {name}") from error
            raise
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1_000_000:
                raise SubmissionError(f"Invalid submitted file: {name}")
            content = stream.read(1_000_001)
            if len(content) > 1_000_000:
                raise SubmissionError(f"Submitted file exceeds size limit: {name}")
        target = destination / name
        target.write_bytes(content)
        target.chmod(0o644)


def main() -> int:
    logs = Path("/logs/verifier")
    logs.mkdir(parents=True, exist_ok=True)
    logs.parent.chmod(0o755)
    logs.chmod(0o755)
    contract = json.loads(Path("/tests/contract.json").read_text())
    try:
        quarantine(Path("/app"), Path("/submission"), contract["allowed_files"])
    except SubmissionError as error:
        (logs / "reward.txt").write_text("0\n")
        (logs / "quarantine-error.txt").write_text(f"{type(error).__name__}: {error}\n")
        print(f"Submission rejected: {error}", flush=True)
        return 0
    subprocess.run(["install", "-d", "-o", "grader", "-g", "grader", "/home/grader/out"], check=True)
    writable = subprocess.run(["runuser", "-u", "grader", "--", "test", "-w", str(logs)],
                              check=False)
    if writable.returncode != 1:
        raise RuntimeError("Verifier logs must not be writable by the submission user")
    result = subprocess.run(
        [
            "runuser", "-u", "grader", "--", "env", "-i",
            "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/home/grader",
            "PYTHONDONTWRITEBYTECODE=1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            "python", "-I", "-m", "pytest", "-p", "ctrf",
            "-p", "no:cacheprovider", "--rootdir=/tests",
            "--ctrf", "/home/grader/out/ctrf.json", "/tests/test_hidden.py", "-rA",
        ],
        cwd="/home/grader/out",
        check=False,
    )
    report = Path("/home/grader/out/ctrf.json")
    if report.is_file():
        shutil.copyfile(report, logs / "ctrf.json")
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Verifier infrastructure failed: pytest exit {result.returncode}")
    (logs / "reward.txt").write_text("1\n" if result.returncode == 0 else "0\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
