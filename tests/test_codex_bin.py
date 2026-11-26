import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
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
        if os.environ.get("RPC_LOG"):
            with open(os.environ["RPC_LOG"], "a") as rf:
                print(json.dumps(request, ensure_ascii=False), file=rf)
        if name == "thread/start":
            assert request["params"]["sandbox"] == os.environ.get("EXPECT_SANDBOX", "read-only")
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
            reply = os.environ.get("STUB_REPLY", "OK")
            print(json.dumps({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": reply}}}), flush=True)
            print(json.dumps({"method": "turn/completed", "params": {"turn": {"status": "completed"}}}), flush=True)
elif sys.argv[1] == "exec":
    print(json.dumps({"type": "thread.started", "thread_id": "exec-session"}))
    with open(sys.argv[sys.argv.index("-o") + 1], "w") as output:
        output.write("exec OK\nsecond line")
elif sys.argv[1] == "review":
    if os.environ.get("FAIL_REVIEW"):
        print("review failed", file=sys.stderr)
        sys.exit(7)
    if sys.argv[-1].startswith("请审查"):
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
        self.env.pop("CODEX_WORKER_DEFAULT_DIR", None)
        self.env.pop("CODEX_KIT_RECEIPT_LOG", None)
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

    def run_cli_in(self, name, cwd, *args):
        return subprocess.run(
            [str(self.commands / name), *args],
            cwd=str(cwd), env=self.env, capture_output=True, text=True, timeout=10,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def receipts(self, path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_receipts_default_off_and_task_id_footer(self):
        self.env["CODEX_BIN"] = str(self.override)
        before = set(self.base.rglob("*"))
        for env_value in (None, ""):
            if env_value is not None:
                self.env["CODEX_KIT_RECEIPT_LOG"] = env_value
            worker = self.run_cli_in("codex-worker", self.base, "--read-only", "Reply OK")
            review = self.run_cli_in("codex-review", self.base)
            for result in (worker, review):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertRegex(result.stdout, r"(?m)status=ok .* task_id=t-[0-9a-f]{8}$")
            self.assertEqual(worker.stdout.splitlines()[-1], "[codex-worker] session_id=app-session")
        self.assertEqual(set(self.base.rglob("*")) - before, {self.log})

    def test_receipts_append_across_worker_resume_review_with_cli_priority(self):
        env_log = self.base / "receipts" / "env.jsonl"
        cli_log = self.base / "other" / "cli.jsonl"
        task_id = "feature-42"
        self.env.update(
            CODEX_KIT_RECEIPT_LOG=str(env_log), STUB_REPLY="好" * 250 + "\nsecond line",
        )
        cases = (
            ("codex-worker", ("--read-only", "Reply OK")),
            ("codex-worker", ("--read-only", "--hard", "--resume", "app-session", "Follow up")),
            ("codex-review", ()),
            ("codex-review", ("--base", "main", "--receipt-log", str(cli_log))),
        )
        for name, args in cases:
            result = self.run_cli(name, "--task-id", task_id, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f" task_id={task_id}\n", result.stdout)

        initial, resumed, review = self.receipts(env_log)
        (explicit,) = self.receipts(cli_log)
        for row in (initial, resumed, review, explicit):
            self.assertEqual(set(row), {
                "ts", "tool", "task_id", "session_id", "resume_from", "cwd", "effort",
                "sandbox", "model", "worktree", "scope", "status", "exit_code",
                "duration_ms", "summary",
            })
            self.assertEqual(row["task_id"], task_id)
            self.assertEqual(row["cwd"], str(self.target))
            self.assertEqual(row["status"], "ok")
            self.assertEqual(row["exit_code"], 0)
            self.assertIsInstance(row["duration_ms"], int)
            self.assertGreaterEqual(row["duration_ms"], 0)
            self.assertEqual(datetime.fromisoformat(row["ts"]).utcoffset(), timedelta(0))
        self.assertEqual(initial["tool"], "codex-worker")
        self.assertEqual(initial["sandbox"], "read-only")
        self.assertEqual(initial["summary"], "好" * 240)
        self.assertIsNone(initial["resume_from"])
        self.assertEqual(initial["session_id"], "app-session")
        self.assertEqual(resumed["session_id"], initial["session_id"])
        self.assertEqual(resumed["resume_from"], initial["session_id"])
        self.assertEqual(resumed["effort"], "high")
        self.assertEqual(review["tool"], "codex-review")
        self.assertEqual(review["scope"], "uncommitted")
        self.assertIsNone(review["session_id"])
        self.assertEqual(review["summary"], "review retry OK")
        self.assertEqual(explicit["scope"], "base:main")

    def test_failed_task_receipts_and_unwritable_log(self):
        receipt_log = self.base / "failures.jsonl"
        self.env.update(
            CODEX_KIT_RECEIPT_LOG=str(receipt_log), FAIL_WRITER_LOCK="1", FAIL_REVIEW="1",
        )
        worker = self.run_cli("codex-worker", "--raw", "--read-only", "--resume", "app-session", "Retry")
        self.assertEqual(worker.returncode, 1, worker.stderr)
        self.assertNotIn("[codex-worker]", worker.stdout)
        review = self.run_cli("codex-review")
        self.assertEqual(review.returncode, 7, review.stderr)
        worker_row, review_row = self.receipts(receipt_log)
        self.assertEqual(worker_row["exit_code"], 1)
        self.assertEqual(worker_row["resume_from"], "app-session")
        self.assertEqual(worker_row["status"], "failed(failed)")
        self.assertIn("already has an active writer", worker_row["summary"])
        self.assertEqual(review_row["exit_code"], 7)
        self.assertEqual(review_row["status"], "failed(7)")
        self.assertEqual(review_row["summary"], "review failed")

        failed_write = self.run_cli("codex-review", "--receipt-log", str(receipt_log / "nested"))
        self.assertEqual(failed_write.returncode, 7)
        self.assertIn("Warning: could not append task receipt", failed_write.stderr)

    def test_invalid_receipt_path_and_reveal_only(self):
        env_log = self.base / "env.jsonl"
        self.env["CODEX_KIT_RECEIPT_LOG"] = str(env_log)
        session_id = "019a0000-0000-7000-8000-000000000009"
        for name, args in (
            ("codex-worker", ("Reply OK",)),
            ("codex-worker", ("--reveal-only", session_id)),
            ("codex-review", ()),
        ):
            for path in ("", "  ", str(self.base)):
                with self.subTest(name=name, args=args, path=path):
                    result = self.run_cli(name, "--receipt-log", path, *args)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("--receipt-log", result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse(env_log.exists())

        self.env["CODEX_KIT_RECEIPT_LOG"] = str(self.base)
        invalid_env = self.run_cli("codex-review")
        self.assertEqual(invalid_env.returncode, 2)
        self.assertIn("CODEX_KIT_RECEIPT_LOG", invalid_env.stderr)
        self.assertFalse(self.log.exists())

        self.env["CODEX_KIT_RECEIPT_LOG"] = str(env_log)
        # Keep this test independent of real Desktop state.
        lsappinfo = self.commands / "lsappinfo"
        lsappinfo.write_text("#!/bin/sh\nexit 0\n")
        lsappinfo.chmod(0o755)
        self.env["PATH"] = str(self.commands) + os.pathsep + self.env["PATH"]
        result = self.run_cli("codex-worker", "--reveal-only", session_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse(env_log.exists())

    def test_raw_worktree_receipt_keeps_session_and_final_message(self):
        receipt_log = self.base / "worktree.jsonl"
        result = self.run_cli(
            "codex-worker", "--worktree", "--raw", "--read-only",
            "--receipt-log", str(receipt_log), "Reply OK",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["thread_id"], "exec-session")
        (row,) = self.receipts(receipt_log)
        self.assertTrue(row["worktree"])
        self.assertEqual(row["session_id"], "exec-session")
        self.assertEqual(row["summary"], "exec OK")

    def test_receipt_permissions_prelaunch_failures_and_fifo_safety(self):
        # 1. Created receipt file uses 0600 permissions; blank env acts as unset; blank --task-id fails.
        receipt_log = self.base / "private.jsonl"
        ok = self.run_cli(
            "codex-worker", "--read-only", "--task-id", "perm-check",
            "--receipt-log", str(receipt_log), "Reply OK",
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        if os.name == "posix":
            self.assertEqual(receipt_log.stat().st_mode & 0o777, 0o600)

        self.env["CODEX_KIT_RECEIPT_LOG"] = "   "
        blank_env = self.run_cli("codex-review")
        self.assertEqual(blank_env.returncode, 0, blank_env.stderr)
        self.env.pop("CODEX_KIT_RECEIPT_LOG", None)

        blank_tid = self.run_cli("codex-worker", "--read-only", "--task-id", "  ", "Reply OK")
        self.assertEqual(blank_tid.returncode, 2)
        self.assertIn("--task-id", blank_tid.stderr)

        # 2. Pre-launch failures (missing CODEX_BIN / missing -C dir) still record receipts when enabled.
        prelaunch_log = self.base / "prelaunch.jsonl"
        self.env["CODEX_KIT_RECEIPT_LOG"] = str(prelaunch_log)
        self.env["CODEX_BIN"] = str(self.base / "no-such-codex")
        w_missing_bin = self.run_cli("codex-worker", "--task-id", "pre-1", "Reply OK")
        r_missing_bin = self.run_cli("codex-review", "--task-id", "pre-2")
        self.assertEqual(w_missing_bin.returncode, 2)
        self.assertEqual(r_missing_bin.returncode, 2)

        self.env["CODEX_BIN"] = str(self.override)
        missing_dir = self.base / "no-such-dir"
        w_missing_dir = self.run_cli_in("codex-worker", self.target, "-C", str(missing_dir), "--task-id", "pre-3", "Reply OK")
        self.assertEqual(w_missing_dir.returncode, 2)

        rows = self.receipts(prelaunch_log)
        self.assertEqual([(r["tool"], r["task_id"], r["exit_code"]) for r in rows], [
            ("codex-worker", "pre-1", 2),
            ("codex-review", "pre-2", 2),
            ("codex-worker", "pre-3", 2),
        ])

        # 3. Named pipe (FIFO) is rejected immediately without blocking.
        if hasattr(os, "mkfifo"):
            fifo = self.base / "receipt.fifo"
            os.mkfifo(fifo)
            res_fifo = self.run_cli("codex-worker", "--read-only", "--receipt-log", str(fifo), "Reply OK")
            self.assertEqual(res_fifo.returncode, 2)
            self.assertIn("regular file", res_fifo.stderr)

            sys.path.insert(0, str(ROOT / "bin"))
            from codex_runtime import append_task_receipt
            # Direct append to FIFO must warn and return immediately without hanging.
            append_task_receipt(fifo, {"tool": "codex-worker", "task_id": "fifo-test", "summary": "noop"})

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
        receipt_log = self.base / "launch-failures.jsonl"
        self.env["CODEX_KIT_RECEIPT_LOG"] = str(receipt_log)
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
        rows = self.receipts(receipt_log)
        self.assertEqual([row["exit_code"] for row in rows], [2, 2, 2])
        self.assertEqual([row["worktree"] for row in rows], [False, True, False])

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

    def test_default_dir_env_fallback_override_and_validation(self):
        default_inbox = self.base / "default inbox"
        default_inbox.mkdir()
        explicit_repo = self.base / "explicit repo"
        explicit_repo.mkdir()
        self.env["CODEX_BIN"] = str(self.override)

        # 1. No -C and no CODEX_WORKER_DEFAULT_DIR -> falls back to current directory (self.target).
        res_cwd = self.run_cli_in("codex-worker", self.target, "--read-only", "Reply OK")
        self.assertEqual(res_cwd.returncode, 0, res_cwd.stderr)
        self.assertIn(f"cwd={self.target}", res_cwd.stdout)

        # 2. CODEX_WORKER_DEFAULT_DIR applies when -C is omitted for codex-worker, not codex-review.
        self.env["CODEX_WORKER_DEFAULT_DIR"] = str(default_inbox)
        res_default = self.run_cli_in("codex-worker", explicit_repo, "--read-only", "Reply OK")
        self.assertEqual(res_default.returncode, 0, res_default.stderr)
        self.assertIn(f"cwd={default_inbox}", res_default.stdout)

        res_review = self.run_cli_in("codex-review", self.target)
        self.assertEqual(res_review.returncode, 0, res_review.stderr)
        self.assertIn(f"cwd={self.target}", res_review.stdout)

        # 3. Explicit -C (including -C .) always overrides CODEX_WORKER_DEFAULT_DIR.
        res_explicit = self.run_cli_in("codex-worker", explicit_repo, "-C", ".", "--read-only", "Reply OK")
        self.assertEqual(res_explicit.returncode, 0, res_explicit.stderr)
        self.assertIn(f"cwd={explicit_repo}", res_explicit.stdout)

        # 4. Blank CODEX_WORKER_DEFAULT_DIR acts as unset; nonexistent or file value fails fast.
        self.env["CODEX_WORKER_DEFAULT_DIR"] = "   "
        res_blank = self.run_cli_in("codex-worker", self.target, "--read-only", "Reply OK")
        self.assertEqual(res_blank.returncode, 0, res_blank.stderr)
        self.assertIn(f"cwd={self.target}", res_blank.stdout)

        missing_dir = self.base / "missing inbox"
        self.env["CODEX_WORKER_DEFAULT_DIR"] = str(missing_dir)
        res_missing = self.run_cli_in("codex-worker", self.target, "--read-only", "Reply OK")
        self.assertEqual(res_missing.returncode, 2)
        self.assertIn("CODEX_WORKER_DEFAULT_DIR", res_missing.stderr)

        not_a_dir = self.base / "not-a-dir.txt"
        not_a_dir.write_text("file")
        self.env["CODEX_WORKER_DEFAULT_DIR"] = str(not_a_dir)
        res_file = self.run_cli_in("codex-worker", self.target, "--read-only", "Reply OK")
        self.assertEqual(res_file.returncode, 2)
        self.assertIn("CODEX_WORKER_DEFAULT_DIR", res_file.stderr)

        # 5. Explicit blank -C fails fast for both CLIs instead of silently resolving '.'.
        self.env.pop("CODEX_WORKER_DEFAULT_DIR", None)
        for name, args in (("codex-worker", ("-C", "  ", "Reply OK")), ("codex-review", ("-C", ""))):
            res_empty = self.run_cli_in(name, self.target, *args)
            self.assertEqual(res_empty.returncode, 2)
            self.assertIn("-C/--cd", res_empty.stderr)

    def test_allow_full_developer_instructions_and_unsubscribe(self):
        # --read-only and --allow-full are mutually exclusive
        conflict = self.run_cli("codex-worker", "--read-only", "--allow-full", "Reply OK")
        self.assertEqual(conflict.returncode, 2)

        rpc_log = self.base / "rpc_calls.jsonl"
        self.env.update(
            CODEX_BIN=str(self.override),
            EXPECT_SANDBOX="danger-full-access",
            RPC_LOG=str(rpc_log),
        )
        res = self.run_cli("codex-worker", "--allow-full", "Reply OK")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("sandbox=danger-full-access", res.stdout)

        requests = [json.loads(line) for line in rpc_log.read_text().splitlines()]
        methods = [req["method"] for req in requests]
        self.assertIn("thread/start", methods)
        self.assertIn("thread/unsubscribe", methods)

        start_req = next(req for req in requests if req["method"] == "thread/start")
        dev_inst = start_req["params"].get("developerInstructions", "")
        self.assertIn("二次请示", dev_inst)
        self.assertIn(".tmp/", dev_inst)


if __name__ == "__main__":
    unittest.main()
