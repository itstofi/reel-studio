"""Captions: always English, word-synced, chunked, never overlapping, inside the cut."""
from reelstudio.edit.captions import build_cues, chunk, distribute, write_ass
from reelstudio.edit.edl import build_timeline
from reelstudio.config import LayoutSpec
from reelstudio.models import (Beat, BlockTranscript, CaptionWord, Layout, Plan, Segment, Span,
                               SpeechMap, Word)


def test_distribute_skips_pauses():
    words = distribute(["one", "two", "three", "four"], [(0.0, 1.0), (2.0, 3.0)])
    assert words[0].start == 0.0 and words[-1].end <= 3.0
    for w in words:  # nothing is timed inside the pause
        assert not (1.0 < w.start < 2.0)


def test_chunk_breaks_on_punctuation_count_and_pause():
    spec = LayoutSpec()
    ws = [CaptionWord(text=t, start=i * 0.3, end=i * 0.3 + 0.25) for i, t in
          enumerate("Hi friends, I am Jweed from Iraq and this is huge".split())]
    ws[-1] = CaptionWord(text="huge", start=10.0, end=10.3)   # long pause before the last word
    cues = chunk(ws, "B01", spec)
    texts = [c.text for c in cues]
    assert texts[0] == "Hi friends,"
    assert all(len(c.words) <= spec.caption_max_words for c in cues)
    assert texts[-1] == "huge"


def _project_bits():
    plan = Plan(beats=[
        Beat(id="B01", caption_en="Breaking news from the world of AI.",
             segments=[Segment(id="B01s1", take_id="t01", src_in=0.75, src_out=3.25)]),
        Beat(id="B02", caption_en="Google released a new tool today.",
             segments=[Segment(id="B02s1", take_id="t01", src_in=5.75, src_out=8.25)]),
    ])
    tl = build_timeline(plan, 60)
    sm = SpeechMap(take_id="t01", duration=20, frame_s=0.01, floor_db=-52,
                   runs=[Span(start=1.0, end=3.0), Span(start=6.0, end=8.0)])
    return plan, tl, {"t01": sm}


def test_arabic_cut_cues_are_english_inside_beats_and_non_overlapping():
    plan, tl, sms = _project_bits()
    cues = build_cues(plan, tl, [], sms, lang="ar")
    assert cues and all(c.end <= tl.duration + 1e-6 for c in cues)
    assert all(a.end <= b.start + 1e-6 for a, b in zip(cues, cues[1:]))
    b2 = [c for c in cues if c.beat_id == "B02"]
    assert b2[0].start >= 2.5  # beat 2 starts at 2.5 s in the output (after beat 1)
    assert " ".join(c.text for c in cues).startswith("Breaking news")


def test_english_cut_uses_transcript_times():
    plan, tl, sms = _project_bits()
    words = [Word(text=w, start=1.0 + i * 0.28, end=1.0 + i * 0.28 + 0.22) for i, w in
             enumerate("breaking news from the world of ai".split())]
    trs = [BlockTranscript(block_id="t01_b001", take_id="t01", words=words)]
    cues = build_cues(plan, tl, trs, sms, lang="en")
    first = cues[0].words[0]
    assert first.text == "Breaking" and abs(first.start - 0.25) < 0.01  # src 1.0 -> out 0.25


def test_write_ass_has_one_event_per_word_and_highlight(tmp_path):
    plan, tl, sms = _project_bits()
    cues = build_cues(plan, tl, [], sms, lang="ar")
    layout = Layout(canvas_w=2160, canvas_h=3840, head=None, caption_band=Span(start=2700, end=3000),
                    graphics_bands=[], safe_x0=130, safe_x1=1900)
    out = write_ass(cues, layout, tmp_path / "c.ass")
    text = out.read_text()
    assert "PlayResX: 2160" in text and "PlayResY: 3840" in text
    events = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    assert len(events) == sum(len(c.words) for c in cues)
    assert "\\c&H0000D4FF&" in text and "\\pos(1015,2850)" in text
