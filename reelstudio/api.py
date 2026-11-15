"""Web API (FastAPI) + static UI. Local only: bind 127.0.0.1."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, pipeline
from .config import WEB_DIR
from .jobs import JobManager, to_jsonable
from .models import CaptionCue, Plan, ProjectSettings, QAReport
from .store import ProjectStore, read_json

STAGE_FNS = {
    "ingest": lambda s, pid, pr: pipeline.ingest(s, pid, pr),
    "analyze": lambda s, pid, pr: pipeline.analyze(s, pid, pr),
    "plan": lambda s, pid, pr: pipeline.plan(s, pid, pr),
    "render": lambda s, pid, pr: pipeline.render(s, pid, pr),
    "preview": lambda s, pid, pr: pipeline.render(s, pid, pr, preview=True),
    "qa": lambda s, pid, pr: pipeline.qa(s, pid, pr),
    "prepare": lambda s, pid, pr: pipeline.run_until_approval(s, pid, pr),
}


def create_app(store: ProjectStore | None = None, jobs: JobManager | None = None) -> FastAPI:
    store = store or ProjectStore()
    jobs = jobs or JobManager()
    app = FastAPI(title="Reel Studio", version=__version__)

    def project_or_404(pid: str):
        try:
            return store.load(pid)
        except FileNotFoundError:
            raise HTTPException(404, f"no project {pid}")

    def guard(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except HTTPException:
            raise
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc))
        except (ValueError, pipeline.StageError) as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.get("/api/doctor")
    def doctor_route():
        from .doctor import run_checks
        return {"checks": run_checks()}

    @app.get("/api/projects")
    def projects():
        return [p.model_dump(mode="json") for p in store.list()]

    @app.post("/api/projects")
    def create(body: dict = Body(...)):
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        return guard(pipeline.create_project, store, name, lang=body.get("lang", "ar"),
                     mode=body.get("mode", "talking_head"))

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        return project_or_404(pid)

    @app.patch("/api/projects/{pid}")
    def patch_project(pid: str, body: dict = Body(...)):
        project_or_404(pid)

        def f(p):
            if body.get("name"):
                p.name = body["name"]
            if body.get("settings"):
                p.settings = ProjectSettings(**{**p.settings.model_dump(), **body["settings"]})
                pipeline._stale_from(p, "plan")
        return store.update(pid, f)

    @app.post("/api/projects/{pid}/takes/import")
    def import_takes(pid: str, body: dict = Body(...)):
        project_or_404(pid)
        return guard(pipeline.import_takes, store, pid, body.get("paths") or [], lang=body.get("lang"),
                     mirror=body.get("mirror", True))

    @app.post("/api/projects/{pid}/takes/upload")
    async def upload(pid: str, files: list[UploadFile] = File(...)):
        project_or_404(pid)
        dest = store.paths(pid).root / "uploads"
        dest.mkdir(parents=True, exist_ok=True)
        saved = []
        for f in files:
            name = Path(f.filename or "take.mov").name
            target = dest / name
            with target.open("wb") as out:
                while chunk := await f.read(8 * 1024 * 1024):
                    out.write(chunk)
            saved.append(str(target))
        return guard(pipeline.import_takes, store, pid, saved)

    @app.patch("/api/projects/{pid}/takes/{tid}")
    def patch_take(pid: str, tid: str, body: dict = Body(...)):
        project_or_404(pid)

        def f(p):
            t = p.take(tid)
            if "lang" in body:
                t.lang = body["lang"] or None
            if "mirror" in body:
                t.mirror = bool(body["mirror"])
            pipeline._stale_from(p, "ingest")
        return guard(store.update, pid, f)

    @app.delete("/api/projects/{pid}/takes/{tid}")
    def delete_take(pid: str, tid: str):
        project_or_404(pid)
        return pipeline.remove_take(store, pid, tid)

    @app.put("/api/projects/{pid}/script")
    def put_script(pid: str, body: dict = Body(...)):
        project_or_404(pid)
        return guard(pipeline.set_script, store, pid, ar_text=body.get("ar_text"), en_text=body.get("en_text"))

    @app.post("/api/projects/{pid}/run/{stage}")
    def run_stage(pid: str, stage: str):
        project_or_404(pid)
        if stage not in STAGE_FNS:
            raise HTTPException(400, f"unknown stage {stage}")
        if stage in ("render", "preview"):
            try:
                if not pipeline.get_plan(store, pid).approved:
                    raise HTTPException(400, "approve the plan before rendering")
            except FileNotFoundError:
                raise HTTPException(400, "no plan yet")
        job = jobs.submit(stage, lambda pr: STAGE_FNS[stage](store, pid, pr), project_id=pid)
        return {"job_id": job.id}

    @app.get("/api/jobs")
    def list_jobs():
        return [j.model_dump(mode="json") for j in jobs.list()]

    @app.get("/api/jobs/{jid}")
    def get_job(jid: str):
        try:
            return jobs.get(jid).model_dump(mode="json")
        except KeyError:
            raise HTTPException(404, "no such job")

    @app.post("/api/jobs/{jid}/cancel")
    def cancel_job(jid: str):
        try:
            return jobs.cancel(jid).model_dump(mode="json")
        except KeyError:
            raise HTTPException(404, "no such job")

    @app.get("/api/events")
    async def events(request: Request):
        q = jobs.subscribe_async(asyncio.get_running_loop())

        async def stream():
            try:
                yield "retry: 2000\n\n"
                for j in jobs.list()[-20:]:
                    yield f"event: job\ndata: {json.dumps(to_jsonable(j))}\n\n"
                while not await request.is_disconnected():
                    try:
                        job = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"event: job\ndata: {json.dumps(to_jsonable(job))}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                jobs.unsubscribe_async(q)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/projects/{pid}/analysis")
    def analysis(pid: str):
        project_or_404(pid)
        return pipeline.analysis_view(store, pid)

    @app.get("/api/projects/{pid}/alignment")
    def alignment(pid: str):
        f = store.paths(pid).alignment
        if not f.exists():
            raise HTTPException(404, "no alignment yet")
        return json.loads(f.read_text(encoding="utf-8"))

    @app.get("/api/projects/{pid}/plan")
    def get_plan(pid: str):
        from .edit.planner import plan_summary
        pl = guard(pipeline.get_plan, store, pid)
        return {"plan": pl.model_dump(mode="json"), "summary": plan_summary(pl)}

    @app.put("/api/projects/{pid}/plan")
    def put_plan(pid: str, body: dict = Body(...)):
        project_or_404(pid)
        return guard(pipeline.update_plan, store, pid, Plan(**body))

    @app.post("/api/projects/{pid}/plan/choose")
    def choose(pid: str, body: dict = Body(...)):
        return guard(pipeline.choose_attempt, store, pid, body["beat_id"], int(body["attempt_index"]))

    @app.post("/api/projects/{pid}/plan/approve")
    def approve(pid: str):
        return guard(pipeline.approve, store, pid)

    @app.get("/api/projects/{pid}/captions")
    def captions(pid: str):
        f = store.paths(pid).captions_json
        return [c.model_dump(mode="json") for c in read_json(f, list[CaptionCue])] if f.exists() else []

    @app.get("/api/projects/{pid}/qa")
    def qa_report(pid: str):
        f = store.paths(pid).qa_report
        if not f.exists():
            raise HTTPException(404, "no QA report yet")
        rep = read_json(f, QAReport)
        return {**rep.model_dump(mode="json"), "passed": rep.passed}

    def safe_path(pid: str, rel: str) -> Path:
        root = store.paths(pid).root.resolve()
        p = (root / rel).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            # project files may be symlinks to originals in takes/: allow only those links
            link = root / rel
            if not (link.is_symlink() and link.parent.resolve() == (root / "takes").resolve()):
                raise HTTPException(403, "path outside the project")
        if not p.is_file():
            raise HTTPException(404, "no such file")
        return p

    @app.get("/api/projects/{pid}/files/{rel:path}")
    def files(pid: str, rel: str, request: Request):
        project_or_404(pid)
        return _ranged_file(safe_path(pid, rel), request)

    @app.post("/api/projects/{pid}/reveal")
    def reveal(pid: str, body: dict = Body(...)):
        p = safe_path(pid, body.get("relpath", ""))
        subprocess.run(["open", "-R", str(p)], check=False)
        return {"ok": True}

    @app.get("/api/voice/engines")
    def voice_engines():
        from .voice.engines import list_engines
        return list_engines()

    @app.post("/api/projects/{pid}/voice")
    def voice(pid: str, body: dict = Body(...)):
        project_or_404(pid)
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "text is required")
        job = jobs.submit("voice", lambda pr: pipeline.generate_voice(
            store, pid, text=text, lang=body.get("lang", "ar"), engine=body.get("engine", "say"),
            voice=body.get("voice", "jweed")), project_id=pid)
        return {"job_id": job.id}

    @app.get("/api/projects/{pid}/voice")
    def voice_list(pid: str):
        return [c.model_dump(mode="json") for c in pipeline.list_voice(store, pid)]

    # ── recording studio: the creator reads the script into the laptop mic, line by line ──
    @app.get("/api/projects/{pid}/record")
    def record_session(pid: str):
        project_or_404(pid)
        from .voice.recorded import session
        return guard(session, store, pid)

    @app.post("/api/projects/{pid}/record/build")   # declared before /record/{line}
    def record_build(pid: str):
        project_or_404(pid)
        from .voice.recorded import build, missing_lines
        miss = guard(missing_lines, store, pid)
        if miss:
            raise HTTPException(400, f"lines still to record: {', '.join(map(str, miss))}")
        job = jobs.submit("narrate", lambda pr: build(store, pid, pr), project_id=pid)
        return {"job_id": job.id}

    @app.post("/api/projects/{pid}/record/{line}")
    async def record_take(pid: str, line: int, file: UploadFile = File(...)):
        project_or_404(pid)
        from starlette.concurrency import run_in_threadpool
        from .voice.recorded import process_take
        data = await file.read()
        if len(data) < 1000:
            raise HTTPException(400, "empty recording")
        return await run_in_threadpool(guard, process_take, store, pid, line, data)

    @app.post("/api/projects/{pid}/record/{line}/pin")
    def record_pin(pid: str, line: int, body: dict = Body(...)):
        project_or_404(pid)
        from .voice.recorded import pin
        return guard(pin, store, pid, line, body.get("take"))

    @app.post("/api/script/draft")
    def draft(body: dict = Body(...)):
        from .llm.claude import available
        from .llm.tasks import ClaudeTasks
        ok, why = available()
        if not ok:
            raise HTTPException(503, why)
        return ClaudeTasks().draft_script(body.get("topic", ""), body.get("length", "30-60s"))

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/")
        def index():
            return FileResponse(WEB_DIR / "index.html")

    return app


def _ranged_file(path: Path, request: Request):
    """FileResponse with explicit HTTP Range (206) so <video> can seek large files."""
    import mimetypes
    size = path.stat().st_size
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix.lower() == ".mov":
        ctype = "video/quicktime"
    rng = request.headers.get("range")
    if not rng or not rng.startswith("bytes="):
        return FileResponse(path, media_type=ctype, headers={"Accept-Ranges": "bytes"})
    start_s, _, end_s = rng[6:].split(",")[0].partition("-")
    try:
        if start_s:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        else:
            start, end = max(0, size - int(end_s)), size - 1
    except ValueError:
        raise HTTPException(416, "bad range")
    end = min(end, size - 1)
    if start > end or start >= size:
        return JSONResponse({"error": "range not satisfiable"}, status_code=416,
                            headers={"Content-Range": f"bytes */{size}"})

    def body():
        with path.open("rb") as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = fh.read(min(1 << 20, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk
    return StreamingResponse(body(), status_code=206, media_type=ctype, headers={
        "Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1)})


app = None  # created by `reel serve` (uvicorn factory)


def get_app() -> FastAPI:
    return create_app()
