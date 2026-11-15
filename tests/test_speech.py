"""Speech map: -52 dB floor, digital-zero pauses, block grouping, gap finding."""
import numpy as np

from reelstudio.config import SpeechSpec
from reelstudio.media.speech import (blocks_from_map, frame_levels, gaps_between,
                                     speech_map_array)
from reelstudio.models import Span, SpeechMap

SR = 48000


def tone(seconds: float, db: float, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    amp = 10 ** (db / 20) * np.sqrt(2)  # RMS of a sine = amp / sqrt(2)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def zeros(seconds: float) -> np.ndarray:
    return np.zeros(int(SR * seconds), dtype=np.float32)


def test_quiet_speech_at_minus_46_db_is_still_speech():
    # His real speech runs -39..-46 dB: a -38 dB floor would delete it.
    audio = np.concatenate([zeros(0.5), tone(1.0, -46.0), zeros(0.5)])
    runs = speech_map_array(audio, SR, SpeechSpec())
    assert len(runs) == 1
    assert abs(runs[0].start - 0.5) < 0.02 and abs(runs[0].end - 1.5) < 0.02


def test_below_floor_is_silence():
    audio = np.concatenate([zeros(0.5), tone(1.0, -60.0), zeros(0.5)])
    assert speech_map_array(audio, SR, SpeechSpec()) == []


def test_frame_levels_digital_zero():
    db, peak = frame_levels(zeros(0.1), SR, 0.01)
    assert len(db) == 10 and (peak == 0).all() and (db < -200).all()


def test_short_gaps_merge_long_gaps_split_blocks():
    spec = SpeechSpec()
    audio = np.concatenate([
        tone(0.6, -42), zeros(0.05), tone(0.6, -42),   # 50 ms gap -> one run
        zeros(0.4), tone(0.5, -42),                    # 0.4 s gap -> same block
        zeros(1.2), tone(0.8, -42),                    # 1.2 s gap -> new block
    ])
    runs = speech_map_array(audio, SR, spec)
    assert len(runs) == 3
    sm = SpeechMap(take_id="t01", duration=len(audio) / SR, frame_s=spec.frame_s,
                   floor_db=spec.floor_db, runs=runs)
    blocks = blocks_from_map(sm, spec)
    assert [b.id for b in blocks] == ["t01_b001", "t01_b002"]
    assert len(blocks[0].runs) == 2 and len(blocks[1].runs) == 1


def test_blips_are_not_blocks():
    spec = SpeechSpec()
    audio = np.concatenate([zeros(1.0), tone(0.1, -40), zeros(1.0), tone(1.0, -40)])
    runs = speech_map_array(audio, SR, spec)
    sm = SpeechMap(take_id="t02", duration=3.1, frame_s=0.01, floor_db=-52, runs=runs)
    blocks = blocks_from_map(sm, spec)
    assert len(blocks) == 1 and blocks[0].start > 2.0


def test_gaps_between_runs_inside_window():
    runs = [Span(start=0, end=1), Span(start=2.2, end=3), Span(start=3.1, end=4), Span(start=9, end=10)]
    gaps = gaps_between(runs, 0.0, 4.5)
    assert [(g.start, g.end) for g in gaps] == [(1, 2.2), (3, 3.1)]
