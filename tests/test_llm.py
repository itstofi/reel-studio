"""llm.claude + llm.tasks against a fake `claude` binary (argv recorded, canned envelopes)."""
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import numpy as np
import pytest
from pydantic import BaseModel

from reelstudio import config
from reelstudio.edit.planner import build_plan
from reelstudio.llm import claude
from reelstudio.llm.claude import ClaudeError, ClaudeRefusal, ask_json, ask_text, available, strict_schema
from reelstudio.llm.tasks import SYSTEM, ClaudeTasks
from reelstudio.media.audioio import write_wav
from reelstudio.models import Block, BlockTranscript, Project, Span, SpeechMap, Take, Word

FAKE = """#!{python}
import json, os, sys
d = os.environ["FAKE_CLAUDE_DIR"]
with open(os.path.join(d, "calls.jsonl"), "a") as fh:
    fh.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}) + "\\n")
queue_path = os.path.join(d, "queue.json")
queue = json.load(open(queue_path))
item = queue.pop(0)
json.dump(queue, open(queue_path, "w"))
if "stderr" in item:
    sys.stderr.write(item["stderr"])
sys.stdout.write(item["raw"] if "raw" in item else json.dumps(item["env"]))
sys.exit(item.get("exit", 0))
"""


def ok(structured=None, result=None):
    return {"env": {"type": "result", "subtype": "success", "is_error": False,
                    "result": result if result is not None else json.dumps(structured),
                    "structured_output": structured, "total_cost_usd": 0.01, "duration_ms": 5}}


class Fake:
    def __init__(self, d: Path):
        self.dir = d

    def queue(self, *items):
        (self.dir / "queue.json").write_text(json.dumps(list(items)))

    @property
    def calls(self):
        p = self.dir / "calls.jsonl"
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def remaining(self):
        return json.loads((self.dir / "queue.json").read_text())


@pytest.fixture
def fake(tmp_path, monkeypatch):
    binary = tmp_path / "claude"
    binary.write_text(FAKE.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CLAUDE_BIN", str(binary))
    monkeypatch.setenv("FAKE_CLAUDE_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    f = Fake(tmp_path)
    f.queue()
    return f


def arg(argv, flag):
    return argv[argv.index(flag) + 1]


# ── strict schema ─────────────────────────────────────────────────────────────
class Inner(BaseModel):
    a: str | None = None
    tags: list[str] = []


class Outer(BaseModel):
    name: str
    inner: Inner
    many: list[Inner]
    meta: dict[str, int] = {}


def _objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for v in node.values():
            yield from _objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _objects(v)


def test_strict_schema_inlines_refs_and_requires_everything():
    s = strict_schema(Outer)
    text = json.dumps(s)
    assert "$ref" not in text and "$defs" not in text and '"default"' not in text
    objs = list(_objects(s))
    assert len(objs) == 4  # Outer, inner, many[], meta
    for o in objs:
        assert o["additionalProperties"] is False
        assert o["required"] == list(o["properties"])
    assert s["required"] == ["name", "inner", "many", "meta"]
    inner = s["properties"]["inner"]
    assert inner["required"] == ["a", "tags"]
    assert {"type": "null"} in inner["properties"]["a"]["anyOf"]          # optional stays nullable
    assert s["properties"]["many"]["items"]["properties"]["tags"]["type"] == "array"


def test_strict_schema_rejects_recursive_models():
    class Node(BaseModel):
        children: list["Node"] = []

    Node.model_rebuild()
    with pytest.raises(ValueError):
        strict_schema(Node)


# ── ask_json / ask_text via the CLI ──────────────────────────────────────────
class Answer(BaseModel):
    lines: list[str]


def test_ask_json_builds_the_verified_command(fake):
    fake.queue(ok({"lines": ["hello"]}))
    out = ask_json("say hi", Answer, system="be brief", effort="low")
    assert out == Answer(lines=["hello"])
    (call,) = fake.calls
    argv = call["argv"]
    assert argv[:2] == ["-p", "say hi"]
    assert arg(argv, "--output-format") == "json"
    assert json.loads(arg(argv, "--json-schema")) == strict_schema(Answer)
    assert arg(argv, "--model") == config.SETTINGS.llm.model == "claude-opus-5-5"
    assert "--no-session-persistence" in argv and "--strict-mcp-config" in argv
    assert arg(argv, "--tools") == ""
    assert arg(argv, "--system-prompt") == "be brief" and arg(argv, "--effort") == "low"
    # fresh temp cwd (not the project), removed afterwards
    assert "reel-claude-" in call["cwd"] and not Path(call["cwd"]).exists()


def test_ask_json_omits_optional_flags_and_falls_back_to_result_text(fake):
    fake.queue(ok(None, result='```json\n{"lines": ["a", "b"]}\n```'))
    assert ask_json("x", Answer).lines == ["a", "b"]
    argv = fake.calls[0]["argv"]
    assert "--system-prompt" not in argv and "--effort" not in argv


def test_ask_text_reads_result(fake):
    fake.queue(ok(None, result="plain words"))
    assert ask_text("x") == "plain words"
    assert "--json-schema" not in fake.calls[0]["argv"]


@pytest.mark.parametrize("item, match", [
    ({"env": {"type": "result", "subtype": "success", "is_error": True, "result": "API Error: 500"}}, "API Error"),
    ({"env": {"type": "result", "subtype": "error_max_structured_output_retries", "is_error": False}},
     "error_max_structured_output_retries"),
    ({"raw": "not json", "stderr": "Invalid API key", "exit": 1}, "Invalid API key"),
    ({"raw": "", "exit": 2}, "exited 2"),
])
def test_error_envelopes_raise(fake, item, match):
    fake.queue(item)
    with pytest.raises(ClaudeError, match=match):
        ask_json("x", Answer)


def test_schema_mismatch_and_bad_json_raise(fake):
    fake.queue(ok({"wrong": 1}), ok(None, result="{nope"))
    with pytest.raises(ClaudeError, match="does not match"):
        ask_json("x", Answer)
    with pytest.raises(ClaudeError, match="invalid JSON"):
        ask_json("x", Answer)


def test_envelope_with_stray_lines(fake):
    env = ok({"lines": ["z"]})["env"]
    fake.queue({"raw": "warning: something\n" + json.dumps(env) + "\n"})
    assert ask_json("x", Answer).lines == ["z"]


def test_available(fake, monkeypatch):
    assert available()[0] is True
    monkeypatch.delenv("CLAUDE_BIN")
    monkeypatch.setattr(config, "find_claude_bin", lambda: None)
    usable, reason = available()
    assert usable is False and "ANTHROPIC_API_KEY" in reason
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert available() == (True, "Anthropic API (ANTHROPIC_API_KEY)")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(ClaudeError, match="not found"):
        ask_text("x")


# ── SDK fallback ─────────────────────────────────────────────────────────────
class FakeSDK:
    def __init__(self, response=None, error=None):
        self.kwargs = None
        self.response, self.error = response, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def sdk_response(text, stop="end_turn", details=None):
    return SimpleNamespace(stop_reason=stop, stop_details=details,
                           content=[SimpleNamespace(type="thinking", thinking=""),
                                    SimpleNamespace(type="text", text=text)])


def test_sdk_fallback_when_cli_fails(fake, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    sdk = FakeSDK(sdk_response('{"lines": ["from api"]}'))
    monkeypatch.setattr(claude, "_sdk_client", lambda timeout: sdk)
    fake.queue({"raw": "", "stderr": "boom", "exit": 1})
    assert ask_json("prompt", Answer, system="sys").lines == ["from api"]
    kw = sdk.kwargs
    assert kw["model"] == "claude-opus-5-5" and kw["max_tokens"] == 16000
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["output_config"] == {"effort": "medium",
                                   "format": {"type": "json_schema", "schema": strict_schema(Answer)}}
    assert kw["system"] == "sys" and kw["messages"] == [{"role": "user", "content": "prompt"}]
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(kw)


def test_sdk_refusal_and_errors(monkeypatch):
    monkeypatch.setattr(config, "find_claude_bin", lambda: None)
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    details = SimpleNamespace(category="cyber", explanation="no")
    monkeypatch.setattr(claude, "_sdk_client", lambda timeout: FakeSDK(sdk_response("", "refusal", details)))
    with pytest.raises(ClaudeRefusal, match="cyber"):
        ask_json("x", Answer)
    resp = httpx.Response(429, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    err = anthropic.RateLimitError("slow down", response=resp, body=None)
    monkeypatch.setattr(claude, "_sdk_client", lambda timeout: FakeSDK(error=err))
    with pytest.raises(ClaudeError, match="rate limit"):
        ask_json("x", Answer)
    sdk = FakeSDK(sdk_response("just text"))
    monkeypatch.setattr(claude, "_sdk_client", lambda timeout: sdk)
    assert ask_text("x") == "just text" and "format" not in sdk.kwargs["output_config"]


# ── tasks ────────────────────────────────────────────────────────────────────
def indexed(*texts, order=None):
    idx = order if order is not None else range(len(texts))
    return ok({"lines": [{"i": i, "text": t} for i, t in zip(idx, texts)]})


def test_translate_lines(fake):
    fake.queue(indexed("Breaking news from the AI world.", "I am Juaid from Iraq.", order=[1, 0]))
    out = ClaudeTasks().translate_lines(["أنا جويد من العراق.", "خبر عاجل [عاجل]"], source_lang="ar")
    assert out == ["I am Jweed from Iraq.", "Breaking news from the AI world."]   # by index, name fixed
    argv = fake.calls[0]["argv"]
    assert arg(argv, "--system-prompt") == SYSTEM and "ALWAYS ENGLISH" in SYSTEM and '"Jweed"' in SYSTEM
    prompt = arg(argv, "-p")
    assert "أنا جويد من العراق." in prompt and "same order" in prompt
    assert arg(argv, "--effort") == config.SETTINGS.llm.effort
    assert ClaudeTasks().translate_lines([], source_lang="ar") == [] and len(fake.calls) == 1


def test_translate_lines_retries_on_wrong_count_then_fails(fake):
    fake.queue(indexed("only one"), indexed("one", "two"))
    assert ClaudeTasks().translate_lines(["أ", "ب"], source_lang="ar") == ["one", "two"]
    assert "return exactly 2 items" in arg(fake.calls[1]["argv"], "-p")
    fake.queue(indexed("x"), indexed("y"))
    with pytest.raises(ClaudeError, match="exactly 2"):
        ClaudeTasks().translate_lines(["أ", "ب"], source_lang="ar")
    fake.queue(indexed("مرحبا"))
    with pytest.raises(ClaudeError, match="Arabic"):
        ClaudeTasks().translate_lines(["أ"], source_lang="ar")


def test_plan_unscripted_filters_ids(fake):
    rows = [{"block_id": "t01_b001", "take_id": "t01", "start": 1.0, "end": 2.0, "text": "hi I'm Juaid"},
            {"block_id": "t01_b002", "take_id": "t01", "start": 3.0, "end": 4.0, "text": "hi I'm Jweed"}]
    fake.queue(ok({"beats": [
        {"block_ids": ["t01_b002", "t09_b999"], "caption_en": "Hi, I'm Juaid.", "reason": "attempt 2 of 2"},
        {"block_ids": ["t01_b002"], "caption_en": "dup", "reason": "reused"},
        {"block_ids": ["nope"], "caption_en": "ghost", "reason": "x"}]}))
    out = ClaudeTasks().plan_unscripted(rows)
    assert out == [{"block_ids": ["t01_b002"], "caption_en": "Hi, I'm Jweed.", "reason": "attempt 2 of 2"}]
    argv = fake.calls[0]["argv"]
    assert "LAST clean attempt" in arg(argv, "-p") and arg(argv, "--effort") == "high"


def g(beat_id, template, motion="fade+scale in, rows reveal, fade out", offset=0.3, duration=3.0, **params):
    full = {k: None for k in ("text", "subtitle", "title", "items", "value", "prefix", "suffix", "label",
                              "source", "highlight", "name", "role")}
    full.update(params)
    return {"beat_id": beat_id, "template": template, "params": full, "beat_offset": offset,
            "duration": duration, "motion": motion}


def test_suggest_graphics_is_sparse_moving_and_honest(fake):
    beats = [{"beat_id": "B01", "caption_en": "95% of people use ChatGPT wrong.", "has_graphic": False},
             {"beat_id": "B02", "caption_en": "Anthropic released Claude today.", "has_graphic": False},
             {"beat_id": "B03", "caption_en": "Breaking news.", "has_graphic": True},
             {"beat_id": "B04", "caption_en": "It costs a lot.", "has_graphic": False}]
    fake.queue(ok({"graphics": [
        g("B01", "stat", value="95", suffix="%", label="use it wrong", duration=9),
        g("B02", "logo", source="Anthropic", offset=-1),
        g("B03", "title", text="BREAKING"),                       # beat already has a graphic
        g("B04", "stat", value="$20", label="per month"),          # number he never said
        g("B99", "title", text="ghost"),                           # unknown beat
    ]}))
    out = ClaudeTasks().suggest_graphics(beats)
    assert [o["beat_id"] for o in out] == ["B01", "B02"]
    assert out[0] == {"beat_id": "B01", "template": "stat", "duration": 6.0, "beat_offset": 0.3,
                      "params": {"value": "95", "suffix": "%", "label": "use it wrong",
                                 "motion": "fade+scale in, rows reveal, fade out"}}
    assert out[1]["beat_offset"] == 0.0 and out[1]["params"]["source"] == "Anthropic"
    prompt = arg(fake.calls[0]["argv"], "-p")
    assert "EVERY graphic moves" in prompt and "At most 2 graphics" in prompt and "never cover his face" in prompt


def test_draft_script_line_aligned(fake):
    fake.queue(ok({"title": "Anthropic تطلق Claude جديد", "hook_formula": "news_flash", "caption": "c #AI",
                   "lines": [
                       {"ar": "خبر عاجل من عالم الذكاء الاصطناعي.", "en": "Breaking news from the AI world.",
                        "onscreen_ar": "عاجل", "onscreen_en": "BREAKING"},
                       {"ar": "أطلقت Anthropic نموذجا جديدا اليوم.", "en": "Anthropic released a new model today.",
                        "onscreen_ar": None, "onscreen_en": None},
                       {"ar": "احفظ الفيديو.", "en": "Save this video.", "onscreen_ar": None, "onscreen_en": None}]}))
    out = ClaudeTasks().draft_script("Anthropic launches a new Claude model", 25)
    assert out["ar"].splitlines() == ["خبر عاجل من عالم الذكاء الاصطناعي. [عاجل]",
                                      "أطلقت Anthropic نموذجا جديدا اليوم.", "احفظ الفيديو."]
    assert out["en"].splitlines() == ["Breaking news from the AI world. [BREAKING]",
                                      "Anthropic released a new model today.", "Save this video."]
    assert out["title"] and out["caption"] == "c #AI"
    prompt = arg(fake.calls[0]["argv"], "-p")
    assert "15-30s" in prompt and "35-60" in prompt and "Fusha-light" in prompt and "CTA" in prompt

    fake.queue(ok({"title": "t", "hook_formula": "contrarian", "caption": "c",
                   "lines": [{"ar": "Stop using it", "en": "توقف", "onscreen_ar": None, "onscreen_en": None}]}))
    with pytest.raises(ClaudeError, match="swapped"):
        ClaudeTasks().draft_script("x", "60-90s")


def test_proofread_captions(fake):
    fake.queue(indexed("Hi, I'm Jweed.", "Claude is made by Anthropic."))
    assert ClaudeTasks().proofread_captions(["hi im Juaid", "claude is made by antropic"]) == \
        ["Hi, I'm Jweed.", "Claude is made by Anthropic."]
    assert "do not rephrase" in arg(fake.calls[0]["argv"], "-p")


def test_claude_tasks_drive_the_unscripted_planner(fake, tmp_path):
    """ClaudeTasks satisfies PlannerLLM: build_plan runs plan_unscripted + suggest_graphics through it."""
    wav = write_wav(tmp_path / "a.wav", np.zeros(48000 * 10, dtype=np.float32))
    take = Take(id="t01", filename="a.mov", source_path="/x/a.mov", order=1, audio_path=str(wav))
    project = Project(id="p", name="p", takes=[take])
    blocks = [Block(id="t01_b001", take_id="t01", start=1.0, end=2.0, runs=[Span(start=1.0, end=2.0)]),
              Block(id="t01_b002", take_id="t01", start=4.0, end=5.0, runs=[Span(start=4.0, end=5.0)])]
    trs = [BlockTranscript(block_id=b.id, take_id="t01", text="Claude is here",
                           words=[Word(text="Claude", start=b.start, end=b.start + 0.4)]) for b in blocks]
    sm = SpeechMap(take_id="t01", duration=10, frame_s=0.01, floor_db=-52,
                   runs=[Span(start=1.0, end=2.0), Span(start=4.0, end=5.0)])
    fake.queue(ok({"beats": [{"block_ids": ["t01_b002"], "caption_en": "Claude is here.", "reason": "last"}]}),
               ok({"graphics": [g("B01", "logo", source="Claude")]}))
    plan = build_plan(project, None, trs, {"t01": sm}, blocks=blocks, verify_gaps=False, llm=ClaudeTasks())
    assert len(plan.beats) == 1 and plan.beats[0].caption_en == "Claude is here."
    assert plan.beats[0].segments[0].src_in == 3.75
    assert plan.beats[0].graphics[0].template == "logo" and fake.remaining() == []


# ── real CLI (costs ~$0.05) ─────────────────────────────────────────────────
@pytest.mark.network
def test_real_translate_lines():
    usable, reason = available()
    if not usable:
        pytest.skip(reason)
    out = ClaudeTasks(effort="low").translate_lines(
        ["مرحبا يا أصدقاء، أنا جويد من العراق.", "أطلقت أنثروبيك نموذجا جديدا اليوم. [عاجل]"], source_lang="ar")
    print(out)
    assert len(out) == 2
    assert "Jweed" in out[0] and "Anthropic" in out[1]
    assert not any(c for c in "".join(out) if "؀" <= c <= "ۿ")
