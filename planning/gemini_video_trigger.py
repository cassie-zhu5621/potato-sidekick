"""Small Gemini Flash smoke test for a video-conditioned feedback trigger.

The user's text is treated as the trigger condition. Gemini examines the whole
video and returns a machine-readable decision plus a short user-facing feedback
message. This remains a whole-video smoke tool; production uses the shared
Gemini provider with independent planning/event images.

Usage:
    python3 planning/gemini_video_trigger.py VIDEO "TRIGGER CONDITION"
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_THINKING_LEVEL = "minimal"
DEFAULT_MEDIA_RESOLUTION = "low"
DEFAULT_MAX_OUTPUT_TOKENS = 256
DEFAULT_SERVICE_TIER = "priority"
SUPPORTED_VIDEO_TYPES = {
    "video/mp4",
    "video/mpeg",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-flv",
    "video/webm",
    "video/x-ms-wmv",
    "video/3gpp",
}

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "feedback_trigger": {
            "type": "boolean",
            "description": "True only when the requested event is clearly supported by the video.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Confidence in the trigger decision from 0 to 1.",
        },
        "evidence_timestamps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Relevant MM:SS timestamps; empty when there is no evidence.",
        },
        "feedback": {
            "type": "string",
            "description": "A brief Chinese message suitable for showing to the user.",
        },
        "reason": {
            "type": "string",
            "description": "A concise Chinese explanation grounded only in the video.",
        },
    },
    "required": [
        "feedback_trigger",
        "confidence",
        "evidence_timestamps",
        "feedback",
        "reason",
    ],
    "additionalProperties": False,
}


def _prompt(trigger_condition: str) -> str:
    return f"""你是一个严格的视频事件触发器。

用户设定的反馈触发条件：{trigger_condition}

请检查视频的视觉和音频内容：
1. 只有视频中有清晰证据表明该条件实际发生时，feedback_trigger 才能为 true。
2. 不要根据常识补全视频中没有出现的内容；不确定时返回 false。
3. evidence_timestamps 使用 MM:SS；没有证据时返回空数组。
4. feedback 和 reason 使用简短中文。feedback 是可以直接展示给用户的一句话。
"""


def _state_name(file_obj: Any) -> str:
    state = getattr(file_obj, "state", None)
    return str(getattr(state, "name", state) or "UNKNOWN").upper()


def _wait_until_active(client: Any, uploaded: Any, timeout_seconds: int) -> Any:
    deadline = time.monotonic() + timeout_seconds
    current = uploaded
    while True:
        state = _state_name(current)
        if state == "ACTIVE":
            return current
        if state == "FAILED":
            raise RuntimeError("Gemini failed to process the uploaded video.")
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Video processing did not finish within {timeout_seconds} seconds."
            )
        print(f"Video processing: {state}", file=sys.stderr)
        time.sleep(2)
        current = client.files.get(name=current.name)


def _validate_result(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Gemini response is not a JSON object.")
    missing = [key for key in RESULT_SCHEMA["required"] if key not in raw]
    if missing:
        raise ValueError(f"Gemini response is missing fields: {', '.join(missing)}")
    if not isinstance(raw["feedback_trigger"], bool):
        raise ValueError("feedback_trigger is not a boolean.")
    if not isinstance(raw["confidence"], (int, float)):
        raise ValueError("confidence is not a number.")
    if not 0 <= float(raw["confidence"]) <= 1:
        raise ValueError("confidence is outside the range 0..1.")
    if not isinstance(raw["evidence_timestamps"], list):
        raise ValueError("evidence_timestamps is not a list.")
    return raw


def analyze_video(
    video_path: Path,
    trigger_condition: str,
    *,
    model: str = DEFAULT_MODEL,
    timeout_seconds: int = 180,
    thinking_level: str = DEFAULT_THINKING_LEVEL,
    media_resolution: str = DEFAULT_MEDIA_RESOLUTION,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    service_tier: str = DEFAULT_SERVICE_TIER,
) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing from .env.")

    video_path = video_path.expanduser().resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not trigger_condition.strip():
        raise ValueError("The trigger condition cannot be empty.")

    mime_type, _ = mimetypes.guess_type(video_path.name)
    if mime_type not in SUPPORTED_VIDEO_TYPES:
        supported = ", ".join(sorted(SUPPORTED_VIDEO_TYPES))
        raise ValueError(f"Unsupported video type {mime_type!r}. Supported: {supported}")

    from google import genai

    client = genai.Client(api_key=api_key)
    uploaded = None
    try:
        print(f"Uploading {video_path.name}...", file=sys.stderr)
        uploaded = client.files.upload(file=str(video_path))
        uploaded = _wait_until_active(client, uploaded, timeout_seconds)
        print(f"Asking {model}...", file=sys.stderr)
        interaction = client.interactions.create(
            model=model,
            input=[
                {
                    "type": "video",
                    "uri": uploaded.uri,
                    "mime_type": uploaded.mime_type,
                    "resolution": media_resolution,
                },
                {"type": "text", "text": _prompt(trigger_condition.strip())},
            ],
            store=False,
            service_tier=service_tier,
            generation_config={
                "thinking_level": thinking_level,
                "max_output_tokens": max_output_tokens,
            },
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": RESULT_SCHEMA,
            },
        )
        return _validate_result(json.loads(interaction.output_text))
    finally:
        if uploaded is not None and getattr(uploaded, "name", None):
            try:
                client.files.delete(name=uploaded.name)
            except Exception as exc:
                print(f"Warning: could not delete uploaded file: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send one video and one text trigger condition to Gemini Flash."
    )
    parser.add_argument("video", type=Path, help="path to a local video file")
    parser.add_argument("text", help="event that should trigger feedback")
    parser.add_argument(
        "--model",
        default=os.environ.get(
            "NOTICEBOT_GEMINI_MODEL",
            os.environ.get("GEMINI_MODEL", DEFAULT_MODEL),
        ),
        help=f"Gemini model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--thinking-level",
        choices=["minimal", "low", "medium", "high"],
        default=os.environ.get("NOTICEBOT_GEMINI_THINKING_LEVEL",
                               DEFAULT_THINKING_LEVEL),
        help=f"thinking level (default: {DEFAULT_THINKING_LEVEL})",
    )
    parser.add_argument(
        "--media-resolution",
        choices=["low", "medium", "high", "ultra_high"],
        default=os.environ.get("NOTICEBOT_GEMINI_MEDIA_RESOLUTION",
                               DEFAULT_MEDIA_RESOLUTION),
        help=f"video token resolution (default: {DEFAULT_MEDIA_RESOLUTION})",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=int(os.environ.get("NOTICEBOT_GEMINI_MAX_OUTPUT_TOKENS",
                                   DEFAULT_MAX_OUTPUT_TOKENS)),
        help=f"response token cap (default: {DEFAULT_MAX_OUTPUT_TOKENS})",
    )
    parser.add_argument(
        "--service-tier",
        choices=["priority", "standard", "flex"],
        default=os.environ.get("NOTICEBOT_GEMINI_SERVICE_TIER",
                               DEFAULT_SERVICE_TIER),
        help=f"Gemini service tier (default: {DEFAULT_SERVICE_TIER})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="video processing timeout in seconds (default: 180)",
    )
    args = parser.parse_args()

    try:
        result = analyze_video(
            args.video,
            args.text,
            model=args.model,
            timeout_seconds=args.timeout,
            thinking_level=args.thinking_level,
            media_resolution=args.media_resolution,
            max_output_tokens=args.max_output_tokens,
            service_tier=args.service_tier,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
