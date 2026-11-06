from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench import pipeline


class PipelineTests(unittest.TestCase):
    @contextmanager
    def workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "jobs").mkdir()
            (root / "suites").mkdir()
            (root / "suites/dev.json").write_text(json.dumps({
                "name": "dev", "version": "1.0.0", "tasks": [{"name": "example"}]}))
            config = {"schema_version": 1, "suite": "suites/dev.json", "agent": "oracle",
                      "attempts": 1, "concurrency": 1}
            (root / "config.json").write_text(json.dumps(config))
            with patch.object(pipeline, "ROOT", root):
                yield root, config

    def test_strict_config_and_provider_confirmation(self):
        with self.workspace() as (root, config):
            pipeline.load_config(root / "config.json")
            for changes in ({"api_key": "do-not-store"}, {"agent": "unknown"},
                            {"attempts": True}, {"attempts": 0}, {"concurrency": 7},
                            {"model": "unneeded"}, {"suite": "../elsewhere.json"}):
                (root / "config.json").write_text(json.dumps(config | changes))
                with self.assertRaises((ValueError, KeyError)):
                    pipeline.load_config(root / "config.json")
            config.update(agent="copilot-host", model="claude-haiku-4.5")
            (root / "config.json").write_text(json.dumps(config))
            pipeline.load_config(root / "config.json")
            with self.assertRaisesRegex(ValueError, "confirm-spend"):
                pipeline.run_saved(root / "jobs", {"agent": "copilot-host"}, False)

    def test_digest_ignores_cache_but_rejects_symlink_and_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source.py").write_text("value = 1")
            original = pipeline.tree_digest(root)
            (root / "__pycache__").mkdir()
            (root / "__pycache__/source.pyc").write_text("bytecode")
            self.assertEqual(pipeline.tree_digest(root), original)
            (root / "source.py").write_text("value = 2")
            self.assertNotEqual(pipeline.tree_digest(root), original)
            (root / "link").symlink_to(root / "source.py")
            with self.assertRaisesRegex(ValueError, "Symlinks"):
                pipeline.tree_digest(root)

    def test_lock_excludes_another_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with pipeline.locked(root):
                with self.assertRaisesRegex(RuntimeError, "Another process"):
                    with pipeline.locked(root):
                        pass

    def test_atomic_json_and_tamper_checks(self):
        with self.workspace() as (root, config):
            directory = root / "jobs/test"
            directory.mkdir()
            for name in ("effective", "request", "suite"):
                pipeline.write_json(directory / f"{name}.json", {"example": 1})
            manifest = {
                "tasks": [], "code_sha256": {}, "agent": "oracle",
                "versions": {"harbor": {"value": "0.25.0", "error": None}},
                **{f"{name}_sha256": pipeline.sha256(directory / f"{name}.json")
                   for name in ("effective", "request", "suite")},
            }
            with patch.object(pipeline, "observation", return_value={"value": "0.25.0", "error": None}):
                pipeline.verify_inputs(directory, manifest)
                (directory / "effective.json").write_text("{}")
                with self.assertRaisesRegex(ValueError, "configuration changed"):
                    pipeline.verify_inputs(directory, manifest)
            self.assertFalse((directory / "request.json.tmp").exists())

    def test_archive_preserves_completed_and_failed_trials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("complete", "failed", "cancelled", "partial"):
                trial = root / "harbor" / name
                trial.mkdir(parents=True)
                (trial / "log.txt").write_text(name)
                if name != "partial":
                    error = (None if name == "complete" else
                             {"exception_type": "CancelledError" if name == "cancelled" else "AuthenticationError"})
                    (trial / "result.json").write_text(json.dumps({"exception_info": error}))
            moved = pipeline.archive_interrupted(root)
            self.assertEqual(len(moved), 2)
            self.assertEqual({p.name for p in (root / "harbor").iterdir()}, {"complete", "failed"})
            self.assertEqual({(root / p / "log.txt").read_text() for p in moved}, {"cancelled", "partial"})

    def test_summary_does_not_confuse_zero_reward_with_infrastructure_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trial = root / "harbor/trial"
            (trial / "artifacts").mkdir(parents=True)
            result = {
                "task_name": "local/example", "task_checksum": "abc",
                "exception_info": None, "verifier_result": {"rewards": {"reward": 0}},
            }
            (trial / "result.json").write_text(json.dumps(result))
            (trial / "artifacts/manifest.json").write_text(json.dumps([
                {"source": "/app", "status": "ok"}]))
            manifest = {"tasks": [{"name": "local/example", "harbor_checksum": "abc"}],
                        "attempts": 1, "agent": "nop"}
            self.assertEqual(pipeline.summarize(root, manifest)["issues"], [])
            result["exception_info"] = {"exception_type": "AuthenticationError"}
            (trial / "result.json").write_text(json.dumps(result))
            self.assertTrue(pipeline.summarize(root, manifest)["issues"])
            result["exception_info"] = None
            result["task_checksum"] = "changed"
            (trial / "result.json").write_text(json.dumps(result))
            self.assertTrue(pipeline.summarize(root, manifest)["issues"])

    def test_completed_resume_is_idempotent_and_no_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {"agent": "oracle", "state": "completed"}
            summary = {"issues": [], "n_trials": 1, "successes": 1}
            with patch.object(pipeline, "verify_inputs"), patch.object(
                pipeline, "summarize", return_value=summary
            ), patch.object(pipeline, "execute") as execute:
                self.assertEqual(pipeline.run_saved(root, manifest, False), 0)
                execute.assert_not_called()

    def test_interruption_is_durable_and_incomplete_counts_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {"agent": "oracle", "state": "prepared", "executions": [],
                        "expected_trials": 2}
            summary = {"issues": ["Expected 2 trials"], "n_trials": 1, "successes": 1}
            with patch.object(pipeline, "verify_inputs"), patch.object(
                pipeline, "archive_interrupted", return_value=[]
            ), patch.object(pipeline, "execute", return_value=(-2, True)), patch.object(
                pipeline, "summarize", return_value=summary
            ):
                self.assertEqual(pipeline.run_saved(root, manifest, False), 130)
            saved = json.loads((root / "manifest.json").read_text())
            self.assertEqual(saved["state"], "interrupted")
            self.assertTrue(saved["executions"][0]["interrupted"])
            self.assertEqual(json.loads((root / "summary.json").read_text()), summary)


if __name__ == "__main__":
    unittest.main()
