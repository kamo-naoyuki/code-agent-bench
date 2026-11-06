import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("grade", ROOT / "bench/taskkit/grade.py")
assert spec is not None and spec.loader is not None
grade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grade)


class QuarantineTests(unittest.TestCase):
    def test_only_allowed_files_and_missing_deletions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "app"
            source.mkdir()
            (source / "code.py").write_text("value = 1")
            (source / "conftest.py").write_text("raise RuntimeError")
            grade.quarantine(source, root / "out", ["code.py", "deleted.py"])
            self.assertEqual([p.name for p in (root / "out").iterdir()], ["code.py"])
            self.assertEqual((root / "out/code.py").read_text(), "value = 1")

    def test_rejects_symlink_and_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "app"
            source.mkdir()
            (source / "code.py").symlink_to("/etc/passwd")
            with self.assertRaises(grade.SubmissionError):
                grade.quarantine(source, root / "out", ["code.py"])
            with self.assertRaises(ValueError):
                grade.quarantine(source, root / "other", ["../outside.py"])

    def test_rejects_fifo_and_oversized_file(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.mkfifo(root / "fifo.py")
            with self.assertRaises(ValueError):
                grade.quarantine(root, root / "out", ["fifo.py"])
            (root / "large.py").write_bytes(b"x" * 1_000_001)
            with self.assertRaises(ValueError):
                grade.quarantine(root, root / "other", ["large.py"])


class ExportTests(unittest.TestCase):
    def test_snapshot_without_future_history_and_no_source_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (repo / "code.txt").write_text("base")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (repo / "code.txt").write_text("future answer")
            git("commit", "-qam", "future")
            future = git("rev-parse", "HEAD")
            command = ["bash", str(ROOT / "bench/taskkit/export_source.sh"),
                       str(repo), base, str(root / "out")]
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual((root / "out/code.txt").read_text(), "base")
            self.assertFalse((root / "out/.git").exists())
            self.assertEqual(git("rev-parse", "HEAD"), future)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
