"""Shared data models (pydantic v2). Every module and the web API speak these.

Time conventions:
- ``src`` times are seconds in a take's own (source) clock.
- ``out`` times are seconds in the delivered reel's clock.
- EDL/timeline boundaries are integer frames at DeliverySpec.fps (60) so
  durations never drift; seconds are derived as frames / fps.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


Lang = Literal["ar", "en"]


# ── Media ────────────────────────────────────────────────────────────────────
class HDRKind(str, Enum):
    sdr = "sdr"
    hlg = "hlg"            # arib-std-b67 (iPhone HLG / Dolby Vision 8.4 base layer)
    pq = "pq"              # smpte2084 (HDR10)
    dolby_vision = "dolby_vision"  # DV with a non-HLG base layer


class MediaInfo(BaseModel):
    path: str
    duration: float
    width: int                      # display width (after rotation)
    height: int                     # display height (after rotation)
    rotation: int = 0               # degrees from the display matrix
    fps: float
    fps_rational: str = "60/1"
    vcodec: str = ""
    pix_fmt: str = ""
    bit_depth: int = 8
    color_primaries: str | None = None
    color_trc: str | None = None
    color_space: str | None = None
    color_range: str | None = None  # "tv" | "pc" | None
    hdr: HDRKind = HDRKind.sdr
    has_audio: bool = True
    audio_codec: str | None = None
    audio_rate: int | None = None
    audio_channels: int | None = None
    size_bytes: int = 0


class Take(BaseModel):
    id: str                          # "t01", "t02", ... in recording order
    filename: str
    source_path: str                 # absolute path to the original (never modified)
    order: int
    lang: Lang | None = None         # None = auto-detect
    mirror: bool = True              # HARD RULE: hflip every take of his footage
    info: MediaInfo | None = None
    audio_path: str | None = None    # 48 kHz mono float WAV for analysis
    proxy_path: str | None = None    # 1080x1920 H.264, fully conformed, for UI + face


# ── Speech map ───────────────────────────────────────────────────────────────
class Span(BaseModel):
    start: float
    end: float

    @property
    def dur(self) -> float:
        return self.end - self.start


class SpeechMap(BaseModel):
    take_id: str
    duration: float
    frame_s: float
    floor_db: float
    runs: list[Span]                 # speech present (source time)
    noise_floor_db: float | None = None
    digital_zero_ratio: float = 0.0  # fraction of frames with peak == 0.0


class Block(BaseModel):
    id: str                          # "t01_b003"
    take_id: str
    start: float                     # speech start (no pad)
    end: float                       # speech end (no pad)
    runs: list[Span]                 # speech runs inside the block


class Word(BaseModel):
    text: str
    start: float                     # source seconds (absolute in the take)
    end: float
    prob: float | None = None


class BlockTranscript(BaseModel):
    block_id: str
    take_id: str
    language: str | None = None
    language_prob: float | None = None
    text: str = ""
    words: list[Word] = Field(default_factory=list)
    engine: str = ""
    model: str = ""
    forced_language: bool = False


# ── Script ───────────────────────────────────────────────────────────────────
class ScriptLine(BaseModel):
    id: str                          # "L01"
    ar: str | None = None
    en: str | None = None
    onscreen: str | None = None      # text from [brackets] in the script


class Script(BaseModel):
    lines: list[ScriptLine] = Field(default_factory=list)


# ── Alignment (last repeat wins) ─────────────────────────────────────────────
class Attempt(BaseModel):
    line_ids: list[str]
    take_id: str
    start: float                     # first matched word start (source)
    end: float                       # last matched word end (source)
    # Robust probes for snapping to the speech map: whisper stretches a word's END
    # across dropouts, so use the middle of the first/last word, not its edges.
    first_mid: float | None = None
    last_mid: float | None = None
    block_ids: list[str]
    coverage: float                  # fraction of the line's words matched
    inserted: int = 0                # extra spoken words inside the attempt (stutters)
    score: float                     # alignment quality 0..1
    clean: bool = False
    text: str


class LineAlignment(BaseModel):
    line_id: str
    attempts: list[Attempt] = Field(default_factory=list)
    chosen: int | None = None        # index into attempts (default: last clean one)
    status: Literal["ok", "weak", "missing"] = "missing"


class Alignment(BaseModel):
    lang: Lang
    lines: list[LineAlignment]
    unmatched_block_ids: list[str] = Field(default_factory=list)


# ── Gaps (mid-phrase dropouts) ───────────────────────────────────────────────
class GapIssue(BaseModel):
    take_id: str
    start: float                     # silence start (source)
    end: float                       # silence end (source)
    kind: Literal["mid_phrase", "sentence", "line"] = "mid_phrase"
    action: Literal["close", "keep"] = "close"
    verified: bool | None = None     # A/B transcript diff result (None = not run)
    reason: str = ""

    @property
    def dur(self) -> float:
        return self.end - self.start


# ── Plan (shown to the creator BEFORE rendering) ─────────────────────────────
class Segment(BaseModel):
    id: str
    take_id: str
    src_in: float                    # seconds, padded, frame-snapped
    src_out: float
    line_ids: list[str] = Field(default_factory=list)


class GraphicSpec(BaseModel):
    id: str
    template: Literal["title", "list", "stat", "screenshot", "nametag", "logo", "quote"]
    params: dict[str, Any] = Field(default_factory=dict)
    beat_offset: float = 0.0         # seconds after the beat starts
    duration: float = 3.0
    band: Literal["auto", "upper", "lower"] = "auto"


class Beat(BaseModel):
    id: str                          # "B01"
    line_ids: list[str] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    caption_en: str = ""             # English caption text (ALWAYS English)
    graphics: list[GraphicSpec] = Field(default_factory=list)
    gaps: list[GapIssue] = Field(default_factory=list)
    note: str = ""
    enabled: bool = True


class Plan(BaseModel):
    beats: list[Beat] = Field(default_factory=list)
    created_by: Literal["aligner", "claude", "manual"] = "aligner"
    created_at: str = Field(default_factory=now_iso)
    approved: bool = False
    approved_at: str | None = None
    warnings: list[str] = Field(default_factory=list)


# ── Timeline (source -> output mapping), frame-exact ─────────────────────────
class TimelineEntry(BaseModel):
    segment_id: str
    beat_id: str
    take_id: str
    src_in_f: int                    # source frame index (at delivery fps)
    src_out_f: int                   # exclusive
    out_in_f: int
    out_out_f: int                   # exclusive


class Timeline(BaseModel):
    fps: int = 60
    entries: list[TimelineEntry] = Field(default_factory=list)

    @property
    def frames(self) -> int:
        return self.entries[-1].out_out_f if self.entries else 0

    @property
    def duration(self) -> float:
        return self.frames / self.fps


# ── Captions ─────────────────────────────────────────────────────────────────
class CaptionWord(BaseModel):
    text: str
    start: float                     # output seconds
    end: float


class CaptionCue(BaseModel):
    id: str
    beat_id: str
    start: float                     # output seconds
    end: float
    words: list[CaptionWord]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


# ── Face + layout ────────────────────────────────────────────────────────────
class Box(BaseModel):
    x0: int
    y0: int
    x1: int
    y1: int


class FaceInfo(BaseModel):
    take_id: str
    canvas_w: int                    # delivery canvas coords (2160x3840)
    canvas_h: int
    samples: int
    detections: int
    face: Box | None = None          # union of detected face boxes (after mirror)
    head: Box | None = None          # face + clearance (hair, chin, sides, shadow)


class Layout(BaseModel):
    canvas_w: int
    canvas_h: int
    head: Box | None                 # off-limits for ANY graphic pixel (incl. shadows)
    caption_band: Span               # y range (pixels) for captions
    graphics_bands: list[Span]       # y ranges (pixels) free for graphics
    safe_x0: int
    safe_x1: int


# ── QA ───────────────────────────────────────────────────────────────────────
class CheckResult(BaseModel):
    name: str
    status: Literal["pass", "warn", "fail", "skip"]
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class QAReport(BaseModel):
    file: str
    created_at: str = Field(default_factory=now_iso)
    checks: list[CheckResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.status != "fail" for c in self.checks)


# ── Voice ────────────────────────────────────────────────────────────────────
class VoiceClip(BaseModel):
    id: str
    text: str
    lang: Lang
    engine: str                      # elevenlabs | chatterbox | say
    voice: str                       # voice id / reference name
    path: str                        # WAV
    duration: float = 0.0
    created_at: str = Field(default_factory=now_iso)


# ── Project ──────────────────────────────────────────────────────────────────
StageName = Literal["ingest", "analyze", "plan", "render", "qa"]
STAGES: tuple[StageName, ...] = ("ingest", "analyze", "plan", "render", "qa")


class StageStatus(BaseModel):
    state: Literal["pending", "running", "done", "failed", "stale"] = "pending"
    message: str = ""
    progress: float = 0.0
    started_at: str | None = None
    finished_at: str | None = None


class ProjectSettings(BaseModel):
    lang: Lang = "ar"                # language of the cut being built
    music_path: str | None = None
    music_gain_db: float = -20.0
    captions: bool = True            # captions are expected; switch exists for tests
    graphics: bool = True
    close_gaps: bool = True


class Project(BaseModel):
    id: str
    name: str
    created_at: str = Field(default_factory=now_iso)
    mode: Literal["talking_head", "narrated"] = "talking_head"
    settings: ProjectSettings = Field(default_factory=ProjectSettings)
    takes: list[Take] = Field(default_factory=list)
    script: Script | None = None
    stages: dict[str, StageStatus] = Field(
        default_factory=lambda: {s: StageStatus() for s in STAGES}
    )
    final_path: str | None = None
    preview_path: str | None = None

    def take(self, take_id: str) -> Take:
        for t in self.takes:
            if t.id == take_id:
                return t
        raise KeyError(take_id)
