"""Central configuration: paths, delivery spec, analysis + QA thresholds.

Every number here encodes a rule the creator (Jweed) has enforced after a defect
shipped. Change thresholds HERE, never inline in modules. The reasoning for each
rule lives in .claude/skills/reel-standard/SKILL.md.
"""
from __future__ import annotations

import glob
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
APP_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = Path(os.environ.get("REEL_PROJECTS_DIR", APP_ROOT / "projects"))
MODELS_DIR = Path(os.environ.get("REEL_MODELS_DIR", APP_ROOT / "models"))
VENDOR_DIR = APP_ROOT / "vendor"
ASSETS_DIR = APP_ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
RENDER_DIR = APP_ROOT / "render"  # Node project that pins HyperFrames
WEB_DIR = APP_ROOT / "web"
YUNET_MODEL = MODELS_DIR / "face_detection_yunet_2023mar.onnx"


def load_env() -> None:
    """Load APP_ROOT/.env into os.environ (existing env vars win)."""
    env_file = APP_ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# Must run before the dataclasses below read os.environ for their defaults.
load_env()


# ── Delivery: 2160x3840, 60 fps, HEVC hvc1, .mov, Rec.709 SDR, tv range ─────
# Downscaling to 1080p/30 is why a reel was rejected ("the quality was bad").
@dataclass(frozen=True)
class DeliverySpec:
    width: int = 2160
    height: int = 3840
    fps: int = 60
    vcodec: str = "hevc"
    vtag: str = "hvc1"
    container: str = "mov"
    pix_fmt: str = "yuv420p"
    color_primaries: str = "bt709"
    color_trc: str = "bt709"
    color_space: str = "bt709"
    color_range: str = "tv"
    video_bitrate: str = "60M"
    video_maxrate: str = "90M"
    audio_codec: str = "aac"
    audio_rate: int = 48000
    audio_channels: int = 2
    audio_bitrate: str = "320k"
    # Loudness for Instagram Reels: normalise to -14 LUFS with a -1.5 dBTP ceiling so the
    # delivered AAC still measures <= -1.0 dBTP after encoding (QA checks the file).
    loudness_i: float = -14.0
    loudness_tp: float = -1.5
    loudness_lra: float = 11.0
    gop: int = 120                   # keyframe every 2 s at 60 fps


# ── Speech map: his mic hard-gates pauses to digital zero ───────────────────
# -38 dB is NOT silence on his mic (speech runs -39..-46 dB); -38 deleted words.
@dataclass(frozen=True)
class SpeechSpec:
    frame_s: float = 0.010          # RMS analysis frame
    floor_db: float = -52.0         # speech present when frame RMS > floor
    merge_run_gap_s: float = 0.10   # raw runs closer than this are one run
    min_run_s: float = 0.05         # drop clicks shorter than this
    block_gap_s: float = 0.70       # silence >= this starts a new block
    min_block_speech_s: float = 0.30  # blocks with less speech are blips
    pad_s: float = 0.25             # pad each side of every cut (0.15 clipped words)
    long_gap_s: float = 0.60        # internal gap longer than this is audited
    # After closing, a gap keeps pad_s on each side => 2 * pad_s of pause.


# ── Transcription ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ASRSpec:
    engine: str = os.environ.get("REEL_ASR_ENGINE", "mlx")  # mlx | faster | elevenlabs
    model: str = os.environ.get("REEL_ASR_MODEL", "large-v3")
    word_timestamps: bool = True
    vad_filter: bool = False
    condition_on_previous_text: bool = False
    block_pad_s: float = 0.15       # audio context around an isolated block
    # Whisper mishears the creator's name; corrected everywhere on screen.
    vocabulary: tuple[str, ...] = ("Jweed", "Claude", "Anthropic", "ChatGPT", "OpenAI")
    name_fixes: tuple[tuple[str, str], ...] = (
        (r"\b(Juaid|Juwaid|Jouaid|Jwaid|Jawid|Jweid|Juweid|Jwid)\b", "Jweed"),
    )


# ── Layout: never cover his face or head ────────────────────────────────────
@dataclass(frozen=True)
class LayoutSpec:
    face_sample_every_s: float = 2.0
    head_clearance_top: float = 0.45    # x face height above the box (hair)
    head_clearance_bottom: float = 0.25  # x face height below the box (chin)
    head_clearance_side: float = 0.30    # x face width each side
    shadow_budget_px: int = 120          # box-shadow alpha reaches ~60 px @1080
    # Instagram UI overlays (fractions of canvas) — keep text out of these.
    ig_top: float = 0.08
    ig_bottom: float = 0.20
    ig_right: float = 0.12
    caption_font_px: int = 150          # at 2160 px wide
    caption_max_chars: int = 26
    caption_max_words: int = 4


# ── QA: every check here replaced a step that shipped a broken reel ────────
@dataclass(frozen=True)
class QASpec:
    stutter_window_s: float = 3.5
    stutter_step_s: float = 1.75
    av_tolerance_frames: int = 1
    face_alpha_threshold: int = 8        # 0-255; any overlay pixel above counts
    face_occlusion_max: float = 0.0      # fraction of head-band pixels allowed
    final_gap_max_s: float = 0.60        # mid-phrase gaps in the delivered audio
    loudness_tol_lu: float = 1.0          # integrated within -14 +/- 1 LU
    true_peak_max_db: float = -1.0        # delivered file true peak ceiling


@dataclass(frozen=True)
class PronounceSpec:
    """Generated-voice pronunciation QA: every line is heard back by whisper; any script word missing,
    misheard or repeated -> the line is regenerated (new seed + phonetic respelling), best take kept."""
    max_tries: int = 4
    fuzz_min: int = 72                   # rapidfuzz ratio for "heard the same word"
    extra_run_max: int = 1               # >1 consecutive unexpected heard words = garbled/extra speech
    # spoken respellings (captions keep the real spelling). Lowercase keys, matched as whole words.
    respell: tuple[tuple[str, str], ...] = (
        ("ai", "A.I."), ("faa", "F.A.A."), ("tsa", "T.S.A."), ("u.s.", "U.S."), ("o'hare", "Oh-Hair"),
    )
    # brand names whisper may write in either alphabet (Arabic spelling -> Latin key; prefixes و/ب/ال handled)
    aliases: tuple[tuple[str, str], ...] = (
        ("اوتلوك", "outlook"), ("أوتلوك", "outlook"), ("مايكروسوفت", "microsoft"), ("ميكروسوفت", "microsoft"),
        ("تيمز", "teams"), ("كوبايلوت", "copilot"), ("كوبيلوت", "copilot"), ("ايميل", "email"), ("إيميل", "email"),
        ("هكرز", "hackers"), ("هاكرز", "hackers"), ("هكر", "hacker"), ("هاكر", "hacker"), ("داتا", "data"), ("سيرفرات", "servers"),
    )
    # NOTE: Chatterbox says "U.S." as "U.S.S."/"U.S. I" even respelled — write "the United States" instead.
    # second-try respellings for words heard wrong after the first respelling
    respell_alt: tuple[tuple[str, str], ...] = (
        ("ai", "A. I."), ("faa", "F. A. A."), ("u.s.", "U. S."), ("o'hare", "oh hair"),
    )


@dataclass(frozen=True)
class RecordSpec:
    """Creator-recorded narration (laptop mic, one take per script line, last clean take wins)."""
    pad_s: float = 0.25                  # kept each side of the speech (same as every cut)
    floor_db: float = -52.0              # speech floor (house rule) ...
    floor_margin_db: float = 10.0        # ... raised to noise floor + margin on a noisy room
    close_gap_s: float = 0.80            # pauses longer than this inside a line are tightened ...
    keep_gap_s: float = 0.45             # ... to this, only if the A/B transcript is unchanged
    highpass_hz: int = 80
    denoise_floor_db: float = -20.0      # spectral gate: noise is pulled down at most this much (no "underwater")
    denoise_over: float = 1.6            # gate opens at noise profile x this
    speech_rms_db: float = -20.0         # every take levelled to this speech RMS (lines match each other)
    peak_db: float = -1.5
    clip_db: float = -0.3                # raw sample peak above this = clipping -> retake
    quiet_db: float = -45.0              # raw speech RMS below this = too far from the mic
    min_snr_db: float = 15.0             # speech vs room noise
    pace_wps: tuple[tuple[str, float], ...] = (("en", 2.6), ("ar", 2.1))   # teleprompter pace w/o a guide


@dataclass(frozen=True)
class LLMSpec:
    model: str = os.environ.get("REEL_CLAUDE_MODEL", "claude-opus-5-5")
    effort: str = os.environ.get("REEL_CLAUDE_EFFORT", "medium")
    timeout_s: int = 600


def find_claude_bin() -> str | None:
    """Locate the Claude Code CLI: $CLAUDE_BIN, PATH, then the newest VS Code bundle."""
    explicit = os.environ.get("CLAUDE_BIN")
    if explicit and Path(explicit).exists():
        return explicit
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    pattern = str(Path.home() / ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude")
    candidates = sorted(glob.glob(pattern), key=_version_key)
    return candidates[-1] if candidates else None


def _version_key(path: str) -> tuple[int, ...]:
    # .../anthropic.claude-code-2.1.323-darwin-arm64/... -> (2, 1, 323)
    part = path.split("anthropic.claude-code-")[-1].split("-")[0]
    return tuple(int(p) for p in part.split(".") if p.isdigit())


@dataclass
class Settings:
    delivery: DeliverySpec = field(default_factory=DeliverySpec)
    speech: SpeechSpec = field(default_factory=SpeechSpec)
    asr: ASRSpec = field(default_factory=ASRSpec)
    layout: LayoutSpec = field(default_factory=LayoutSpec)
    qa: QASpec = field(default_factory=QASpec)
    pronounce: PronounceSpec = field(default_factory=PronounceSpec)
    record: RecordSpec = field(default_factory=RecordSpec)
    llm: LLMSpec = field(default_factory=LLMSpec)
    ffmpeg: str = field(default_factory=lambda: shutil.which("ffmpeg") or "ffmpeg")
    ffprobe: str = field(default_factory=lambda: shutil.which("ffprobe") or "ffprobe")
    host: str = os.environ.get("REEL_HOST", "127.0.0.1")
    port: int = int(os.environ.get("REEL_PORT", "8765"))
    chatterbox_url: str = os.environ.get("CHATTERBOX_URL", "http://127.0.0.1:8004")

    @property
    def claude_bin(self) -> str | None:
        return find_claude_bin()

    @property
    def elevenlabs_key(self) -> str | None:
        return os.environ.get("ELEVENLABS_API_KEY") or None

    @property
    def anthropic_key_set(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))


SETTINGS = Settings()
