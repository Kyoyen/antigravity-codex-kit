"""Resolve the Codex executable used by the toolkit's entry points."""

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional, TextIO


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
