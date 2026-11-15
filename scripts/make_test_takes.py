"""Generate synthetic "Jweed" takes that exercise every hard rule, for end-to-end tests.

Speech: macOS `say` (Majed = Arabic, Samantha = English), scaled so speech RMS sits
around -42 dB like his mic, with true digital-zero pauses (his mic hard-gates).
Video: a public-domain portrait as the talking head, 2160x3840 @ 60 fps, MIRRORED like
his camera (a red marker on the TRUE left ends up on the right in the source; the
pipeline's hflip must put it back on the left), with light noise so frames change.

Takes (Arabic cut uses t01+t02, English cut uses t03):
  t01_ar.mov      SDR 8-bit FULL range: L1 partial retry, L1 keeper, L2 with a 1.2 s
                  mid-phrase dropout, L3 first (will LOSE to t02's later repeat)
  t02_ar_hlg.mov  HLG BT.2020 10-bit: L3 keeper (last repeat wins across takes), L4
  t03_en.mov      SDR, stored landscape + display rotation: L1, L2 with a stutter
                  restart, L3, L4

Usage: uv run python scripts/make_test_takes.py OUT_DIR
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
PORTRAIT = ROOT / "tests" / "data" / "portrait_pd.jpg"
SR = 48000
FPS = 60
W, H = 2160, 3840
SPEECH_DB = -42.0

SCRIPT = [
    {"ar": "مرحبا يا أصدقاء، أنا جويد من العراق.", "en": "Hi friends, I am Jweed from Iraq."},
    {"ar": "خبر عاجل من عالم الذكاء الاصطناعي. [عاجل]", "en": "Breaking news from the world of AI. [BREAKING]"},
    {"ar": "أطلقت أنثروبيك نموذجا جديدا اليوم.", "en": "Anthropic released a new model today."},
    {"ar": "تابعني لتعرف المزيد.", "en": "Follow me to learn more."},
]


def sh(*args: str) -> None:
    subprocess.run(list(args), check=True, capture_output=True)


def say(text: str, voice: str, tmp: Path) -> np.ndarray:
    """Synthesize speech with macOS `say`, return mono float32 @ 48 kHz scaled to SPEECH_DB."""
    aiff = tmp / f"say_{abs(hash((text, voice)))}.aiff"
    sh("say", "-v", voice, "-o", str(aiff), text.split("[")[0].strip())
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(aiff), "-f", "f32le", "-ac", "1",
                          "-ar", str(SR), "-"], check=True, capture_output=True).stdout
    x = np.frombuffer(raw, dtype="<f4").copy()
    x[np.abs(x) < 1e-4] = 0.0                     # gate: the mic outputs true zeros
    nz = x[np.abs(x) > 0]
    rms = float(np.sqrt(np.mean(nz ** 2))) if nz.size else 1.0
    x *= (10 ** (SPEECH_DB / 20)) / rms
    return np.clip(x, -1, 1).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SR * seconds), dtype=np.float32)


def half(text: str, first: bool) -> str:
    words = text.split("[")[0].split()
    k = max(1, len(words) // 2)
    return " ".join(words[:k] if first else words[k:])


def write_wav(path: Path, x: np.ndarray) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
                    "-c:a", "pcm_s24le", str(path)], input=x.tobytes(), check=True, capture_output=True)


def video(audio: Path, out: Path, *, kind: str, tmp: Path) -> None:
    """Mirrored talking-head video over the audio. kind: sdr_full | hlg | sdr_rotated."""
    base = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            # red marker on the TRUE left, then mirror like his camera does
            f"drawbox=x=60:y=1500:w=220:h=220:color=red@1:t=fill,hflip,"
            f"noise=alls=3:allf=t")
    common = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(PORTRAIT),
              "-i", str(audio), "-shortest", "-r", str(FPS)]
    aac = ["-c:a", "aac", "-b:a", "256k", "-ar", str(SR), "-ac", "2"]
    if kind == "sdr_full":
        sh(*common, "-vf", base + ",scale=out_range=full,format=yuv420p",
           "-c:v", "hevc_videotoolbox", "-b:v", "40M", "-tag:v", "hvc1",
           "-color_range", "pc", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
           *aac, str(out))
    elif kind == "hlg":
        sh(*common, "-vf", base + ",format=gbrpf32le,"
           "zscale=tin=bt709:min=bt709:pin=bt709:rin=tv:t=arib-std-b67:m=bt2020nc:p=bt2020:r=tv:npl=203,"
           "format=yuv420p10le",
           "-c:v", "hevc_videotoolbox", "-profile:v", "main10", "-b:v", "40M", "-tag:v", "hvc1",
           "-color_range", "tv", "-color_primaries", "bt2020", "-color_trc", "arib-std-b67",
           "-colorspace", "bt2020nc", *aac, str(out))
    elif kind == "sdr_rotated":
        land = tmp / "landscape.mov"
        # store landscape (like an iPhone portrait clip) and tag the display matrix
        sh(*common, "-vf", base + ",transpose=2,format=yuv420p",
           "-c:v", "hevc_videotoolbox", "-b:v", "40M", "-tag:v", "hvc1",
           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
           *aac, str(land))
        sh("ffmpeg", "-v", "error", "-y", "-display_rotation:v:0", "90", "-i", str(land), "-c", "copy",
           str(out))
    else:
        raise ValueError(kind)


def build(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ar = [say(l["ar"], "Majed", tmp) for l in SCRIPT]
        en = [say(l["en"], "Samantha", tmp) for l in SCRIPT]
        l1_partial = say(half(SCRIPT[0]["ar"], True), "Majed", tmp)
        l2_a = say(half(SCRIPT[1]["ar"], True), "Majed", tmp)
        l2_b = say(half(SCRIPT[1]["ar"], False), "Majed", tmp)
        stutter = say(" ".join(SCRIPT[1]["en"].split()[:2]), "Samantha", tmp)

        t01 = [silence(1.0), l1_partial, silence(1.0), ar[0], silence(0.9),
               l2_a, silence(1.2), l2_b,                       # 1.2 s mid-phrase dropout
               silence(1.1), ar[2], silence(1.0)]
        t02 = [silence(0.8), ar[2], silence(1.0), ar[3], silence(1.0)]
        t03 = [silence(0.8), en[0], silence(0.9), stutter, silence(0.03), en[1],   # restart, no pause
               silence(1.0), en[2], silence(0.9), en[3], silence(1.0)]

        takes = {"t01_ar.mov": (t01, "sdr_full"), "t02_ar_hlg.mov": (t02, "hlg"),
                 "t03_en.mov": (t03, "sdr_rotated")}
        for name, (parts, kind) in takes.items():
            wav = tmp / (name + ".wav")
            write_wav(wav, np.concatenate(parts))
            video(wav, out_dir / name, kind=kind, tmp=tmp)

        # expected: where the mid-phrase gap is in t01 (source seconds)
        gap_start = sum(len(p) for p in t01[:6]) / SR
    (out_dir / "script_ar.txt").write_text("\n".join(l["ar"] for l in SCRIPT) + "\n", encoding="utf-8")
    (out_dir / "script_en.txt").write_text("\n".join(l["en"] for l in SCRIPT) + "\n", encoding="utf-8")
    expected = {
        "ar": {"L01": "t01", "L02": "t01", "L03": "t02", "L04": "t02"},
        "en": {"L01": "t03", "L02": "t03", "L03": "t03", "L04": "t03"},
        "t01_mid_phrase_gap": [round(gap_start, 3), round(gap_start + 1.2, 3)],
        "marker_true_side": "left",
    }
    (out_dir / "expected.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    return expected


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "media"
    print(json.dumps(build(target), indent=2))
