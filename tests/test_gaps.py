"""Gap audit: only long INTERNAL gaps; closure keeps 0.25 s pads; A/B keeps the pause on any word diff."""
import numpy as np

from reelstudio.edit.gaps import ab_verify, audit_gaps, split_segment
from reelstudio.media.audioio import write_wav
from reelstudio.models import GapIssue, Span, SpeechMap, Word

SM = SpeechMap(take_id="t01", duration=12, frame_s=0.01, floor_db=-52, runs=[
    Span(start=1.0, end=2.0), Span(start=2.3, end=3.0),      # 0.3 s: normal pause
    Span(start=4.2, end=5.0),                                # 1.2 s: mid-phrase dropout
    Span(start=6.0, end=7.0),                                # 1.0 s after a sentence end
])
WORDS = [Word(text="engineers,", start=2.3, end=4.1), Word(text="and", start=4.2, end=4.4),
         Word(text="done.", start=4.6, end=5.0), Word(text="Next", start=6.0, end=6.3)]


def test_audit_classifies_and_ignores_short_gaps():
    gaps = audit_gaps("t01", (0.75, 7.25), SM, WORDS)
    assert [(g.start, g.end, g.kind) for g in gaps] == [(3.0, 4.2, "mid_phrase"), (5.0, 6.0, "sentence")]


def test_split_keeps_pads():
    gaps = [GapIssue(take_id="t01", start=3.0, end=4.2), GapIssue(take_id="t01", start=5.0, end=6.0, action="keep")]
    assert split_segment((0.75, 7.25), gaps, 0.25) == [(0.75, 3.25), (3.95, 7.25)]


class FakeASR:
    """Returns the words it is told, keyed by audio length, to simulate a cut eating a word."""
    def __init__(self, uncut_words, cut_words):
        self.calls = 0
        self.outputs = [uncut_words, cut_words]

    def transcribe(self, audio, *, language=None, prompt=None):
        words = self.outputs[self.calls]
        self.calls += 1
        # cut sits 2.3 s into the window (gap 3.0 + pad 0.25 - window start 0.95)
        times = [(1.5, 2.0), (2.0, 2.28), (2.6, 2.8), (2.9, 3.2)]
        return [Word(text=w, start=a, end=b) for w, (a, b) in zip(words, times)], "en", 1.0


def test_ab_verify_detects_a_lost_word(tmp_path):
    wav = write_wav(tmp_path / "a.wav", np.zeros(48000 * 12, dtype=np.float32))
    gap = GapIssue(take_id="t01", start=3.0, end=4.2)
    ok, why = ab_verify(gap, wav, SM, language="en", transcriber=FakeASR(
        ["missile", "engineers", "and", "using"], ["missile", "engines", "and", "using"]))
    assert not ok and "engines" in why
    ok, _ = ab_verify(gap, wav, SM, language="en",
                      transcriber=FakeASR(["a", "team", "and"], ["A", "team,", "and"]))
    assert ok  # punctuation/case are not word differences
    ok, why = ab_verify(gap, wav, SM, language="en", transcriber=FakeASR(
        ["a", "team", "and", "artificial"], ["a", "team", "and", "superficial"]))
    assert ok and "noise" in why  # a difference two words away from the cut is re-decoding noise
