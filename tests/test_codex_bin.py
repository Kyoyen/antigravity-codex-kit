import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUB = """
import json, os, sys
with open(os.environ["CALL_LOG"], "a") as f:
    print(json.dumps([sys.argv[0], *sys.argv[1:]]), file=f)
if sys.argv[1] == "--version":
    print("codex-cli 0.1.0")
elif sys.argv[1] == "app-server":
    for line in sys.stdin:
        request = json.loads(line)
        name = request["method"]
        if name == "initialize":
            print(json.dumps({"id": 1, "result": {}}), flush=True)
        if name == "thread/start":
            assert request["params"]["sandbox"] == "read-only"
            assert request["params"]["cwd"] == os.getcwd()
            if os.environ.get("FAIL_CONFIG"):
                print(json.dumps({"id": 2, "error": {"message": "failed to load configuration: unknown variant indexed"}}), flush=True)
            else:
                print(json.dumps({"id": 2, "result": {"thread": {"id": "app-session"}}}), flush=True)
        if name == "turn/start":
            print(json.dumps({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "OK"}}}), flush=True)
            print(json.dumps({"method": "turn/completed", "params": {"turn": {"status": "completed"}}}), flush=True)
elif sys.argv[1] == "exec":
    print(json.dumps({"type": "thread.started", "thread_id": "exec-session"}))
elif sys.argv[1] == "review":
    if len(sys.argv) > 5:
        print("prompt cannot be used with --uncommitted", file=sys.stderr)
        sys.exit(2)
    print("review retry OK")
"""


class CodexBinTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.target = self.base / "empty project"
        self.target.mkdir()
        subprocess.run(["git", "init", "-q", str(self.target)], check=True)

        self.default = self.fake_codex(self.target / "relative-path" / "codex")
        self.override = self.fake_codex(self.base / "directory with spaces" / "codex")
        self.log = self.base / "calls.jsonl"
        self.env = os.environ.copy()
        self.env.pop("CODEX_BIN", None)
        self.env.update(
            PATH="relative-path" + os.pathsep + os.environ.get("PATH", os.defpath),
            CALL_LOG=str(self.log),
            PYTHONDONTWRITEBYTECODE="1",
        )

        # Match install.sh: links to the executable scripts, not copies of bin/.
        self.commands = self.base / "installed bin"
        self.commands.mkdir()
        for name in ["codex-worker", "codex-review"]:
            (self.commands / name).symlink_to(ROOT / "bin" / name)

    def fake_codex(self, path):
        path.parent.mkdir(parents=True)
        path.write_text("#!" + sys.executable + "\n" + STUB)
        path.chmod(0o755)
        return path

    def run_cli(self, name, *args):
        return subprocess.run(
            [str(self.commands / name), "-C", str(self.target), *args],
            env=self.env, capture_output=True, text=True, timeout=10,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_default_looks_up_path_from_target_directory(self):
        result = self.run_cli("codex-worker", "--read-only", "Reply OK")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("session_id=app-session", result.stdout)
        self.assertEqual(self.calls(), [[str(self.default), "app-server", "--stdio"]])

    def test_spaces_and_all_launch_paths(self):
        self.env["CODEX_BIN"] = str(self.override)
        for name, args, expected in (
            ("codex-worker", ("--read-only", "Reply OK"), "session_id=app-session"),
            ("codex-worker", ("--read-only", "--worktree", "Reply OK"), "session_id=exec-session"),
            ("codex-worker", ("--read-only", "--worktree", "--resume", "previous", "Reply OK"), "session_id=exec-session"),
            ("codex-review", (), "review retry OK"),
        ):
            result = self.run_cli(name, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(expected, result.stdout)
        calls = self.calls()
        self.assertEqual([call[1] for call in calls], ["app-server", "exec", "exec", "review", "review"])
        self.assertTrue(all(call[0] == str(self.override) for call in calls))
        self.assertIn("--worktree", calls[1])
        self.assertEqual(calls[1][calls[1].index("-s") + 1], "read-only")
        self.assertEqual(calls[2][2], "resume")
        self.assertIn('sandbox_mode="read-only"', calls[2])

    def test_invalid_override_does_not_fall_back(self):
        for invalid in ("", "codex", str(self.override.parent), str(self.base / "missing")):
            with self.subTest(invalid=invalid):
                self.env["CODEX_BIN"] = invalid
                for name, args in (("codex-worker", ("Reply OK",)), ("codex-review", ())):
                    result = self.run_cli(name, *args)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("CODEX_BIN", result.stderr)
                    self.assertFalse(self.log.exists())

    def test_bad_executable_format_reports_error_without_fallback(self):
        invalid = self.base / "bad executable"
        invalid.write_text("not an executable")
        invalid.chmod(0o755)
        self.env["CODEX_BIN"] = str(invalid)
        for name, args in (
            ("codex-worker", ("Reply OK",)),
            ("codex-worker", ("--worktree", "Reply OK")),
            ("codex-review", ()),
        ):
            with self.subTest(name=name, args=args):
                result = self.run_cli(name, *args)
                self.assertEqual(result.returncode, 2)
                self.assertIn(str(invalid), result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(self.log.exists())

    def test_config_failure_reports_selected_executable_and_version(self):
        self.env.update(CODEX_BIN=str(self.override), FAIL_CONFIG="1")
        result = self.run_cli("codex-worker", "--read-only", "Reply OK")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown variant indexed", result.stderr)
        self.assertIn(str(self.override), result.stderr)
        self.assertIn("codex-cli 0.1.0", result.stderr)
        self.assertEqual(self.calls()[-1], [str(self.override), "--version"])


if __name__ == "__main__":
    unittest.main()
