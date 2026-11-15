"""Script alignment: LAST clean repeat wins, full coverage, cuts from the speech map."""
from reelstudio.edit.align import align_script, attempt_segment
from reelstudio.models import BlockTranscript, Script, ScriptLine, Span, SpeechMap, Take, Word


def words_at(text: str, start: float, step: float = 0.3) -> list[Word]:
    out, t = [], start
    for w in text.split():
        out.append(Word(text=w, start=round(t, 3), end=round(t + step * 0.8, 3)))
        t += step
    return out


def tr(block_id: str, text: str, start: float, take: str = "t01") -> BlockTranscript:
    ws = words_at(text, start)
    return BlockTranscript(block_id=block_id, take_id=take, text=text, words=ws)


TAKES = [Take(id="t01", filename="a.mov", source_path="/x/a.mov", order=1),
         Take(id="t02", filename="b.mov", source_path="/x/b.mov", order=2)]


def script_en(*lines: str) -> Script:
    return Script(lines=[ScriptLine(id=f"L{i+1:02d}", en=t) for i, t in enumerate(lines)])


def test_last_clean_repeat_wins_and_partial_retry_loses():
    s = script_en("Imagine you needed a team of missile engineers.", "There is no engineer.")
    trs = [
        tr("t01_b001", "imagine you needed a team of missile engineers", 1.0),
        tr("t01_b002", "imagine you needed a team of missile engineers", 6.0),   # keeper
        tr("t01_b003", "there is no engineer", 11.0),                           # keeper
        tr("t01_b004", "there is no", 14.0),                                    # partial retry
    ]
    al = align_script(s, "en", trs, TAKES)
    l1, l2 = al.lines
    assert l1.status == "ok" and len(l1.attempts) == 2
    assert l1.attempts[l1.chosen].start == 6.0
    assert l2.status == "ok" and l2.attempts[l2.chosen].start == 11.0
    assert not l2.attempts[-1].clean            # "there is no" is not the whole line
    assert al.unmatched_block_ids == ["t01_b001", "t01_b004"]


def test_keeper_can_be_in_a_later_take():
    s = script_en("Claude built the whole thing in one night.")
    trs = [tr("t01_b001", "claude built the whole thing in one night", 2.0),
           tr("t02_b001", "claude built the whole thing in one night", 3.0, take="t02")]
    line = align_script(s, "en", trs, TAKES).lines[0]
    assert line.attempts[line.chosen].take_id == "t02"


def test_stutter_is_skipped_inside_the_attempt():
    s = script_en("What went wrong")
    trs = [tr("t01_b001", "what went what went wrong", 1.0)]
    line = align_script(s, "en", trs, TAKES).lines[0]
    chosen = line.attempts[line.chosen]
    assert chosen.clean and chosen.start == 1.6      # starts at the SECOND "what"


def test_missing_line_is_reported():
    s = script_en("First line here.", "This line was never recorded at all.")
    al = align_script(s, "en", [tr("t01_b001", "first line here", 1.0)], TAKES)
    assert al.lines[1].status == "missing" and al.lines[1].chosen is None


def test_arabic_spelling_variants_match():
    s = Script(lines=[ScriptLine(id="L01", ar="أطلقتْ جوجل أداةً جديدةً للذكاء الاصطناعي")])
    trs = [BlockTranscript(block_id="t01_b001", take_id="t01",
                           words=words_at("اطلقت جوجل اداة جديدة للذكاء الاصطناعي", 0.5))]
    line = align_script(s, "ar", trs, TAKES).lines[0]
    assert line.status == "ok" and line.attempts[line.chosen].coverage == 1.0


def test_attempt_segment_uses_speech_runs_and_never_pads_into_neighbours():
    al = align_script(script_en("hello there my friends"),
                      "en", [tr("t01_b001", "hello there my friends", 2.0)], TAKES)
    att = al.lines[0].attempts[0]
    sm = SpeechMap(take_id="t01", duration=10, frame_s=0.01, floor_db=-52,
                   runs=[Span(start=0.5, end=1.85), Span(start=1.95, end=3.3), Span(start=3.4, end=5.0)])
    a, b, warns = attempt_segment(att, sm, 0.25)
    # starts 0.02 s after the neighbour run (no room for the full pad), ends inside the gap
    assert a == 1.87 and b == 3.38
    assert any("tight" in w for w in warns)


def test_restart_without_pause_is_cut_on_word_times():
    words = words_at("imagine you needed imagine you needed a team", 1.0)
    trs = [BlockTranscript(block_id="t01_b001", take_id="t01", words=words)]
    al = align_script(script_en("imagine you needed a team"), "en", trs, TAKES)
    att = al.lines[0].attempts[al.lines[0].chosen]
    assert att.start == 1.9
    sm = SpeechMap(take_id="t01", duration=10, frame_s=0.01, floor_db=-52,
                   runs=[Span(start=1.0, end=3.3)])        # one continuous run: no pause
    a, b, warns = attempt_segment(att, sm, 0.25, take_words=[(w.start, w.end) for w in words])
    assert 1.78 <= a < 1.9 and b == 3.3 + 0.25
    assert any("word times" in w for w in warns)
