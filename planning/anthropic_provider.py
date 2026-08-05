"""Anthropic implementation of the provider seam. Selected by

    NOTICEBOT_LLM_PROVIDER=anthropic

and by nothing else -- `gemini_provider.py` is untouched and stays the default.
See `provider.py` for the interface this file has to honour.

STRUCTURED OUTPUT COMES BACK AS AN OBJECT, NOT AS TEXT TO BE PARSED.

    The schema is handed over as a tool's `input_schema` and the model is forced
    to call that tool. What arrives is `tool_use.input` -- already a dict.

    This removes a whole failure class rather than working around it. On the
    Gemini path the reply is a JSON string, and when it runs past the output
    budget the string is truncated mid-value, so the error is

        parse-fail: Expecting ',' delimiter: line 1 column 4273

    -- a message about commas, from a model that had answered correctly and been
    cut off. Nothing in it points at a token budget. Here, running out of room
    surfaces as stop_reason == "max_tokens", which says what happened.

ONE CLIENT PER PROCESS.

    Cached, for the reason measured on the Gemini path tonight: constructing a
    client per call throws away the connection and the auth exchange and pays for
    them again, which cost 82 s on the first call and 20 s on the second, of what
    is otherwise a 2-3 s request.

MODEL SELECTION IGNORES ANOTHER PROVIDER'S ENV VAR.

    judge.py passes NOTICEBOT_GEMINI_JUDGE_MODEL through as `model`. If someone
    has set it, that is a Gemini model name arriving at an Anthropic endpoint --
    a 404 whose cause is three files away. An override that is not a Claude model
    is dropped, with a line saying so.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "claude-sonnet-5"

_CLIENT = None
_CLIENT_KEY = None
_CLIENT_LOCK = threading.Lock()


def model_name(override: str | None = None) -> str:
    if override and not str(override).lower().startswith("claude"):
        print(f"[anthropic] ignoring model {override!r} -- not a Claude model "
              f"(a NOTICEBOT_GEMINI_* variable is probably still set)")
        override = None
    return override or os.environ.get("NOTICEBOT_ANTHROPIC_MODEL", DEFAULT_MODEL)


def _client(key: str):
    global _CLIENT, _CLIENT_KEY
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT_KEY != key:
            import anthropic
            _CLIENT = anthropic.Anthropic(api_key=key)
            _CLIENT_KEY = key
        return _CLIENT


def image_part(jpeg: bytes) -> dict[str, Any]:
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": base64.standard_b64encode(jpeg).decode("ascii")}}


def call_json(
    prompt: str,
    schema: dict[str, Any],
    *,
    images: Iterable[bytes] = (),
    labels: Iterable[str] | None = None,
    model: str | None = None,
    max_output_tokens: int | None = None,
) -> tuple[dict[str, Any], str]:
    """-> (parsed dict, the same thing as JSON text).

    The text half of the return exists only because the Gemini path had a raw
    string to hand back and `planner.plan()` stores it as `raw` in the audit
    record. Re-serialising keeps the audit files comparable between providers,
    which is the whole reason for running both through one seam.
    """
    load_dotenv(ROOT / ".env")
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is missing from .env")

    budget = max_output_tokens or int(
        os.environ.get("NOTICEBOT_ANTHROPIC_MAX_OUTPUT_TOKENS", 8192))

    # Each image is announced by name first. The labels carry meaning the pixels
    # do not -- which pan station a frame came from, where in a burst it sits --
    # and the planner's `view_index` refers back to that order.
    label_list = list(labels or [])
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for i, jpeg in enumerate(images):
        name = label_list[i] if i < len(label_list) else f"image_{i}"
        content.append({"type": "text", "text": f"IMAGE {i}: {name}"})
        content.append(image_part(jpeg))

    tool = {"name": "answer",
            "description": "Return the answer in the required structure.",
            "input_schema": schema}

    msg = _client(key).messages.create(
        model=model_name(model),
        max_tokens=budget,
        tools=[tool],
        tool_choice={"type": "tool", "name": "answer"},
        messages=[{"role": "user", "content": content}],
    )

    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            value = block.input
            if not isinstance(value, dict):
                raise ValueError("structured response is not a JSON object")
            return value, json.dumps(value)

    # No tool block. Say which of the two reasons it was -- they need different
    # fixes and "no structured response" alone points at neither.
    stop = getattr(msg, "stop_reason", "?")
    if stop == "max_tokens":
        raise ValueError(f"ran out of output budget ({budget} tokens) before the "
                         f"answer was complete -- raise "
                         f"NOTICEBOT_ANTHROPIC_MAX_OUTPUT_TOKENS")
    raise ValueError(f"no structured response (stop_reason={stop})")


def warm(model: str | None = None) -> None:
    """Open the connection before anyone is waiting on it. Never raises.

    Same purpose as the Gemini one: the first call of a process pays for DNS,
    TLS and auth, and whoever asks first should not be a participant. A warm-up
    that fails is not a session that fails -- the real call will produce the real
    error with the real audit record attached.
    """
    t0 = time.time()
    try:
        call_json("Call the tool with ok set to true.",
                  {"type": "object",
                   "properties": {"ok": {"type": "boolean"}},
                   "required": ["ok"]},
                  model=model, max_output_tokens=64)
        print(f"[anthropic] warm in {time.time() - t0:.1f}s "
              f"({model_name(model)}; a five-image call will be slower)")
    except Exception as e:
        print(f"[anthropic] warm-up failed ({type(e).__name__}: {e}); "
              f"the first real call will pay the cold start")
