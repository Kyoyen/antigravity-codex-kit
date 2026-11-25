import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUB = r"""
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
            if os.environ.get("NOISY_APP_SERVER"):
                sys.stderr.buffer.write(b"debug line\n" * 110000)
                sys.stderr.buffer.write(b"last stderr line\n")
                sys.stderr.buffer.flush()
            if os.environ.get("FAIL_CONFIG"):
                print(json.dumps({"id": 2, "error": {"message": "failed to load configuration: unknown variant indexed"}}), flush=True)
            else:
                tid = os.environ.get("STUB_THREAD_ID", "app-session")
                print(json.dumps({"id": 2, "result": {"thread": {"id": tid}}}), flush=True)
        if name == "thread/resume":
            if os.environ.get("FAIL_WRITER_LOCK"):
                print(json.dumps({"id": 2, "error": {"message": "thread 019a0000-0000-7000-8000-000000000001 already has an active writer"}}), flush=True)
            else:
                print(json.dumps({"id": 2, "result": {"thread": {"id": request["params"]["threadId"]}}}), flush=True)
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
        self.env.pop("CODEX_WORKER_REVEAL", None)
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

    def test_app_server_drains_noisy_stderr_before_thread_response(self):
        self.env.update(CODEX_BIN=str(self.override), NOISY_APP_SERVER="1")
        result = self.run_cli("codex-worker", "--read-only", "Reply OK")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("session_id=app-session", result.stdout)
        self.assertEqual(result.stderr, "")

        self.env["FAIL_CONFIG"] = "1"
        result = self.run_cli("codex-worker", "--read-only", "Reply OK")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown variant indexed", result.stderr)
        self.assertIn("last stderr line", result.stderr)
        self.assertLess(len(result.stderr), 66 * 1024)

    def test_config_failure_reports_selected_executable_and_version(self):
        self.env.update(CODEX_BIN=str(self.override), FAIL_CONFIG="1")
        result = self.run_cli("codex-worker", "--read-only", "Reply OK")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown variant indexed", result.stderr)
        self.assertIn(str(self.override), result.stderr)
        self.assertIn("codex-cli 0.1.0", result.stderr)
        self.assertEqual(self.calls()[-1], [str(self.override), "--version"])

    def test_invalid_reveal_env_is_rejected(self):
        self.env.update(CODEX_BIN=str(self.override), CODEX_WORKER_REVEAL="bogus")
        result = self.run_cli("codex-worker", "--read-only", "Reply OK")
        self.assertEqual(result.returncode, 2)
        self.assertIn("CODEX_WORKER_REVEAL", result.stderr)
        self.assertFalse(self.log.exists())

    def test_desktop_reveal_modes_and_focus_safety(self):
        sys.path.insert(0, str(ROOT / "bin"))
        from codex_runtime import reveal_in_desktop_app

        open_log = self.base / "open_calls.jsonl"
        fake_tools = self.base / "fake-macos-tools"
        fake_tools.mkdir()

        lsappinfo = fake_tools / "lsappinfo"
        lsappinfo.write_text(
            "#!" + sys.executable + "\n"
            "import os\n"
            "state = os.environ.get('FAKE_DESKTOP_STATE', 'stopped')\n"
            "if state == 'background':\n"
            "    print('[ NULL ]  ASN:0x0-0x1:\\n    bundleID=\"com.openai.codex\"')\n"
            "elif state == 'front':\n"
            "    print('[ NULL ]  ASN:0x0-0x1: (in front)\\n    bundleID=\"com.openai.codex\"')\n"
        )
        lsappinfo.chmod(0o755)

        fake_open = fake_tools / "open"
        fake_open.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "with open(os.environ['OPEN_LOG'], 'a') as f:\n"
            "    print(json.dumps(sys.argv[1:]), file=f)\n"
        )
        fake_open.chmod(0o755)

        valid_uuid = "019a0000-0000-7000-8000-000000000001"
        old_env = os.environ.copy()
        try:
            os.environ["PATH"] = str(fake_tools) + os.pathsep + old_env.get("PATH", os.defpath)
            os.environ["OPEN_LOG"] = str(open_log)

            # 1. Stopped desktop app -> never cold-launch
            os.environ["FAKE_DESKTOP_STATE"] = "stopped"
            self.assertFalse(reveal_in_desktop_app(valid_uuid, "auto", platform="darwin"))
            self.assertFalse(reveal_in_desktop_app(valid_uuid, "always", platform="darwin"))
            self.assertFalse(open_log.exists())

            # 2. Running in background -> auto reveals without stealing focus (-g)
            os.environ["FAKE_DESKTOP_STATE"] = "background"
            self.assertTrue(reveal_in_desktop_app(valid_uuid, "auto", platform="darwin"))
            self.assertEqual(
                [json.loads(l) for l in open_log.read_text().splitlines()],
                [["-g", "-b", "com.openai.codex", f"codex://threads/{valid_uuid}"]],
            )
            open_log.unlink()

            # 3. Running in front -> auto does not hijack active window, always does
            os.environ["FAKE_DESKTOP_STATE"] = "front"
            self.assertFalse(reveal_in_desktop_app(valid_uuid, "auto", platform="darwin"))
            self.assertFalse(open_log.exists())
            self.assertTrue(reveal_in_desktop_app(valid_uuid, "always", platform="darwin"))
            self.assertEqual(
                [json.loads(l) for l in open_log.read_text().splitlines()],
                [["-g", "-b", "com.openai.codex", f"codex://threads/{valid_uuid}"]],
            )
            open_log.unlink()

            # 4. Non-darwin / never / non-UUID -> no-op
            os.environ["FAKE_DESKTOP_STATE"] = "background"
            self.assertFalse(reveal_in_desktop_app(valid_uuid, "never", platform="darwin"))
            self.assertFalse(reveal_in_desktop_app(valid_uuid, "auto", platform="linux"))
            self.assertFalse(reveal_in_desktop_app("not-a-uuid", "always", platform="darwin"))
            self.assertFalse(open_log.exists())
        finally:
            os.environ.clear()
            os.environ.update(old_env)

        # 5. End-to-end CLI flag integration (default=never, opt-in auto/--reveal) on darwin
        if sys.platform == "darwin":
            self.env.update(
                CODEX_BIN=str(self.override),
                STUB_THREAD_ID=valid_uuid,
                OPEN_LOG=str(open_log),
                FAKE_DESKTOP_STATE="background",
                PATH=str(fake_tools) + os.pathsep + self.env["PATH"],
            )
            # Default must NOT open Desktop even when Desktop is in background (keeps --resume unlocked).
            res_default = self.run_cli("codex-worker", "--read-only", "Reply OK")
            self.assertEqual(res_default.returncode, 0, res_default.stderr)
            self.assertFalse(open_log.exists())

            # Explicit CODEX_WORKER_REVEAL=auto reveals when Desktop is in background, skips in front.
            self.env["CODEX_WORKER_REVEAL"] = "auto"
            res_auto_bg = self.run_cli("codex-worker", "--read-only", "Reply OK")
            self.assertEqual(res_auto_bg.returncode, 0, res_auto_bg.stderr)
            self.assertEqual(
                [json.loads(l) for l in open_log.read_text().splitlines()],
                [["-g", "-b", "com.openai.codex", f"codex://threads/{valid_uuid}"]],
            )
            open_log.unlink()

            self.env["FAKE_DESKTOP_STATE"] = "front"
            res_auto_front = self.run_cli("codex-worker", "--read-only", "Reply OK")
            self.assertEqual(res_auto_front.returncode, 0, res_auto_front.stderr)
            self.assertFalse(open_log.exists())

            # Explicit --reveal overrides default/front; --no-reveal overrides env.
            res_reveal = self.run_cli("codex-worker", "--read-only", "--reveal", "Reply OK")
            self.assertEqual(res_reveal.returncode, 0, res_reveal.stderr)
            self.assertEqual(
                [json.loads(l) for l in open_log.read_text().splitlines()],
                [["-g", "-b", "com.openai.codex", f"codex://threads/{valid_uuid}"]],
            )
            open_log.unlink()

            self.env.update(FAKE_DESKTOP_STATE="background", CODEX_WORKER_REVEAL="always")
            res_no = self.run_cli("codex-worker", "--read-only", "--no-reveal", "Reply OK")
            self.assertEqual(res_no.returncode, 0, res_no.stderr)
            self.assertFalse(open_log.exists())

    def test_writer_lock_conflict_reports_actionable_hint(self):
        self.env.update(CODEX_BIN=str(self.override), FAIL_WRITER_LOCK="1")
        result = self.run_cli(
            "codex-worker", "--read-only", "--resume", "019a0000-0000-7000-8000-000000000001", "Reply OK"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("already has an active writer", result.stderr)
        self.assertIn("Codex Desktop", result.stderr)
        self.assertIn("--resume", result.stderr)

    def test_reveal_only_validates_uuid_and_reveals_without_running_codex(self):
        bad = self.run_cli("codex-worker", "--reveal-only", "not-a-uuid")
        self.assertEqual(bad.returncode, 2)
        self.assertIn("valid session UUID", bad.stderr)
        self.assertFalse(self.log.exists())

        if sys.platform == "darwin":
            open_log = self.base / "reveal_only_open.jsonl"
            fake_tools = self.base / "fake-reveal-only-tools"
            fake_tools.mkdir()

            lsappinfo = fake_tools / "lsappinfo"
            lsappinfo.write_text(
                "#!" + sys.executable + "\n"
                "print('[ NULL ]  ASN:0x0-0x1:\\n    bundleID=\"com.openai.codex\"')\n"
            )
            lsappinfo.chmod(0o755)

            fake_open = fake_tools / "open"
            fake_open.write_text(
                "#!" + sys.executable + "\n"
                "import json, os, sys\n"
                "with open(os.environ['OPEN_LOG'], 'a') as f:\n"
                "    print(json.dumps(sys.argv[1:]), file=f)\n"
            )
            fake_open.chmod(0o755)

            valid_uuid = "019a0000-0000-7000-8000-000000000009"
            self.env.update(
                OPEN_LOG=str(open_log),
                PATH=str(fake_tools) + os.pathsep + self.env["PATH"],
            )
            ok = self.run_cli("codex-worker", "--reveal-only", valid_uuid)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn(f"reveal=ok session_id={valid_uuid}", ok.stdout)
            self.assertEqual(
                [json.loads(l) for l in open_log.read_text().splitlines()],
                [["-g", "-b", "com.openai.codex", f"codex://threads/{valid_uuid}"]],
            )
            self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
