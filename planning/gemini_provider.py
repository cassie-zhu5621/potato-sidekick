"""Shared low-latency Gemini structured-output client for planning and judging."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "gemini-3.5-flash"

# ONE client for the process, not one per call.
#
# call_json used to do `genai.Client(api_key=key)` every time. A client owns the
# connection pool, so a fresh one per call throws away the TLS handshake and the
# auth exchange and pays for them again -- and it silently defeated the warm-up
# added to avoid exactly that cost: warm() was heating a client that was thrown
# away before the real call, which then built its own.
#
# Cached under a lock because the planner and the judge can both be in flight.
# Token usage from the most recent call, for the audit record. Module-level
# rather than returned, so `call_json`'s two-value contract -- which both
# providers implement -- does not have to change for a diagnostic.
LAST_USAGE: dict[str, int] = {}

_CLIENT = None
_CLIENT_KEY = None
_CLIENT_LOCK = __import__("threading").Lock()


def _client(key: str):
    global _CLIENT, _CLIENT_KEY
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT_KEY != key:
            from google import genai
            _CLIENT = genai.Client(api_key=key)
            _CLIENT_KEY = key
        return _CLIENT


def model_name(override: str | None = None) -> str:
    return override or os.environ.get("NOTICEBOT_GEMINI_MODEL", DEFAULT_MODEL)


def image_part(jpeg: bytes, *, resolution: str | None = None) -> dict[str, Any]:
    """A JPEG as a request part. `resolution` IS OMITTED UNLESS ASKED FOR.

    It used to fall back to "low" whenever nothing set it, so every call sent a
    downscale request that nobody had chosen -- and removing the variable from
    .env did not stop it, because the default lived here. That is the wrong shape
    for a knob: absent should mean "we have no opinion, use the service's own
    default", not "silently pick the cheapest option". The boxes this affects
    decide `richest_pan`, i.e. where the robot turns, so degrading them is a
    choice worth making on purpose or not at all.

    Set NOTICEBOT_GEMINI_MEDIA_RESOLUTION (or pass `resolution=`) to opt in.
    """
    part: dict[str, Any] = {
        "type": "image",
        "data": base64.standard_b64encode(jpeg).decode("ascii"),
        "mime_type": "image/jpeg",
    }
    res = resolution or os.environ.get("NOTICEBOT_GEMINI_MEDIA_RESOLUTION", "")
    if str(res).strip():
        part["resolution"] = str(res).strip()
    return part


def warm(model: str | None = None) -> None:
    """Open the connection and wake the model BEFORE anyone is waiting on it.

    Measured on the bench: first call 76.4 s, second 16.4 s, same five images and
    the same 750 KB. The extra minute is DNS, TLS, auth and a cold model -- paid
    once per process, by whoever happens to ask first.

    That is exactly the bug STT.warm() exists to prevent, one subsystem over: the
    first request of every session failed to S8 and the second worked, which
    reads as a flaky robot rather than a cold cache. The cost is real either way;
    warming only moves it to a moment with nobody standing there.

    Never raises. A warm-up that fails is not a session that fails -- the real
    call will produce the real error, with the real audit record attached.
    """
    import time as _t
    t0 = _t.time()
    try:
        call_json("reply with {\"ok\": true}",
                  {"type": "object", "properties": {"ok": {"type": "boolean"}},
                   "required": ["ok"]},
                  model=model, max_output_tokens=32)
        print(f"[gemini] warm in {_t.time() - t0:.1f}s "
              f"(one text token; a five-image call will be slower)")
    except Exception as e:
        print(f"[gemini] warm-up failed ({type(e).__name__}: {e}); "
              f"the first real call will pay the cold start")


def call_json(
    prompt: str,
    schema: dict[str, Any],
    *,
    images: Iterable[bytes] = (),
    labels: Iterable[str] | None = None,
    model: str | None = None,
    max_output_tokens: int | None = None,
) -> tuple[dict[str, Any], str]:
    """Call Gemini once with independently labelled images and strict JSON output.

    OUTPUT BUDGET. `.env` carries NOTICEBOT_GEMINI_MAX_OUTPUT_TOKENS and nothing
    read it -- the value looked like configuration and configured nothing. The
    hard-coded 2048 that was used instead is not enough for a five-frame room:
    a planner reply with eight `seen` labels and a box per detection ran past it
    and came back as JSON truncated mid-string, which surfaces as

        parse-fail: Expecting ',' delimiter: line 1 column 4273

    -- a message about commas, from a model that had answered correctly and been
    cut off. The retry then happened to fit and looked like a flaky model rather
    than a budget. 4096 leaves room for a busy room; the env var is now live so a
    busier one can be given more without editing code.
    """
    load_dotenv(ROOT / ".env")
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing from .env")

    image_list = list(images)
    label_list = list(labels or [])
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for index, jpeg in enumerate(image_list):
        label = label_list[index] if index < len(label_list) else f"image_{index}"
        content.append({"type": "text", "text": f"IMAGE {index}: {label}"})
        content.append(image_part(jpeg))

    budget = max_output_tokens or int(
        os.environ.get("NOTICEBOT_GEMINI_MAX_OUTPUT_TOKENS", 8192))

    client = _client(key)
    interaction = client.interactions.create(
        model=model_name(model),
        input=content,
        store=False,
        service_tier=os.environ.get("NOTICEBOT_GEMINI_SERVICE_TIER", "priority"),
        generation_config={
            "thinking_level": os.environ.get(
                "NOTICEBOT_GEMINI_THINKING_LEVEL", "minimal"
            ),
            "max_output_tokens": budget,
        },
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": schema,
        },
    )
    # WHAT THE CALL ACTUALLY COST. Published for the caller to record, because
    # without it the only observable is wall-clock latency and every explanation
    # for a slow call is a guess. Measured 2026-08-05: judge 40-139 s against
    # planner 6-17 s, on the SAME model, the SAME 1280x720 x5 images (4.6 MPx
    # either way -- the KB difference is only compressibility), a SHORTER prompt
    # and a SIMPLER schema. Model, image tokens, prompt size, schema depth and
    # thinking_level were all ruled out by inspection; token usage is the next
    # thing to look at and it was being thrown away.
    #
    # Best-effort: the field name has moved between SDK versions and a missing
    # usage report must not fail a call that otherwise succeeded.
    try:
        u = (getattr(interaction, "usage", None)
             or getattr(interaction, "usage_metadata", None))
        if u is not None:
            LAST_USAGE.clear()
            for k in ("input_tokens", "output_tokens", "total_tokens",
                      "thinking_tokens", "reasoning_tokens", "cached_tokens",
                      "prompt_token_count", "candidates_token_count",
                      "thoughts_token_count", "total_token_count"):
                v = getattr(u, k, None)
                if isinstance(v, int):
                    LAST_USAGE[k] = v
            if not LAST_USAGE and isinstance(u, dict):
                LAST_USAGE.update({k: v for k, v in u.items()
                                   if isinstance(v, int)})
    except Exception:
        pass

    text = interaction.output_text
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Gemini structured response is not a JSON object")
    return value, text
