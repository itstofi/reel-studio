"""Creator-recorded narration: the creator reads each script line into the laptop mic (record dashboard,
`reel record`), every take is cleaned (high-pass, spectral noise gate, trim to the speech with 0.25 s pads,
long pauses tightened only when the A/B transcript is unchanged, levelled, limited) and heard back by whisper
against the script line. Last clean take wins (same rule as his camera takes); the build then uses these takes
instead of TTS — everything else in the narrated build (graphics, captions, presenter, QA) is unchanged.

Files: <project>/voice/rec/line_XX_tNN.raw.wav (as recorded) + .clean.wav (what the build uses),
       <project>/voice/rec/takes.json (manifest), <project>/voice/guide/line_XX.wav (optional AI read, a pace guide).
"""
from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path

import numpy as np
from scipy.signal import butter, istft, sosfiltfilt, stft
from scipy.ndimage import uniform_filter

from ..config import SETTINGS, RecordSpec
from ..media.audioio import SR, read_audio, write_wav
from ..media.speech import frame_levels, merge_spans, runs_from_mask
from ..models import Word
from ..util.proc import run_ffmpeg

FRAME_S = 0.010
_LOCK = threading.Lock()          # one take processed at a time (whisper + files)
_PUNCT_PAUSE = re.compile(r"[,.:;!?،؛؟…]$")


# ── paths + manifest ─────────────────────────────────────────────────────────
def rec_dir(store, pid) -> Path:
    return store.paths(pid).voice_dir / "rec"


def guide_path(store, pid, line: int) -> Path:
    return store.paths(pid).voice_dir / "guide" / f"line_{line:02d}.wav"


def load_spec(store, pid) -> dict:
    f = store.paths(pid).root / "narration.json"
    if not f.exists():
        raise FileNotFoundError(f"{pid} has no narration.json (recording works on narrated projects)")
    return json.loads(f.read_text())


def load_manifest(store, pid) -> dict:
    f = rec_dir(store, pid) / "takes.json"
    return json.loads(f.read_text()) if f.exists() else {"lines": {}}


def _save_manifest(store, pid, man: dict) -> None:
    f = rec_dir(store, pid) / "takes.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(man, indent=2, ensure_ascii=False))
    tmp.replace(f)


def chosen(entry: dict) -> dict | None:
    """The creator's pinned take, else the LAST clean take (house rule: last clean repeat wins)."""
    takes = entry.get("takes", [])
    if entry.get("pinned"):
        return next((t for t in takes if t["n"] == entry["pinned"]), None)
    return next((t for t in reversed(takes) if t["ok"]), None)


def chosen_take(store, pid, line: int) -> dict:
    entry = load_manifest(store, pid)["lines"].get(str(line), {})
    t = chosen(entry)
    if not t:
        raise ValueError(f"line {line}: no clean recording yet (record it, or pin a take)")
    return t


def missing_lines(store, pid) -> list[int]:
    n = len(load_spec(store, pid)["lines"])
    man = load_manifest(store, pid)["lines"]
    return [i for i in range(1, n + 1) if not chosen(man.get(str(i), {}))]


# ── cleanup (numpy + one ffmpeg pass) ────────────────────────────────────────
def _noise_floor_db(db: np.ndarray) -> float:
    return float(np.percentile(db, 10)) if db.size else -90.0


def speech_spans(x: np.ndarray, sr: int, spec: RecordSpec):
    """Speech runs: floor -52 dB, raised to noise + margin in a noisy room. Key clicks / blips at the
    edges (pressing Space) are dropped: a block needs >= 0.3 s of speech."""
    db, _ = frame_levels(x, sr, FRAME_S)
    thr = max(spec.floor_db, _noise_floor_db(db) + spec.floor_margin_db)
    runs = [s for s in merge_spans(runs_from_mask(db > thr, FRAME_S), 0.10) if s.dur >= 0.05]
    blocks, cur = [], []
    for r in runs:
        if cur and r.start - cur[-1].end >= 0.7:
            blocks.append(cur)
            cur = []
        cur.append(r)
    if cur:
        blocks.append(cur)
    blocks = [b for b in blocks if sum(r.dur for r in b) >= 0.30]
    return [r for b in blocks for r in b], thr


def _denoise(x: np.ndarray, sr: int, quiet: np.ndarray, spec: RecordSpec) -> np.ndarray:
    """Spectral gate. Noise profile from the non-speech frames (the countdown pre-roll is room tone);
    gain floor keeps it natural (no musical noise / underwater sound)."""
    if len(x) < sr // 4:
        return x
    f, t, X = stft(x, fs=sr, nperseg=1024, noverlap=768)
    mag = np.abs(X)
    hop_s = 256 / sr
    frame_t = (np.arange(mag.shape[1]) * hop_s / FRAME_S).astype(int).clip(0, len(quiet) - 1)
    qmask = quiet[frame_t]
    noise = (np.mean(mag[:, qmask], axis=1) if qmask.sum() >= 10 else np.percentile(mag, 15, axis=1))
    floor = 10 ** (spec.denoise_floor_db / 20)
    gain = np.clip((mag - spec.denoise_over * noise[:, None]) / (mag + 1e-12), floor, 1.0)
    gain = uniform_filter(gain, size=(3, 5))   # smooth across freq/time
    _, y = istft(X * gain, fs=sr, nperseg=1024, noverlap=768)
    return y[: len(x)].astype(np.float32)


def _cut_long_gaps(x: np.ndarray, sr: int, runs, spec: RecordSpec) -> tuple[np.ndarray, list[dict]]:
    """Shorten pauses longer than close_gap_s to keep_gap_s (cut from the middle, 15 ms crossfade)."""
    pieces, closed, pos = [], [], 0
    fade = int(0.015 * sr)
    for a, b in zip(runs, runs[1:]):
        gap = b.start - a.end
        if gap <= spec.close_gap_s:
            continue
        keep = spec.keep_gap_s / 2
        cut0, cut1 = int((a.end + keep) * sr), int((b.start - keep) * sr)
        pieces.append(x[pos:cut0].copy())
        pos = cut1
        closed.append({"at": round(a.end, 2), "from_s": round(gap, 2), "to_s": spec.keep_gap_s})
    if not closed:
        return x, []
    pieces.append(x[pos:].copy())
    out = pieces[0]
    ramp = np.linspace(0, 1, fade, dtype=np.float32)
    for p in pieces[1:]:
        if len(out) > fade and len(p) > fade:
            out[-fade:] = out[-fade:] * (1 - ramp) + p[:fade] * ramp
            p = p[fade:]
        out = np.concatenate([out, p])
    return out, closed


def _level(x: np.ndarray, sr: int, spec: RecordSpec) -> np.ndarray:
    runs, _ = speech_spans(x, sr, spec)
    if runs:
        sp = np.concatenate([x[int(r.start * sr):int(r.end * sr)] for r in runs])
        rms = float(np.sqrt(np.mean(sp.astype(np.float64) ** 2)) + 1e-12)
        x = x * (10 ** (spec.speech_rms_db / 20) / rms)
    return x.astype(np.float32)


def _finish(x: np.ndarray, out: Path, spec: RecordSpec) -> Path:
    """Gentle compressor + true limiter (ffmpeg), 10 ms edge fades."""
    n = min(len(x) // 4, int(0.01 * SR))
    if n:
        x[:n] *= np.linspace(0, 1, n, dtype=np.float32)
        x[-n:] *= np.linspace(1, 0, n, dtype=np.float32)
    tmp = out.with_suffix(".pre.wav")
    write_wav(tmp, np.clip(x, -4, 4) / 4, SR)  # headroom: 16-bit wav, gain restored in the filter
    lim = 10 ** (spec.peak_db / 20)
    run_ffmpeg(["-i", str(tmp), "-af", f"volume=4,acompressor=threshold=-24dB:ratio=2.5:attack=8:release=120,"
                f"alimiter=limit={lim:.3f}:attack=2:release=40:level=0", "-ar", str(SR), "-ac", "1",
                "-c:a", "pcm_s16le", str(out)], ffmpeg=SETTINGS.ffmpeg)
    tmp.unlink(missing_ok=True)
    return out


def analyse_raw(x: np.ndarray, sr: int, spec: RecordSpec) -> dict:
    """Raw-take metrics the creator can act on (mic distance, clipping, room noise)."""
    db, peak = frame_levels(x, sr, FRAME_S)
    runs, thr = speech_spans(x, sr, spec)
    noise = _noise_floor_db(db)
    if runs:
        sp = np.concatenate([x[int(r.start * sr):int(r.end * sr)] for r in runs])
        speech = float(20 * np.log10(np.sqrt(np.mean(sp.astype(np.float64) ** 2)) + 1e-12))
    else:
        speech = -90.0
    pk = float(20 * np.log10(np.max(np.abs(x)) + 1e-12)) if len(x) else -90.0
    clip_lin = 10 ** (spec.clip_db / 20)
    return {"peak_db": round(pk, 1), "speech_db": round(speech, 1), "noise_db": round(noise, 1),
            "snr_db": round(speech - noise, 1), "clipped": int(np.sum(np.abs(x) >= clip_lin)),
            "threshold_db": round(thr, 1), "speech_s": round(sum(r.dur for r in runs), 2),
            "raw_s": round(len(x) / sr, 2)}


def clean(x: np.ndarray, sr: int, spec: RecordSpec) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """-> (trimmed, tightened, closed gaps). Both versions are denoised, levelled and padded."""
    sos = butter(2, spec.highpass_hz, btype="highpass", fs=sr, output="sos")
    y = sosfiltfilt(sos, x).astype(np.float32)
    runs, thr = speech_spans(y, sr, spec)
    db, _ = frame_levels(y, sr, FRAME_S)
    quiet = db < thr - 3
    if not quiet.size:
        quiet = np.zeros(1, bool)
    y = _denoise(y, sr, quiet, spec)
    if not runs:
        return y[:0], y[:0], []
    a = max(0.0, runs[0].start - spec.pad_s)
    b = min(len(y) / sr, runs[-1].end + spec.pad_s)
    trimmed = y[int(a * sr):int(b * sr)]
    shifted = [type(r)(start=r.start - a, end=r.end - a) for r in runs]
    tight, closed = _cut_long_gaps(trimmed, sr, shifted, spec)
    return _level(trimmed, sr, spec), _level(tight, sr, spec), closed


# ── direction (how to read each line) ────────────────────────────────────────
_LATIN = re.compile(r"[A-Za-z]")


def _stressed(w: str, k: int, lang: str, aliases: set[str]) -> bool:
    """Numbers and names: digits; in Arabic any Latin word or a known brand spelling; in English a
    capitalised word that does not start the sentence (or an acronym)."""
    bare = w.strip("،,.؟?:;!\"'()")
    if any(c.isdigit() for c in bare):
        return True
    if lang == "ar":
        return bool(_LATIN.search(bare)) or bare in aliases or re.sub(r"^(وال|بال|و|ب|ال)", "", bare) in aliases
    return len(bare) > 1 and (bare.isupper() or (k > 0 and bare[0].isupper()))


def direction(spec: dict, settings=SETTINGS) -> list[dict]:
    """Teleprompter data per line: words, which to stress, where to breathe, a reading note, target length."""
    lang = spec.get("lang", "en")
    lines = spec["lines"]
    pace = dict(settings.record.pace_wps).get(lang, 2.4)
    aliases = {a for a, _ in settings.pronounce.aliases}
    out = []
    for i, line in enumerate(lines):
        words = line["text"].split()
        stress = [k for k, w in enumerate(words) if _stressed(w, k, lang, aliases)]
        pauses = [k for k, w in enumerate(words[:-1]) if _PUNCT_PAUSE.search(w)]
        text = line["text"]
        if line.get("direction"):
            note = line["direction"]
        elif i == 0:
            note = "Hook: punchy and confident, hit the first word, no warm-up."
        elif i == len(lines) - 1:
            note = "Close: warmer and slower, like talking to one friend. Smile."
        elif text.rstrip().endswith(("?", "؟")):
            note = "Question: lift your voice at the end, then let it land."
        elif any(c.isdigit() for c in text) or stress:
            note = "Slow down on the bold words: numbers and names must be crystal clear."
        else:
            note = "Steady news pace, finish the sentence down."
        out.append({"line": i + 1, "text": text, "caption": line.get("caption"), "words": words,
                    "stress": stress, "pauses": pauses, "note": note,
                    "target_s": round(len(words) / pace + 0.25 * len(pauses), 1)})
    return out


# ── one take: clean + hear back + verdict ────────────────────────────────────
def _heard_tokens(words) -> list[str]:
    from ..util.textnorm import words as norm
    return [t for w in words for t in norm(w.text)]


def process_take(store, pid, line: int, raw_bytes: bytes, *, transcriber=None, settings=SETTINGS) -> dict:
    """Save a take for script line `line` (1-based), clean it, hear it back. Returns the line state."""
    from ..asr.engines import get_transcriber
    from ..asr.transcribe import transcribe_array
    from .pronounce import check
    spec = load_spec(store, pid)
    if not 1 <= line <= len(spec["lines"]):
        raise ValueError(f"line {line} is not in the script (1..{len(spec['lines'])})")
    text = spec["lines"][line - 1]["text"]
    lang = spec.get("lang", "en")
    rs = settings.record
    tr = transcriber or get_transcriber(settings)
    with _LOCK:
        man = load_manifest(store, pid)
        entry = man["lines"].setdefault(str(line), {"takes": [], "pinned": None})
        n = max((t["n"] for t in entry["takes"]), default=0) + 1
        d = rec_dir(store, pid)
        d.mkdir(parents=True, exist_ok=True)
        stem = f"line_{line:02d}_t{n:02d}"
        upload = d / f"{stem}.upload"
        upload.write_bytes(raw_bytes)
        x = read_audio(upload, sr=SR)          # any browser format (wav / webm / m4a) -> 48 kHz mono
        upload.unlink(missing_ok=True)
        raw = write_wav(d / f"{stem}.raw.wav", x, SR)
        metrics = analyse_raw(x, SR, rs)
        trimmed, tight, closed = clean(x, SR, rs)
        problems, warnings, heard = [], [], []
        if not len(trimmed):
            problems.append("no speech heard: check the microphone")
            use = trimmed
        else:
            heard = transcribe_array(tight, language=lang, transcriber=tr)
            use = tight
            if closed:   # A/B: a tightened pause must not change a single word, else keep the pause
                heard_long = transcribe_array(trimmed, language=lang, transcriber=tr)
                if _heard_tokens(heard_long) != _heard_tokens(heard):
                    warnings.append("kept your long pause (tightening it changed the words)")
                    use, heard, closed = trimmed, heard_long, []
            res = check(text, heard, settings)
            problems += [str(p) for p in res.problems]
        if metrics["clipped"] > 20:
            problems.append("clipping: lower the input or move back a little")
        if metrics["speech_db"] < rs.quiet_db and len(trimmed):
            warnings.append("quiet: move closer to the laptop")
        if metrics["snr_db"] < rs.min_snr_db and len(trimmed):
            warnings.append(f"noisy room (speech only {metrics['snr_db']:.0f} dB above the noise)")
        cleaned = _finish(use.copy(), d / f"{stem}.clean.wav", rs) if len(use) else None
        take = {"n": n, "ok": not problems, "problems": problems, "warnings": warnings,
                "raw": str(raw.relative_to(store.paths(pid).root)),
                "clean": str(cleaned.relative_to(store.paths(pid).root)) if cleaned else None,
                "duration": round(len(use) / SR, 2), "gaps_closed": closed, "metrics": metrics,
                "heard_text": " ".join(w.text for w in heard),
                "heard": [w.model_dump() for w in heard]}
        entry["takes"].append(take)
        entry["pinned"] = None   # a new take re-opens the choice: last clean one wins
        _save_manifest(store, pid, man)
    return line_state(store, pid, line)


def pin(store, pid, line: int, n: int | None) -> dict:
    with _LOCK:
        man = load_manifest(store, pid)
        entry = man["lines"].setdefault(str(line), {"takes": [], "pinned": None})
        if n is not None and not any(t["n"] == n for t in entry["takes"]):
            raise ValueError(f"line {line} has no take {n}")
        entry["pinned"] = n
        _save_manifest(store, pid, man)
    return line_state(store, pid, line)


def line_state(store, pid, line: int) -> dict:
    entry = load_manifest(store, pid)["lines"].get(str(line), {"takes": [], "pinned": None})
    c = chosen(entry)
    return {"line": line, "takes": [{k: v for k, v in t.items() if k != "heard"} for t in entry["takes"]],
            "pinned": entry.get("pinned"), "chosen": c["n"] if c else None}


def session(store, pid) -> dict:
    """Everything the record dashboard needs."""
    spec = load_spec(store, pid)
    root = store.paths(pid).root
    lines = direction(spec)
    for ln in lines:
        g = guide_path(store, pid, ln["line"])
        ln["guide"] = str(g.relative_to(root)) if g.exists() else None
        if ln["guide"]:   # the AI read sets the pace when there is one
            from ..media.audioio import duration_of
            ln["target_s"] = round(duration_of(g), 1)
        ln.update(line_state(store, pid, ln["line"]))
    p = store.load(pid)
    final = store.paths(pid).final
    return {"project": pid, "name": p.name, "lang": spec.get("lang", "en"), "title": spec.get("title"),
            "lines": lines, "missing": missing_lines(store, pid),
            "final": str(final.relative_to(root)) if final.exists() else None}


# ── project wiring ───────────────────────────────────────────────────────────
def recording_project(store, src_pid: str) -> str:
    """The project to record into. A project already in recorded mode is used as is; otherwise a sibling
    '<name> (my voice)' is created (the AI-voice version stays untouched) and the AI lines are copied in
    as pace guides."""
    from ..pipeline import create_project
    spec = load_spec(store, src_pid)
    if spec.get("voice_source") == "recorded":
        return src_pid
    src = store.load(src_pid)
    name = f"{src.name} (my voice)"
    existing = next((p for p in store.list() if p.name == name), None)
    dst = existing or create_project(store, name, lang=spec.get("lang", "en"), mode="narrated")
    dpp = store.paths(dst.id)
    if not (dpp.root / "narration.json").exists():
        (dpp.root / "narration.json").write_text(json.dumps({**spec, "voice_source": "recorded",
                                                             "forked_from": src_pid}, indent=2, ensure_ascii=False))
    for i in range(1, len(spec["lines"]) + 1):
        g = store.paths(src_pid).voice_dir / f"narr_{i:02d}.wav"
        if g.exists() and not guide_path(store, dst.id, i).exists():
            guide_path(store, dst.id, i).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(g, guide_path(store, dst.id, i))
    return dst.id


def build(store, pid, progress=None) -> dict:
    """Build the reel with the recorded takes, then QA the delivered file."""
    from ..narrated import build_narrated
    from ..qa.run import run_qa
    miss = missing_lines(store, pid)
    if miss:
        raise ValueError(f"lines still to record (or pin): {', '.join(map(str, miss))}")
    spec = {**load_spec(store, pid), "voice_source": "recorded"}
    report = progress or (lambda f, m: None)
    final = build_narrated(store, pid, spec, lambda f, m: report(0.85 * f, m))
    report(0.86, "qa")
    rep = run_qa(pid, store)
    report(1.0, "done")
    return {"final": str(final), "qa_passed": rep.passed}


def line_audio(store, pid, i: int, out: Path) -> tuple[Path, list[Word], dict]:
    """narrated._line_audio for a recorded project: the chosen take of line i (0-based)."""
    t = chosen_take(store, pid, i + 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(store.paths(pid).root / t["clean"], out)
    heard = [Word(**w) for w in t["heard"]]
    return out, heard, {"text": load_spec(store, pid)["lines"][i]["text"], "source": "recorded", "take": t["n"],
                        "ok": t["ok"], "problems": t["problems"], "tries": []}
