"""Subprocess helpers: logged runs, clear errors, ffmpeg progress callbacks."""
from __future__ import annotations

import json
import logging
import shlex
import subprocess
from pathlib import Path
from typing import Callable, Sequence

log = logging.getLogger("reelstudio.proc")

ProgressFn = Callable[[float, str], None]  # (fraction 0..1, message)


class CommandError(RuntimeError):
    def __init__(self, cmd: Sequence[str], returncode: int, stderr: str):
        tail = "\n".join(stderr.strip().splitlines()[-25:])
        super().__init__(f"command failed ({returncode}): {shlex.join(map(str, cmd))[:400]}\n{tail}")
        self.cmd = list(map(str, cmd))
        self.returncode = returncode
        self.stderr = stderr


def run(cmd: Sequence[str | Path], *, timeout: float | None = None, cwd: Path | None = None,
        env: dict | None = None, input_bytes: bytes | None = None, check: bool = True,
        log_file: Path | None = None) -> subprocess.CompletedProcess:
    """Run a command, capture stdout/stderr (bytes), raise CommandError on failure."""
    args = [str(c) for c in cmd]
    log.debug("run: %s", shlex.join(args))
    proc = subprocess.run(args, capture_output=True, timeout=timeout, cwd=cwd, env=env,
                          input=input_bytes)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("ab") as fh:
            fh.write(b"$ " + shlex.join(args).encode() + b"\n" + proc.stderr[-20000:] + b"\n")
    if check and proc.returncode != 0:
        raise CommandError(args, proc.returncode, proc.stderr.decode("utf-8", "replace"))
    return proc


def run_json(cmd: Sequence[str | Path], **kw) -> dict:
    return json.loads(run(cmd, **kw).stdout.decode("utf-8"))


def run_ffmpeg(args: Sequence[str | Path], *, ffmpeg: str = "ffmpeg", total_s: float | None = None,
               progress: ProgressFn | None = None, log_file: Path | None = None,
               timeout: float | None = None, cwd: Path | None = None) -> None:
    """Run ffmpeg with machine-readable progress on stdout.

    ``args`` excludes the binary and global flags; we add -hide_banner -nostdin -y
    and -progress pipe:1 so progress can be reported as a fraction of total_s.
    """
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats",
           *[str(a) for a in args]]
    log.debug("ffmpeg: %s", shlex.join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            bufsize=1, cwd=cwd)
    stderr_chunks: list[str] = []

    # Drain stderr on a thread so a chatty ffmpeg can never deadlock on a full pipe.
    import threading

    def _drain():
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_chunks.append(line)

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    assert proc.stdout is not None
    for line in proc.stdout:
        key, _, value = line.strip().partition("=")
        if key == "out_time_us" and progress and total_s:
            try:
                frac = max(0.0, min(1.0, int(value) / 1e6 / total_s))
                progress(frac, "encoding")
            except ValueError:
                pass
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    t.join(timeout=5)
    stderr = "".join(stderr_chunks)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write("$ " + shlex.join(cmd) + "\n" + stderr[-20000:] + "\n")
    if rc != 0:
        raise CommandError(cmd, rc, stderr)
    if progress:
        progress(1.0, "done")
