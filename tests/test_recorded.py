"""Creator-recorded narration: cleanup, trim, pause tightening, take choice, reading direction."""
import numpy as np

from reelstudio.config import SETTINGS
from reelstudio.media.audioio import SR
from reelstudio.voice.recorded import analyse_raw, chosen, clean, direction, speech_spans

RS = SETTINGS.record


def _tone(s, amp=0.2, f=220.0):
    t = np.arange(int(s * SR)) / SR
    # speech-like: voiced tone with a 4 Hz syllable envelope
    return (amp * np.sin(2 * np.pi * f * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t) ** 2)).astype(np.float32)


def _sil(s):
    return np.zeros(int(s * SR), np.float32)


def _take(pause=1.6, noise=0.003, click=True):
    rng = np.random.default_rng(0)
    k = _sil(0.05)
    if click:
        k[100:140] = 0.6        # Space-bar click
    x = np.concatenate([_sil(0.3), k, _sil(1.5), _tone(1.2), _sil(pause), _tone(1.0), _sil(1.0), k, _sil(0.3)])
    return x + rng.normal(0, noise, len(x)).astype(np.float32)


def test_clicks_are_not_speech():
    runs, _ = speech_spans(_take(), SR, RS)
    assert runs[0].start > 1.7 and runs[-1].end < 5.8     # first/last run is the voice, not the key clicks


def test_trim_pads_and_tightens_long_pause():
    trimmed, tight, closed = clean(_take(), SR, RS)
    speech = 1.2 + 1.6 + 1.0
    assert abs(len(trimmed) / SR - (speech + 2 * RS.pad_s)) < 0.15
    assert len(closed) == 1 and closed[0]["to_s"] == RS.keep_gap_s
    assert abs(len(tight) / SR - (1.2 + RS.keep_gap_s + 1.0 + 2 * RS.pad_s)) < 0.15


def test_short_pause_kept():
    _, tight, closed = clean(_take(pause=0.5), SR, RS)
    assert not closed


def test_denoise_lowers_room_noise():
    x = _take(noise=0.006)
    _, tight, _ = clean(x, SR, RS)
    before, after = analyse_raw(x, SR, RS), analyse_raw(tight, SR, RS)
    assert after["snr_db"] > before["snr_db"] + 6


def test_no_speech():
    t, tt, c = clean(_sil(3.0) + np.random.default_rng(1).normal(0, 0.002, int(3 * SR)).astype(np.float32), SR, RS)
    assert len(t) == 0 and len(tt) == 0


def test_last_clean_take_wins_and_pin_overrides():
    e = {"takes": [{"n": 1, "ok": True}, {"n": 2, "ok": True}, {"n": 3, "ok": False}], "pinned": None}
    assert chosen(e)["n"] == 2
    assert chosen({**e, "pinned": 1})["n"] == 1
    assert chosen({"takes": [{"n": 1, "ok": False}]}) is None


def test_direction_marks_numbers_brands_and_breaths():
    spec = {"lang": "en", "lines": [{"text": "Microsoft Outlook went down, in 70 countries."},
                                    {"text": "Why did it happen?"}, {"text": "Follow for more."}]}
    d = direction(spec)
    assert d[0]["note"].startswith("Hook")
    assert [d[0]["words"][k] for k in d[0]["stress"]] == ["Outlook", "70"]   # sentence-initial "Microsoft" is not marked
    assert d[0]["pauses"] == [3]
    assert d[1]["note"].startswith("Question") and d[2]["note"].startswith("Close")
    ar = direction({"lang": "ar", "lines": [{"text": "توقف بريد أوتلوك من مايكروسوفت."}]})
    assert [ar[0]["words"][k] for k in ar[0]["stress"]] == ["أوتلوك", "مايكروسوفت."]
