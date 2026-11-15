"""Narrated (faceless) reels: voice-over + full-frame motion graphics, no footage.

script JSON: {"title": str, "lang": "en"|"ar", "engine": "chatterbox"|"elevenlabs"|"say", "voice": "default"|"jweed",
              "lines": [{"text": spoken line, "graphic": {"template": ..., "params": {...}}}, ...]}
Each line is voiced separately (exact timing per line), the graphics span their line, English word-synced
captions come from re-transcribing the generated voice, and delivery goes through the same final encode
(2160x3840, 60 fps, HEVC hvc1, Rec.709, -14 LUFS).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .config import SETTINGS
from .edit.captions import chunk, write_ass, _finalize
from .edit.align import _align
from .media.audioio import SR, read_audio, write_wav
from .models import Box, CaptionCue, CaptionWord, Layout, Span, Word
from .motion.hyperframes import InsertSpec, render_inserts
from .motion.templates import render_template
from .store import ProjectStore, write_json
from .util.proc import ProgressFn, run_ffmpeg
from .util.textnorm import words as norm_words

GAP_S = 0.30          # pause between lines
TAIL_S = 0.6
W, H = 2160, 3840
LAYOUT = Layout(canvas_w=W, canvas_h=H, head=None, caption_band=Span(start=2780, end=3080),
                graphics_bands=[Span(start=620, end=2600)], safe_x0=160, safe_x1=2000)
BG = "0x0b0f1a"


def _line_audio(store, pid, i, line, spec, project, transcriber, log) -> tuple[Path, list, dict]:
    """Voice one line through the pronunciation-QA loop (heard back, regenerated until every word is right).
    `line["say"]` optionally overrides what the TTS reads; captions always show `line["text"]`."""
    from .voice.engines import get_tts
    from .voice.pronounce import synth_checked
    out = store.paths(pid).voice_dir / f"narr_{i + 1:02d}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    log(f"line {i + 1}: {line['text']}")
    if spec.get("voice_source") == "recorded":   # the creator's own take (cleaned + heard back at record time)
        from .voice.recorded import line_audio
        wav, heard, row = line_audio(store, pid, i, out)
        log(f"  recorded take {row['take']}" + ("" if row["ok"] else " (pinned, flagged: " + "; ".join(row["problems"]) + ")"))
        return wav, heard, row
    meta = out.with_suffix(".check.json")
    key = {"text": line["text"], "say": line.get("say"), "engine": spec.get("engine", "chatterbox"),
           "voice": spec.get("voice", "default")}
    if out.exists() and meta.exists():   # same line already voiced + heard back: reuse
        cached = json.loads(meta.read_text())
        if cached.get("key") == key:
            log("  cached")
            return out, [Word(**w) for w in cached["heard"]], cached["row"]
    wav, heard, row = synth_checked(get_tts(spec.get("engine", "chatterbox")), line["text"], say=line.get("say"),
                         lang=spec.get("lang", "en"), voice=spec.get("voice", "default"), out=out,
                         transcriber=transcriber, project=project, store=store, log=log)
    meta.write_text(json.dumps({"key": key, "row": row, "heard": [
        {"text": w.text, "start": w.start, "end": w.end} for w in heard]}, ensure_ascii=False))
    return wav, heard, row


def _caption_words(text: str, audio: np.ndarray, offset: float, lang: str, transcriber,
                   spoken=None) -> list[CaptionWord]:
    """Script words (exact spelling) timed by whisper's words on the generated audio."""
    from .asr.transcribe import transcribe_array
    from .edit.captions import distribute
    tokens = text.split()
    if spoken is None:
        spoken = transcribe_array(audio, language=lang, transcriber=transcriber)
    flat = [(t, w.start, w.end) for w in spoken for t in (norm_words(w.text) or [w.text])]
    match = _align([(norm_words(t) or [t.lower()])[0] for t in tokens], [f[0] for f in flat]) if flat else None
    dur = len(audio) / SR
    if not match or len(match.pairs) < max(1, len(tokens) // 2):
        return distribute(tokens, [(offset, offset + dur)])
    times = {li: (flat[wj][1], flat[wj][2]) for li, wj, _ in match.pairs}
    out, last = [], 0.0
    for i, tok in enumerate(tokens):
        if i in times:
            a, b = times[i]
        else:  # interpolate between neighbours
            nxt = next((times[k][0] for k in range(i + 1, len(tokens)) if k in times), dur)
            a, b = last, max(last + 0.1, (last + nxt) / 2)
        a = max(a, last)
        out.append(CaptionWord(text=tok, start=round(offset + a, 3), end=round(offset + max(b, a + 0.08), 3)))
        last = max(b, a + 0.08)
    return out


def _translated_caption(caption: str, heard, offset: float, dur: float) -> list[CaptionWord]:
    """English caption words spread over the spoken spans of a non-English line (pauses skipped)."""
    from .edit.captions import distribute
    spans: list[list[float]] = []
    for w in sorted(heard, key=lambda w: w.start):
        a, b = offset + max(0.0, w.start), offset + min(dur, max(w.end, w.start + 0.05))
        if spans and a - spans[-1][1] < 0.25:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return distribute(caption.split(), [tuple(x) for x in spans] or [(offset, offset + dur)])


def build_narrated(store: ProjectStore, pid: str, spec: dict, progress: ProgressFn | None = None) -> Path:
    from .asr.engines import get_transcriber
    from .media.render import final_encode
    report = progress or (lambda f, m: None)
    project = store.load(pid)
    pp = store.paths(pid)
    lang = spec.get("lang", "en")
    lines = spec["lines"]
    if spec.get("voice_source") == "recorded":
        from .voice.recorded import missing_lines
        if miss := missing_lines(store, pid):
            raise ValueError(f"lines still to record: {', '.join(map(str, miss))} (run `reel record {pid}`)")
    write_json(pp.root / "narration.json", spec)

    # 1. voice per line (pronunciation QA: heard back + regenerated), assembled on one timeline
    from .voice.pronounce import write_report
    report(0.0, "voice")
    tr = get_transcriber(SETTINGS)
    log_path = pp.voice_dir / "pronunciation.log"
    pp.voice_dir.mkdir(parents=True, exist_ok=True)
    log_path.write_text("")

    def log(msg: str) -> None:
        with log_path.open("a") as fh:
            fh.write(msg + "\n")

    clips, starts, heards, rows, t = [], [], [], [], 0.4
    for i, line in enumerate(lines):
        wav, heard, row = _line_audio(store, pid, i, line, spec, project, tr, log)
        rows.append({"line": i + 1, **row})
        heards.append(heard)
        write_report(pp.voice_dir / "pronunciation.json", rows)
        x = read_audio(wav, sr=SR)
        starts.append(t)
        clips.append(x)
        t += len(x) / SR + GAP_S
        report(0.3 * (i + 1) / len(lines), f"voice {i + 1}/{len(lines)}")
    total = round((t - GAP_S + TAIL_S) * 60) / 60
    narration = np.zeros(int(total * SR), dtype=np.float32)
    for x, s in zip(clips, starts):
        a = int(s * SR)
        narration[a:a + len(x)] += x[: len(narration) - a]
    nar_wav = write_wav(pp.render_dir / "narration.wav", narration, SR)

    # 2. captions from the generated voice
    report(0.35, "captions")
    cues: list[CaptionCue] = []
    for i, (line, x, s, heard) in enumerate(zip(lines, clips, starts, heards)):
        if line.get("caption"):   # Arabic cut: the English line is paced over his Arabic speech (house rule)
            words = _translated_caption(line["caption"], heard, s, len(x) / SR)
        else:
            words = _caption_words(line["text"], x, s, lang, tr, heard)
        cues += chunk(words, f"B{i + 1:02d}", SETTINGS.layout,
                      start_index=len(cues))
    cues = _finalize(cues, total)
    write_json(pp.captions_json, cues)
    write_json(pp.layout, LAYOUT)
    ass = write_ass(cues, LAYOUT, pp.captions_ass)

    # 3. one HyperFrames graphic per line, spanning the line
    report(0.45, "graphics")
    inserts = []
    presenter = spec.get("presenter")
    if presenter:   # anchor card owns y 2140-2740: cards move up, photo headlines end above it
        box, zoom = Box(x0=LAYOUT.safe_x0, y0=700, x1=LAYOUT.safe_x1, y1=2080), 1.3
    else:
        box, zoom = Box(x0=LAYOUT.safe_x0, y0=1050, x1=LAYOUT.safe_x1, y1=2650), 1.6
    import shutil
    from .config import FONTS_DIR
    for i, (line, x, s) in enumerate(zip(lines, clips, starts)):
        graphics = line.get("graphics") or ([line["graphic"]] if line.get("graphic") else [])
        nxt = starts[i + 1] if i + 1 < len(starts) else total
        for j, g in enumerate(graphics):
            gid = f"N{i + 1:02d}{chr(97 + j)}"
            start = round(max(0.0, s - 0.15 + float(g.get("offset", 0))) * 60) / 60
            if i == 0 and j == 0:   # the first frame is the thumbnail/hook: picture on screen from frame 0
                start = 0.0
            dur = round(max(1.2, nxt - start - 0.02) * 60) / 60
            proj = pp.inserts_dir / gid
            (proj / "assets").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FONTS_DIR / "NotoKufiArabic-Bold.ttf", proj / "assets" / "NotoKufiArabic-Bold.ttf")
            params = dict(g.get("params", {}))
            if i == 0 and j == 0 and g["template"] == "photo":
                params.setdefault("cut_in", True)
            if presenter and g["template"] == "photo":
                params.setdefault("news_bottom", 1800)
            if params.get("src"):  # pictures are copied in: HyperFrames never fetches at render
                src = Path(params["src"]).expanduser()
                if not src.exists():
                    raise FileNotFoundError(f"line {i + 1}: picture not found: {src}")
                shutil.copyfile(src, proj / "assets" / src.name)
                params["src"] = f"assets/{src.name}"
            gbox = dict(g.get("box") or {"x": box.x0, "y": box.y0, "w": box.x1 - box.x0, "h": box.y1 - box.y0,
                                          "zoom": zoom})
            html = render_template(g["template"], gid, dur, gbox, params)
            (proj / "index.html").write_text(html, encoding="utf-8")
            inserts.append(InsertSpec(graphic_id=gid, beat_id=f"B{i + 1:02d}", template=g["template"],
                                      comp_path=str(proj), start=start, duration=dur, box=box))
    overlays = render_inserts(inserts, pp.render_dir / "graphics", progress=lambda f, m: report(0.45 + 0.3 * f, m))
    if presenter:
        from .motion.presenter import render_presenter
        report(0.74, "presenter")
        src = Path(presenter["photo"]).expanduser()
        if not src.exists():
            raise FileNotFoundError(f"presenter photo not found: {src}")
        clip = render_presenter(src, nar_wav, total, pp.render_dir / "graphics" / "presenter.mov",
                                title=presenter.get("title", ""), subtitle=presenter.get("subtitle", ""),
                                crop=tuple(presenter["crop"]) if presenter.get("crop") else None)
        overlays.append((clip, 0.0, 160, 2140))
    (pp.render_dir / "inserts.json").write_text(json.dumps(
        [{"graphic_id": i.graphic_id, "path": str(o[0]), "start": o[1], "duration": i.duration}
         for i, o in zip(inserts, overlays)], indent=2))

    # 4. moving background + narration -> mezzanine, then the standard delivery encode
    report(0.75, "background")
    cut = pp.render_dir / "cut.mov"
    need = int(total * 0.12e9) + 2_000_000_000   # ProRes mezzanine ~100 MB/s of video + the delivery file
    free = shutil.disk_usage(pp.render_dir).free
    if free < need:
        raise RuntimeError(f"not enough disk space: {free / 1e9:.1f} GB free, the encode needs ~{need / 1e9:.0f} GB")
    bg = (f"gradients=s={W}x{H}:r=60:c0=0x0b0f1a:c1=0x1d2340:c2=0x2a1d3f:c3=0x0b0f1a:nb_colors=4:speed=0.012:"
          f"type=radial:d={total:.3f},noise=alls=1:allf=t,format=p210le,"
          "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")
    run_ffmpeg(["-f", "lavfi", "-i", bg, "-i", str(nar_wav), "-map", "0:v", "-map", "1:a", "-c:v", "prores_videotoolbox",
                "-profile:v", "2", "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "2", "-t", f"{total:.3f}", str(cut)],
               ffmpeg=SETTINGS.ffmpeg)
    final = pp.final
    final_encode(cut, final, captions_ass=ass, overlays=overlays,
                 progress=lambda f, m: report(0.8 + 0.2 * f, m))
    cut.unlink(missing_ok=True)   # 8 GB/min mezzanine, rebuilt in ~1 min when needed
    store.update(pid, lambda p: setattr(p, "final_path", str(final)))
    report(1.0, "done")
    return final
