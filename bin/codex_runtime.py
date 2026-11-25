"""Resolve the Codex executable used by the toolkit's entry points."""

import os
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Optional, TextIO

DESKTOP_BUNDLE_ID = "com.openai.codex"
REVEAL_ENV_MAP = {
    "auto": "auto",
    "always": "always",
    "1": "always",
    "true": "always",
    "yes": "always",
    "never": "never",
    "0": "never",
    "false": "never",
    "no": "never",
}


def resolve_codex(target_dir: Path) -> Optional[str]:
    override = os.environ.get("CODEX_BIN")
    if override is not None:
        path = Path(override)
        if path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
            return str(path)
        print("Error: CODEX_BIN must name an executable file by absolute path.", file=sys.stderr)
        return None

    # The Codex child runs in target_dir; resolve relative PATH entries there too.
    search_path = os.pathsep.join(
        str(target_dir / entry) for entry in os.get_exec_path()
    )
    path = shutil.which("codex", path=search_path)
    if path:
        return path
    print("Error: codex was not found on PATH. Install Codex CLI or set CODEX_BIN.", file=sys.stderr)
    return None


def report_launch_error(codex_bin: str, error: OSError) -> None:
    print(f"Error: could not launch Codex CLI {codex_bin}: {error.strerror or error}", file=sys.stderr)


def report_config_error(codex_bin: str, *messages: str) -> None:
    if not any(
        "failed to load configuration" in message.lower() or "unknown variant" in message.lower()
        for message in messages
    ):
        return

    version = ""
    try:
        result = subprocess.run(
            [codex_bin, "--version"], capture_output=True, text=True, errors="replace",
            timeout=2, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            version = f" ({result.stdout.strip().splitlines()[0]})"
    except (OSError, subprocess.TimeoutExpired):
        pass

    print(
        f"Codex CLI: {codex_bin}{version}. "
        "Use a CLI compatible with the current configuration via CODEX_BIN or PATH.",
        file=sys.stderr,
    )


class StderrTail:
    """Continuously drain a CLI pipe while keeping just the last 64 KiB."""

    LIMIT = 64 * 1024

    def __init__(self, stream: TextIO) -> None:
        self._tail = bytearray()
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._drain, args=(stream,), daemon=True)
        self._reader.start()

    def _drain(self, stream: TextIO) -> None:
        with stream:
            try:
                while chunk := os.read(stream.fileno(), 8192):
                    with self._lock:
                        self._tail.extend(chunk)
                        del self._tail[:-self.LIMIT]
            except OSError:
                pass

    def finish(self) -> str:
        # The caller has already reaped the child. A descendant retaining the
        # pipe must not prevent returning diagnostics.
        self._reader.join(timeout=2)
        with self._lock:
            return self._tail.decode("utf-8", errors="replace")


def report_writer_lock_error(*messages: str) -> None:
    if not any("already has an active writer" in message.lower() for message in messages):
        return
    print(
        "Hint: This session is currently locked by another Codex process (usually Codex Desktop "
        "if the thread was opened there). Continue in Codex Desktop, start a new codex-worker "
        "session without --resume, or restart Codex Desktop to release the lock.",
        file=sys.stderr,
    )


def resolve_reveal_mode(reveal_flag: Optional[bool]) -> Optional[str]:
    if reveal_flag is True:
        return "always"
    if reveal_flag is False:
        return "never"

    raw = os.environ.get("CODEX_WORKER_REVEAL")
    if raw is None or not raw.strip():
        return "never"
    mapped = REVEAL_ENV_MAP.get(raw.strip().lower())
    if mapped is not None:
        return mapped
    print(
        "Error: CODEX_WORKER_REVEAL must be one of never, auto, always (or 0/1/false/true).",
        file=sys.stderr,
    )
    return None


def reveal_in_desktop_app(thread_id: Optional[str], mode: str, platform: Optional[str] = None) -> bool:
    """Refresh an already-running macOS Codex Desktop window via official deep link.

    Must be called only after the worker's app-server process has exited so the
    per-thread writer lock has been released. Never cold-launches Codex Desktop,
    never steals window focus (`open -g`), and in `auto` mode avoids switching
    away from an actively focused Codex Desktop window.
    """
    if mode == "never" or not thread_id:
        return False
    if (platform or sys.platform) != "darwin":
        return False

    try:
        canonical_id = str(uuid.UUID(thread_id))
    except ValueError:
        return False

    lsappinfo_bin = shutil.which("lsappinfo")
    open_bin = shutil.which("open")
    if not lsappinfo_bin or not open_bin:
        return False

    try:
        info = subprocess.run(
            [lsappinfo_bin, "info", "-only", "bundleid", "-app", DESKTOP_BUNDLE_ID],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    output = info.stdout or ""
    if f'bundleID="{DESKTOP_BUNDLE_ID}"' not in output:
        return False
    if mode == "auto" and "(in front)" in output:
        return False

    try:
        proc = subprocess.run(
            [open_bin, "-g", "-b", DESKTOP_BUNDLE_ID, f"codex://threads/{canonical_id}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
