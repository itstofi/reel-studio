"""Claude access for Reel Studio: structured JSON (pydantic-validated) and plain text.

Primary path: the Claude Code CLI in print mode (uses the creator's Claude login, no API key):

    claude -p <prompt> --output-format json --json-schema '<strict schema>' --model <SETTINGS.llm.model>
           --no-session-persistence --tools "" --strict-mcp-config [--system-prompt ..] [--effort ..]

run in a fresh temp dir. VERIFIED envelope (CLI 2.1.323): one JSON object
``{"type":"result","subtype":"success","is_error":false,"result":"<text>",
"structured_output":{...},"total_cost_usd":0.04,...}``.

Fallback when ANTHROPIC_API_KEY is set and the CLI is missing or fails: the Anthropic SDK
(``client.beta.messages.create`` with structured outputs + server-side refusal fallback).
"""
from __future__ import annotations

import copy
import json
import logging
import os
import subprocess
import tempfile
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import SETTINGS

log = logging.getLogger("reelstudio.llm")

M = TypeVar("M", bound=BaseModel)

_SDK_BETAS = ["server-side-fallback-2026-07-01"]
_SDK_MAX_TOKENS = 16000


class ClaudeError(RuntimeError):
    """Claude could not produce a usable answer (CLI/API failure, refusal, bad JSON)."""


class ClaudeRefusal(ClaudeError):
    """The model declined the request (stop_reason == "refusal")."""


# ── Strict JSON schema ───────────────────────────────────────────────────────
_DROP_KEYS = {"title", "default", "$defs", "definitions"}


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic model -> self-contained strict JSON schema.

    $refs are inlined, and every object gets ``additionalProperties: false`` with ALL of its
    properties required (optional fields stay nullable through their ``anyOf [.., null]``).
    That is what structured outputs need to guarantee an exact shape.
    """
    raw = model.model_json_schema()
    defs = raw.get("$defs", {}) | raw.get("definitions", {})

    def resolve(node: Any, stack: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [resolve(n, stack) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            if name in stack:
                raise ValueError(f"recursive schema ({name}) cannot be inlined")
            merged = {**copy.deepcopy(defs[name]), **{k: v for k, v in node.items() if k != "$ref"}}
            return resolve(merged, stack + (name,))
        out = {k: resolve(v, stack) for k, v in node.items() if k not in _DROP_KEYS}
        if out.get("type") == "object" or "properties" in out:
            props = out.get("properties", {})
            out["type"] = "object"
            out["properties"] = props
            out["required"] = list(props)
            out["additionalProperties"] = False
        return out

    return resolve(raw, ())


# ── Availability ─────────────────────────────────────────────────────────────
def available() -> tuple[bool, str]:
    """(usable, reason): the CLI is found, or an API key is set for the SDK fallback."""
    binary = SETTINGS.claude_bin
    if binary:
        return True, f"claude CLI at {binary}"
    if SETTINGS.anthropic_key_set:
        return True, "Anthropic API (ANTHROPIC_API_KEY)"
    return False, "Claude Code CLI not found (set CLAUDE_BIN) and ANTHROPIC_API_KEY is not set"


# ── Public calls ─────────────────────────────────────────────────────────────
def ask_json(prompt: str, schema: type[M], *, system: str | None = None, effort: str | None = None,
             timeout: float | None = None) -> M:
    """Ask Claude for JSON matching ``schema``; returns a validated instance."""
    s = strict_schema(schema)
    data = _dispatch(prompt, system=system, effort=effort, timeout=timeout, schema=s)
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise ClaudeError(f"Claude's JSON does not match {schema.__name__}: {exc}") from exc


def ask_text(prompt: str, *, system: str | None = None, effort: str | None = None,
             timeout: float | None = None) -> str:
    """Ask Claude for plain text."""
    return _dispatch(prompt, system=system, effort=effort, timeout=timeout, schema=None)


def _dispatch(prompt: str, *, system: str | None, effort: str | None, timeout: float | None,
              schema: dict | None) -> Any:
    """CLI first; SDK fallback only when an API key is set. Returns parsed JSON (or text)."""
    timeout = timeout or SETTINGS.llm.timeout_s
    binary = SETTINGS.claude_bin
    cli_error: ClaudeError | None = None
    if binary:
        try:
            return _via_cli(binary, prompt, system=system, effort=effort, timeout=timeout, schema=schema)
        except ClaudeRefusal:
            raise
        except ClaudeError as exc:
            cli_error = exc
            log.warning("claude CLI failed: %s", exc)
    if SETTINGS.anthropic_key_set:
        return _via_sdk(prompt, system=system, effort=effort, timeout=timeout, schema=schema)
    if cli_error is not None:
        raise cli_error
    raise ClaudeError(available()[1])


# ── CLI path ─────────────────────────────────────────────────────────────────
def cli_command(binary: str, prompt: str, *, system: str | None, effort: str | None,
                schema: dict | None) -> list[str]:
    cmd = [binary, "-p", prompt, "--output-format", "json"]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    cmd += ["--model", SETTINGS.llm.model, "--no-session-persistence", "--tools", "", "--strict-mcp-config"]
    if system:
        cmd += ["--system-prompt", system]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def _via_cli(binary: str, prompt: str, *, system: str | None, effort: str | None, timeout: float,
             schema: dict | None) -> Any:
    cmd = cli_command(binary, prompt, system=system, effort=effort, schema=schema)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}  # allow a nested CLI run
    with tempfile.TemporaryDirectory(prefix="reel-claude-") as cwd:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                                  env=env, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as exc:
            raise ClaudeError(f"claude CLI timed out after {timeout:.0f} s") from exc
        except OSError as exc:
            raise ClaudeError(f"claude CLI could not start: {exc}") from exc
    envelope = parse_envelope(proc.stdout)
    if envelope is None:
        tail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise ClaudeError(f"claude CLI exited {proc.returncode} without a result envelope: {tail}")
    return _from_envelope(envelope, structured=schema is not None)


def parse_envelope(stdout: str) -> dict | None:
    """The single result object; tolerate stray lines by taking the last parseable result."""
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            return obj
    return None


def _from_envelope(env: dict, *, structured: bool) -> Any:
    if env.get("is_error") or env.get("subtype") != "success":
        detail = env.get("result") or env.get("errors") or env.get("error") or ""
        raise ClaudeError(f"claude CLI error ({env.get('subtype')}): {str(detail)[:600]}")
    log.info("claude CLI ok: cost $%s, %s ms", env.get("total_cost_usd"), env.get("duration_ms"))
    if not structured:
        return str(env.get("result") or "")
    if env.get("structured_output") is not None:
        return env["structured_output"]
    return _loads(env.get("result") or "")


def _loads(text: str) -> Any:
    """JSON from model text; strips a ```json fence if one slipped in."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        return json.loads(t)
    except json.JSONDecodeError as exc:
        raise ClaudeError(f"Claude returned invalid JSON: {text[:300]!r}") from exc


# ── SDK fallback ─────────────────────────────────────────────────────────────
def _sdk_client(timeout: float):
    import anthropic

    return anthropic.Anthropic(timeout=timeout)


def _via_sdk(prompt: str, *, system: str | None, effort: str | None, timeout: float,
             schema: dict | None) -> Any:
    import anthropic

    output_config: dict[str, Any] = {"effort": effort or SETTINGS.llm.effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    kwargs: dict[str, Any] = dict(
        model=SETTINGS.llm.model, max_tokens=_SDK_MAX_TOKENS, betas=_SDK_BETAS, fallbacks="default",
        output_config=output_config, messages=[{"role": "user", "content": prompt}])
    if system:
        kwargs["system"] = system
    try:
        resp = _sdk_client(timeout).beta.messages.create(**kwargs)
    except anthropic.BadRequestError as exc:
        raise ClaudeError(f"Anthropic API rejected the request: {exc.message}") from exc
    except anthropic.AuthenticationError as exc:
        raise ClaudeError("Anthropic API key was rejected (check ANTHROPIC_API_KEY)") from exc
    except anthropic.RateLimitError as exc:
        raise ClaudeError(f"Anthropic API rate limit: {exc.message}") from exc
    except anthropic.APIStatusError as exc:
        raise ClaudeError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise ClaudeError(f"cannot reach the Anthropic API: {exc}") from exc

    if resp.stop_reason == "refusal":
        details = resp.stop_details
        why = f"{getattr(details, 'category', None)}: {getattr(details, 'explanation', '')}" if details else ""
        raise ClaudeRefusal(f"Claude declined the request ({why})")
    if resp.stop_reason == "max_tokens":
        raise ClaudeError("Claude's answer was cut off (max_tokens)")
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), None)
    if text is None:
        raise ClaudeError("Anthropic API returned no text block")
    return _loads(text) if schema is not None else text
