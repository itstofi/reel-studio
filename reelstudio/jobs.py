"""In-process job queue: ONE worker thread runs heavy jobs (ffmpeg, whisper, HyperFrames)
strictly in submission order so two renders never fight over the GPU/RAM.

A job's function receives a progress callback ``progress(fraction, message)``; every call
updates the Job and fans the new state out to subscribers:
- sync callables (CLI, tests) are called on the worker thread;
- asyncio queues (SSE clients) are fed with ``loop.call_soon_threadsafe`` so the worker
  thread never touches an event loop directly.
Cancel is best effort: a queued job is dropped; a running job stops at its next progress
tick (the callback raises ``JobCancelled``).
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from .models import now_iso

log = logging.getLogger("reelstudio.jobs")

JobState = Literal["queued", "running", "done", "failed"]
ProgressFn = Callable[[float, str], None]
JobFn = Callable[[ProgressFn], Any]


class Job(BaseModel):
    id: str
    project_id: str | None = None
    kind: str
    state: JobState = "queued"
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    result: Any = None
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None


class JobCancelled(RuntimeError):
    pass


def to_jsonable(value: Any) -> Any:
    """Job results go to JSON/SSE: pydantic models, Paths, tuples -> plain data."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


class JobManager:
    """Owns the queue, the single worker thread and the subscriber fan-out."""

    def __init__(self, *, log_dir_for: Callable[[str], Path | None] | None = None,
                 min_interval_s: float = 0.2):
        self._jobs: dict[str, Job] = {}
        self._fns: dict[str, JobFn] = {}
        self._cancel: set[str] = set()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.RLock()
        self._subs: list[Callable[[Job], None]] = []
        self._async_subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._ids = itertools.count(1)
        self._log_dir_for = log_dir_for
        self._min_interval_s = min_interval_s   # progress fan-out throttle
        self._last_emit: dict[str, float] = {}
        self._worker: threading.Thread | None = None
        self._stopping = False

    # ── public API ───────────────────────────────────────────────────────────
    def submit(self, kind: str, fn: JobFn, *, project_id: str | None = None) -> Job:
        with self._lock:
            jid = f"j{int(time.time())}-{next(self._ids):04d}"
            job = Job(id=jid, project_id=project_id, kind=kind)
            self._jobs[jid] = job
            self._fns[jid] = fn
            self._ensure_worker()
        self._queue.put(jid)
        self._emit(job)
        return job.model_copy()

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._jobs[job_id].model_copy()

    def list(self) -> list[Job]:
        with self._lock:
            return sorted((j.model_copy() for j in self._jobs.values()),
                          key=lambda j: j.created_at, reverse=True)

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            if job.state in ("done", "failed"):
                return job.model_copy()
            self._cancel.add(job_id)
            if job.state == "queued":   # never started: drop it now
                self._fns.pop(job_id, None)
                self._finish(job, error="cancelled")
            return job.model_copy()

    def wait(self, job_id: str, timeout: float | None = None) -> Job:
        """Block until the job finishes (CLI/tests)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            job = self.get(job_id)
            if job.state in ("done", "failed"):
                return job
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(job_id)
            time.sleep(0.02)

    def subscribe(self, fn: Callable[[Job], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)
        return lambda: self._unsub(fn)

    def subscribe_async(self, loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Queue:
        """An asyncio.Queue fed with Job copies; call unsubscribe_async(q) when done."""
        loop = loop or asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._async_subs.append((loop, q))
        return q

    def unsubscribe_async(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._async_subs = [(lp, x) for lp, x in self._async_subs if x is not q]

    def shutdown(self) -> None:
        self._stopping = True
        self._queue.put(None)

    # ── internals ────────────────────────────────────────────────────────────
    def _unsub(self, fn) -> None:
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._loop, name="reel-worker", daemon=True)
            self._worker.start()

    def _loop(self) -> None:
        while not self._stopping:
            jid = self._queue.get()
            if jid is None:
                return
            with self._lock:
                job = self._jobs.get(jid)
                if job is None or job.state != "queued":   # cancelled while queued
                    continue
                job.state, job.started_at, job.message = "running", now_iso(), "started"
                fn = self._fns.pop(jid)
            self._emit(job, force=True)
            try:
                result = fn(self._progress_for(jid))
            except BaseException as exc:  # record every failure; the worker must survive
                cancelled = isinstance(exc, JobCancelled) or jid in self._cancel
                msg = "cancelled" if cancelled else f"{type(exc).__name__}: {exc}"
                if not cancelled:
                    log.exception("job %s (%s) failed", jid, job.kind)
                    self._write_log(job, traceback.format_exc())
                with self._lock:
                    self._finish(job, error=msg)
                continue
            with self._lock:
                job.result = to_jsonable(result)
                self._finish(job)

    def _finish(self, job: Job, *, error: str | None = None) -> None:
        job.finished_at = now_iso()
        if error is None:
            job.state, job.progress, job.message = "done", 1.0, job.message if job.message != "started" else "done"
        else:
            job.state, job.error, job.message = "failed", error, error.splitlines()[0][:300]
        self._cancel.discard(job.id)
        self._emit(job, force=True)

    def _progress_for(self, jid: str) -> ProgressFn:
        def progress(frac: float, message: str = "") -> None:
            if jid in self._cancel:
                raise JobCancelled("cancelled")
            with self._lock:
                job = self._jobs[jid]
                job.progress = max(0.0, min(1.0, float(frac)))
                if message:
                    job.message = message
            self._emit(job)
        return progress

    def _emit(self, job: Job, force: bool = False) -> None:
        now = time.monotonic()
        if not force and job.state == "running":
            if now - self._last_emit.get(job.id, 0.0) < self._min_interval_s:
                return
        self._last_emit[job.id] = now
        with self._lock:
            snap = job.model_copy(deep=True)
            subs = list(self._subs)
            asubs = list(self._async_subs)
        for fn in subs:
            try:
                fn(snap)
            except Exception:  # a broken subscriber must not kill the job
                log.exception("job subscriber failed")
        for loop, q in asubs:
            try:
                loop.call_soon_threadsafe(_put_nowait, q, snap)
            except RuntimeError:  # loop closed: client went away
                self.unsubscribe_async(q)

    def _write_log(self, job: Job, text: str) -> None:
        if not self._log_dir_for or not job.project_id:
            return
        try:
            d = self._log_dir_for(job.project_id)
            if d is None:
                return
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{job.id}-{job.kind}.log").write_text(text, encoding="utf-8")
        except Exception:
            log.exception("could not write job log")


def _put_nowait(q: asyncio.Queue, item: Job) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:  # slow client: drop the oldest update, keep the newest
        try:
            q.get_nowait()
            q.put_nowait(item)
        except Exception:
            pass
