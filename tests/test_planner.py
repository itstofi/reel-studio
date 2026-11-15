"""Planner: one beat per script line in script order, last clean take, English captions."""
import numpy as np

from reelstudio.asr.fixes import apply_fixes
from reelstudio.edit.align import align_script
from reelstudio.edit.planner import build_plan, plan_summary
from reelstudio.media.audioio import write_wav
from reelstudio.models import (BlockTranscript, Project, ProjectSettings, Script, ScriptLine, Span,
                               SpeechMap, Take, Word)


def words_at(text, start, step=0.3):
    return [Word(text=w, start=round(start + i * step, 3), end=round(start + i * step + 0.24, 3))
            for i, w in enumerate(text.split())]


class FakeLLM:
    def __init__(self):
        self.translated = []

    def translate_lines(self, lines, *, source_lang):
        self.translated.extend(lines)
        return [f"EN({i})" for i, _ in enumerate(lines)]

    def plan_unscripted(self, blocks):
        return []

    def suggest_graphics(self, beats):
        return [{"beat_id": beats[0]["beat_id"], "template": "stat", "params": {"value": 95}},
                {"beat_id": "B99", "template": "title"}, {"beat_id": beats[0]["beat_id"], "template": "bogus"}]


def make_project(tmp_path, lang, lines):
    wav = write_wav(tmp_path / "a.wav", np.zeros(48000 * 20, dtype=np.float32))
    take = Take(id="t01", filename="a.mov", source_path="/x/a.mov", order=1, audio_path=str(wav))
    return Project(id="p", name="p", takes=[take], settings=ProjectSettings(lang=lang),
                   script=Script(lines=lines))


def test_arabic_cut_gets_english_captions_and_missing_line_is_kept(tmp_path):
    lines = [ScriptLine(id="L01", ar="مرحبا يا اصدقاء انا جويد", en="Hi friends, I am Juaid."),
             ScriptLine(id="L02", ar="خبر عاجل من عالم الذكاء الاصطناعي", onscreen="BREAKING"),
             ScriptLine(id="L03", ar="هذا السطر لم يسجل ابدا")]
    project = make_project(tmp_path, "ar", lines)
    trs = [BlockTranscript(block_id="t01_b001", take_id="t01", words=words_at("مرحبا يا اصدقاء انا جويد", 1.0)),
           BlockTranscript(block_id="t01_b002", take_id="t01",
                           words=words_at("خبر عاجل من عالم الذكاء الاصطناعي", 4.0))]
    sm = SpeechMap(take_id="t01", duration=20, frame_s=0.01, floor_db=-52,
                   runs=[Span(start=1.0, end=2.45), Span(start=4.0, end=5.75)])
    al = align_script(project.script, "ar", trs, project.takes)
    llm = FakeLLM()
    plan = build_plan(project, al, trs, {"t01": sm}, verify_gaps=False, llm=llm)
    assert [b.id for b in plan.beats] == ["B01", "B02", "B03"]
    assert plan.beats[0].caption_en == "Hi friends, I am Jweed."          # name fixed
    assert plan.beats[1].caption_en == "EN(0)"                             # translated (no English line)
    assert plan.beats[1].graphics[0].template == "title"                   # [BREAKING] on-screen text
    assert plan.beats[2].segments == [] and any("L03 is missing" in w for w in plan.warnings)
    seg = plan.beats[0].segments[0]
    assert seg.src_in == 0.75 and seg.src_out == 2.7                       # speech map + 0.25 s pads
    assert plan.beats[0].graphics[0].template == "stat"                    # valid suggestion kept
    assert "PLAN (3 beats" in plan_summary(plan)


def test_apply_fixes():
    assert apply_fixes("I am Juwaid from Iraq, juaid!") == "I am Jweed from Iraq, Jweed!"
