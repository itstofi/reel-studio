"""Run every QA check on the delivered file; a crashing check is a FAIL, never a skip."""
from __future__ import annotations

import json
import traceback
from pathlib import Path

from pydantic import TypeAdapter

from ..config import SETTINGS
from ..models import CaptionCue, CheckResult, Layout, Plan, QAReport, Timeline
from ..store import ProjectStore, read_json, write_json
from ..util.proc import ProgressFn
from . import checks as C


def run_qa(project_id: str, store: ProjectStore, settings=SETTINGS, progress: ProgressFn | None = None,
           transcriber=None) -> QAReport:
    from ..media.audioio import SR, read_audio

    project = store.load(project_id)
    paths = store.paths(project_id)
    if not project.final_path or not Path(project.final_path).exists():
        raise FileNotFoundError("no delivered file — run render first")
    final = Path(project.final_path)
    if project.mode == "narrated":
        return _run_narrated(project, paths, final, settings, progress, transcriber)
    plan = read_json(paths.plan, Plan)
    timeline = read_json(paths.timeline, Timeline)
    cues = read_json(paths.captions_json, list[CaptionCue]) if paths.captions_json.exists() else []
    layout = read_json(paths.layout, Layout) if paths.layout.exists() else None
    inserts_file = paths.render_dir / "inserts.json"
    clips = json.loads(inserts_file.read_text()) if inserts_file.exists() else []
    lang = project.settings.lang
    if transcriber is None:
        from ..asr.engines import get_transcriber
        transcriber = get_transcriber(settings)
    delivered = read_audio(final, sr=SR)

    plan_steps = [
        ("delivery_spec", lambda: C.delivery_spec(final, settings)),
        ("av_duration", lambda: C.av_duration(final, timeline, settings)),
        ("timeline_invariants", lambda: C.timeline_invariants(timeline, plan, cues, clips)),
        ("captions_check", lambda: C.captions_check(cues, paths.captions_ass, layout, timeline.duration, settings)),
        ("face_occlusion", lambda: C.face_occlusion(clips, layout) if layout else
            CheckResult(name="face_occlusion", status="fail", summary="no layout.json")),
        ("mirror_rule", lambda: C.mirror_rule(project)),
        ("loudness", lambda: C.loudness(final, settings)),
        ("black_freeze", lambda: C.black_freeze(final, settings)),
        ("final_gap_scan", lambda: C.final_gap_scan(delivered, timeline, plan, settings)),
        ("stutter_scan", lambda: C.stutter_scan(delivered, lang, transcriber, settings)),
        ("closed_gaps_ab", lambda: C.closed_gaps_ab(project, plan, timeline, delivered, transcriber, settings)),
        ("script_coverage", lambda: C.script_coverage(delivered, project.script, lang, transcriber, project.takes)),
    ]
    return _execute(plan_steps, final, paths, progress)


def _run_narrated(project, paths, final, settings, progress, transcriber) -> QAReport:
    """Faceless voice-over reels: no takes/plan/timeline — spec, captions, sound, and the voice heard back."""
    from ..media.audioio import SR, read_audio
    spec = json.loads((paths.root / "narration.json").read_text())
    lines = [l["text"] for l in spec["lines"]]
    lang = spec.get("lang", "en")
    if transcriber is None:
        from ..asr.engines import get_transcriber
        transcriber = get_transcriber(settings)
    delivered = read_audio(final, sr=SR)
    cues = read_json(paths.captions_json, list[CaptionCue]) if paths.captions_json.exists() else []
    layout = read_json(paths.layout, Layout) if paths.layout.exists() else None
    duration = len(delivered) / SR
    steps = [
        ("delivery_spec", lambda: C.delivery_spec(final, settings)),
        ("captions_check", lambda: C.captions_check(cues, paths.captions_ass, layout, duration, settings)),
        ("loudness", lambda: C.loudness(final, settings)),
        ("black_freeze", lambda: C.black_freeze(final, settings)),
        ("stutter_scan", lambda: C.stutter_scan(delivered, lang, transcriber, settings)),
        ("pronunciation_lines", lambda: C.pronunciation_lines(paths.voice_dir, lines, lang, transcriber, settings)),
        ("pronunciation_final", lambda: C.pronunciation_final(delivered, lines, cues, lang, transcriber, settings)),
    ]
    return _execute(steps, final, paths, progress)


def _execute(plan_steps, final, paths, progress) -> QAReport:
    report = QAReport(file=str(final))
    for i, (name, fn) in enumerate(plan_steps):
        if progress:
            progress(i / len(plan_steps), f"QA: {name}")
        try:
            report.checks.append(fn())
        except Exception as exc:  # a check that cannot run is not a pass
            report.checks.append(CheckResult(name=name, status="fail", summary=f"check crashed: {exc}",
                                             details={"traceback": traceback.format_exc()[-2000:]}))
    paths.qa_dir.mkdir(parents=True, exist_ok=True)
    write_json(paths.qa_report, {**report.model_dump(mode="json"), "passed": report.passed})
    if progress:
        progress(1.0, "QA " + ("PASSED" if report.passed else "FAILED"))
    return report
