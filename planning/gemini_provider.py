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


def model_name(override: str | None = None) -> str:
    return override or os.environ.get("NOTICEBOT_GEMINI_MODEL", DEFAULT_MODEL)


def image_part(jpeg: bytes, *, resolution: str | None = None) -> dict[str, Any]:
    return {
        "type": "image",
        "data": base64.standard_b64encode(jpeg).decode("ascii"),
        "mime_type": "image/jpeg",
        "resolution": resolution or os.environ.get(
            "NOTICEBOT_GEMINI_MEDIA_RESOLUTION", "low"
        ),
    }


def call_json(
    prompt: str,
    schema: dict[str, Any],
    *,
    images: Iterable[bytes] = (),
    labels: Iterable[str] | None = None,
    model: str | None = None,
    max_output_tokens: int = 2048,
) -> tuple[dict[str, Any], str]:
    """Call Gemini once with independently labelled images and strict JSON output."""
    load_dotenv(ROOT / ".env")
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing from .env")

    from google import genai

    image_list = list(images)
    label_list = list(labels or [])
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for index, jpeg in enumerate(image_list):
        label = label_list[index] if index < len(label_list) else f"image_{index}"
        content.append({"type": "text", "text": f"IMAGE {index}: {label}"})
        content.append(image_part(jpeg))

    client = genai.Client(api_key=key)
    interaction = client.interactions.create(
        model=model_name(model),
        input=content,
        store=False,
        service_tier=os.environ.get("NOTICEBOT_GEMINI_SERVICE_TIER", "priority"),
        generation_config={
            "thinking_level": os.environ.get(
                "NOTICEBOT_GEMINI_THINKING_LEVEL", "minimal"
            ),
            "max_output_tokens": int(max_output_tokens),
        },
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": schema,
        },
    )
    text = interaction.output_text
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Gemini structured response is not a JSON object")
    return value, text
