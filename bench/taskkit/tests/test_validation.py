import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("validate", ROOT / "bench/taskkit/validate_tasks.py")
assert spec is not None and spec.loader is not None
validate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate)


class ValidationTests(unittest.TestCase):
    def test_snapshot_creation_makes_jobs_parent_in_clean_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "tasks/dev/example"
            task.mkdir(parents=True)
            (task / "task.toml").write_text("version = 1")
            snapshot = validate.create_task_snapshot(root, ["example"], "stamp")
            self.assertEqual((snapshot / "example/task.toml").read_text(), "version = 1")
            self.assertTrue((root / "jobs").is_dir())
            self.assertEqual(snapshot.parent, root / "jobs")

    def test_snapshot_ignores_cache_changes_and_keeps_source_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "task"
            source.mkdir()
            (source / "code.py").write_text("value = 1")
            validate.copy_task(source, root / "first")
            (source / "__pycache__").mkdir()
            (source / "__pycache__/code.pyc").write_bytes(b"cache")
            validate.copy_task(source, root / "second")
            self.assertEqual(list((root / "second").iterdir()), [root / "second/code.py"])
            self.assertEqual((root / "first/code.py").read_bytes(),
                             (root / "second/code.py").read_bytes())
            (source / "code.py").write_text("value = 2")
            self.assertEqual((root / "first/code.py").read_text(), "value = 1")

    def test_incomplete_or_broken_trials_cannot_pass(self):
        with tempfile.TemporaryDirectory(prefix="quality-unit-") as tmp:
            job = Path(tmp)
            self.assertTrue(validate.check_job(job, ["example"], 1, 1)["errors"])
            trial = job / "trial"
            (trial / "verifier").mkdir(parents=True)
            (trial / "artifacts").mkdir()
            result = {"task_name": "local/example", "task_checksum": "abc", "exception_info": None,
                      "verifier_result": {"rewards": {"reward": 1}}}
            (trial / "result.json").write_text(json.dumps(result))
            self.assertTrue(validate.check_job(job, ["example"], 1, 1)["errors"])
            (trial / "artifacts/manifest.json").write_text(json.dumps([
                {"source": "/app", "status": "ok"}]))
            report = {"results": {"summary": {"tests": 3, "failed": 0, "skipped": 0}}}
            (trial / "verifier/ctrf.json").write_text(json.dumps(report))
            self.assertEqual(validate.check_job(job, ["example"], 1, 1)["errors"], [])
            result["exception_info"] = {"exception_type": "VerifierTimeoutError"}
            (trial / "result.json").write_text(json.dumps(result))
            self.assertTrue(validate.check_job(job, ["example"], 1, 1)["errors"])

    def test_nop_requires_failed_tests_and_exact_trial_count(self):
        with tempfile.TemporaryDirectory(prefix="quality-unit-") as tmp:
            job = Path(tmp)
            trial = job / "trial"
            (trial / "verifier").mkdir(parents=True)
            (trial / "artifacts").mkdir()
            (trial / "result.json").write_text(json.dumps({
                "task_name": "local/example", "task_checksum": "abc", "exception_info": None,
                "verifier_result": {"rewards": {"reward": 0}}}))
            (trial / "artifacts/manifest.json").write_text(json.dumps([
                {"source": "/app", "status": "ok"}]))
            report = {"results": {"summary": {"tests": 3, "failed": 0, "skipped": 0}}}
            path = trial / "verifier/ctrf.json"
            path.write_text(json.dumps(report))
            self.assertTrue(validate.check_job(job, ["example"], 1, 0)["errors"])
            report["results"]["summary"]["failed"] = 1
            path.write_text(json.dumps(report))
            self.assertEqual(validate.check_job(job, ["example"], 1, 0)["errors"], [])
            self.assertTrue(validate.check_job(job, ["example"], 2, 0)["errors"])

    def test_different_snapshots_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="quality-unit-") as tmp:
            job = Path(tmp)
            for index in range(2):
                trial = job / str(index)
                (trial / "verifier").mkdir(parents=True)
                (trial / "artifacts").mkdir()
                (trial / "result.json").write_text(json.dumps({
                    "task_name": "local/example", "task_checksum": str(index),
                    "exception_info": None, "verifier_result": {"rewards": {"reward": 1}}}))
                (trial / "artifacts/manifest.json").write_text(json.dumps([
                    {"source": "/app", "status": "ok"}]))
                (trial / "verifier/ctrf.json").write_text(json.dumps({
                    "results": {"summary": {"tests": 1, "failed": 0}}}))
            errors = validate.check_job(job, ["example"], 2, 1)["errors"]
            self.assertTrue(any("checksum changed" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
