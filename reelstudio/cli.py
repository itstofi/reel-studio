"""`reel` command line — same stages as the web UI (Claude Code drives the app through this)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import pipeline
from .store import ProjectStore


def _progress(frac: float, msg: str) -> None:
    sys.stderr.write(f"\r[{frac * 100:5.1f}%] {msg[:90]:<90}")
    sys.stderr.flush()
    if frac >= 1.0:
        sys.stderr.write("\n")


def _ensure_server(path: str) -> str:
    """Start `reel serve` in the background unless it is already answering; return the full URL."""
    import subprocess
    import time
    import urllib.request
    from .config import PROJECTS_DIR, SETTINGS
    base = f"http://{SETTINGS.host}:{SETTINGS.port}"

    def up() -> bool:
        try:
            return urllib.request.urlopen(base + "/api/health", timeout=1).status == 200
        except OSError:
            return False
    if not up():
        log = (PROJECTS_DIR.parent / "logs")
        log.mkdir(exist_ok=True)
        subprocess.Popen([sys.executable, "-m", "reelstudio.cli", "serve"], start_new_session=True,
                         stdout=open(log / "serve.log", "a"), stderr=subprocess.STDOUT)
        for _ in range(60):
            if up():
                break
            time.sleep(0.25)
        else:
            raise RuntimeError(f"web UI did not start (see {log / 'serve.log'})")
    return base + path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reel", description="Reel Studio — Jweed's reel editor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check tools, models and integrations")
    sub.add_parser("list", help="list projects")
    p = sub.add_parser("new", help="create a project (one per language cut)")
    p.add_argument("name"); p.add_argument("--lang", choices=["ar", "en"], default="ar")
    p = sub.add_parser("show", help="project status"); p.add_argument("id")
    p = sub.add_parser("import", help="import takes (symlinked, never modified)")
    p.add_argument("id"); p.add_argument("files", nargs="+"); p.add_argument("--lang", choices=["ar", "en"])
    p.add_argument("--no-mirror", action="store_true", help="NOT his footage (the mirror rule is for his takes)")
    p = sub.add_parser("script", help="set the line-aligned script")
    p.add_argument("id"); p.add_argument("--ar", type=Path); p.add_argument("--en", type=Path)
    for name in ("ingest", "analyze", "plan", "approve", "qa", "run"):
        p = sub.add_parser(name); p.add_argument("id")
    p = sub.add_parser("choose", help="pick another attempt for a beat")
    p.add_argument("id"); p.add_argument("beat"); p.add_argument("attempt", type=int, help="1-based")
    p = sub.add_parser("render"); p.add_argument("id"); p.add_argument("--preview", action="store_true")
    p = sub.add_parser("voice", help="generate speech")
    p.add_argument("id"); p.add_argument("--text", required=True); p.add_argument("--lang", default="ar")
    p.add_argument("--engine", default="say", choices=["elevenlabs", "chatterbox", "say"])
    p.add_argument("--voice", default="jweed")
    p = sub.add_parser("narrate", help="faceless reel: voice-over + motion graphics from a script JSON")
    p.add_argument("name"); p.add_argument("script", type=Path)
    p.add_argument("--project", help="re-build into an existing narrated project (reuses heard-back voice lines)")
    p = sub.add_parser("record", help="record the narration yourself: opens the teleprompter dashboard")
    p.add_argument("id", help="narrated project (an AI-voice project gets a '(my voice)' copy)")
    p.add_argument("--build", action="store_true", help="build + QA the reel from the recorded takes")
    p.add_argument("--no-open", action="store_true", help="don't open the browser")
    p = sub.add_parser("serve", help="web UI"); p.add_argument("--port", type=int); p.add_argument("--host")
    a = ap.parse_args(argv)
    store = ProjectStore()

    try:
        if a.cmd == "doctor":
            from .doctor import run_checks
            bad = 0
            for c in run_checks():
                bad += not c["ok"]
                print(f"{'OK ' if c['ok'] else 'NO '} {c['name']:<28} {c['detail']}" + (f"  -> {c['fix']}" if c["fix"] else ""))
            return 1 if bad else 0
        if a.cmd == "list":
            for pr in store.list():
                print(f"{pr.id:<32} {pr.settings.lang}  takes={len(pr.takes)}  "
                      + " ".join(f"{k}:{v.state}" for k, v in pr.stages.items()))
            return 0
        if a.cmd == "new":
            print(pipeline.create_project(store, a.name, lang=a.lang).id)
            return 0
        if a.cmd == "show":
            print(store.load(a.id).model_dump_json(indent=2))
            return 0
        if a.cmd == "import":
            pr = pipeline.import_takes(store, a.id, a.files, lang=a.lang, mirror=not a.no_mirror)
            for t in pr.takes:
                print(f"{t.id}  {t.filename}  mirror={t.mirror}")
            return 0
        if a.cmd == "script":
            pr = pipeline.set_script(store, a.id, ar_text=a.ar.read_text(encoding="utf-8") if a.ar else None,
                                     en_text=a.en.read_text(encoding="utf-8") if a.en else None)
            print(f"{len(pr.script.lines)} lines")
            return 0
        if a.cmd == "ingest":
            pipeline.ingest(store, a.id, _progress)
            return 0
        if a.cmd == "analyze":
            pipeline.analyze(store, a.id, _progress)
            return 0
        if a.cmd in ("plan", "run"):
            from .edit.planner import plan_summary
            pl = (pipeline.plan if a.cmd == "plan" else pipeline.run_until_approval)(store, a.id, _progress)
            print(plan_summary(pl))
            print("\nReview the plan above, then: reel approve", a.id)
            return 0
        if a.cmd == "choose":
            from .edit.planner import plan_summary
            print(plan_summary(pipeline.choose_attempt(store, a.id, a.beat, a.attempt - 1)))
            return 0
        if a.cmd == "approve":
            pipeline.approve(store, a.id)
            print("approved")
            return 0
        if a.cmd == "render":
            print(pipeline.render(store, a.id, _progress, preview=a.preview))
            return 0
        if a.cmd == "qa":
            rep = pipeline.qa(store, a.id, _progress)
            for c in rep.checks:
                print(f"{c.status.upper():<5} {c.name:<22} {c.summary}")
            print("QA PASSED" if rep.passed else "QA FAILED")
            return 0 if rep.passed else 2
        if a.cmd == "voice":
            clip = pipeline.generate_voice(store, a.id, text=a.text, lang=a.lang, engine=a.engine, voice=a.voice)
            print(json.dumps(clip.model_dump(mode="json"), indent=2, ensure_ascii=False))
            return 0
        if a.cmd == "narrate":
            from .narrated import build_narrated
            spec = json.loads(a.script.read_text(encoding="utf-8"))
            pr = (store.load(a.project) if a.project else
                  pipeline.create_project(store, a.name, lang=spec.get("lang", "en"), mode="narrated"))
            print(build_narrated(store, pr.id, spec, _progress))
            print(f"project: {pr.id}  ->  reel qa {pr.id}")
            return 0
        if a.cmd == "record":
            from .voice import recorded
            pid = recorded.recording_project(store, a.id)
            if a.build:
                res = recorded.build(store, pid, _progress)
                print(res["final"]); print("QA PASSED" if res["qa_passed"] else f"QA FAILED -> reel qa {pid}")
                return 0 if res["qa_passed"] else 2
            url = _ensure_server(f"/#/record/{pid}")
            print(f"project: {pid}\nrecord dashboard: {url}")
            if not a.no_open:
                import subprocess
                subprocess.run(["open", url], check=False)
            return 0
        if a.cmd == "serve":
            import uvicorn
            from .config import SETTINGS
            host, port = a.host or SETTINGS.host, a.port or SETTINGS.port
            print(f"Reel Studio on http://{host}:{port}")
            uvicorn.run("reelstudio.api:get_app", factory=True, host=host, port=port, log_level="warning")
            return 0
    except (pipeline.StageError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
