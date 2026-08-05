"""Which LLM answers — chosen at runtime, from one place.

`planner.py` and `judge.py` used to import `call_json` straight from
`gemini_provider`. That was fine while there was one provider and became a
problem the moment there were two: the choice would have had to be made at three
call sites, and three copies of a decision drift.

The seam already existed and is unchanged:

    call_json(prompt, schema, *, images, labels, model, max_output_tokens)
        -> (parsed_dict, raw_text)
    warm() -> None

Any provider that honours those two is interchangeable. Nothing above this line
knows which one is answering, which is the point: the prompts, the schemas, the
validation and the audit records are identical across providers, so the two can
be compared on the same session rather than on two different pipelines.

    NOTICEBOT_LLM_PROVIDER=gemini      (default -- current behaviour, untouched)
    NOTICEBOT_LLM_PROVIDER=anthropic

DEFAULTING TO GEMINI IS DELIBERATE. Anyone who does not set the variable -- the
other machine, the other branch, a fresh clone -- gets exactly what they had.
Adding a provider must not be able to change the one already in use.

`--offline` is orthogonal and lives one level up, in planner.plan(): it answers
without any provider at all.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_CACHE: dict[str, Any] = {}
_ENV_LOADED = False


def _ensure_env() -> None:
    """Read .env HERE, before the selector variable is looked up.

    Both providers call `load_dotenv` inside `call_json`, which is too late for
    this one variable and cannot be otherwise: the variable decides which module
    to import, and that module's `call_json` is what would have loaded it. Left
    alone, `NOTICEBOT_LLM_PROVIDER=anthropic` in .env selects GEMINI --
    `provider_name()` runs first, at `planner.py` import time, and sees nothing.

    The second-order version is worse than the first. The eventual `load_dotenv`
    inside the Gemini call does put the variable into os.environ, so a later
    `provider_name()` -- judge.py's, imported after the first plan -- answers
    'anthropic'. One run, two models, and nothing in the log says so.

    load_dotenv does not overwrite variables already in the environment, so
    `NOTICEBOT_LLM_PROVIDER=gemini python3 noticebot_loop.py ...` still wins over
    the file. That is the intended way to switch for a single run.
    """
    global _ENV_LOADED
    if not _ENV_LOADED:
        _ENV_LOADED = True
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        except Exception:
            pass          # no .env, or no python-dotenv: the default still holds


def provider_name() -> str:
    _ensure_env()
    return os.environ.get("NOTICEBOT_LLM_PROVIDER", "gemini").strip().lower()


def _impl():
    """Import lazily and once. Lazily because importing a provider pulls its SDK,
    and the unused one's SDK need not be installed at all."""
    name = provider_name()
    if name not in _CACHE:
        if name in ("anthropic", "claude"):
            from planning import anthropic_provider as mod
        elif name in ("gemini", "google", ""):
            from planning import gemini_provider as mod
        else:
            raise RuntimeError(
                f"NOTICEBOT_LLM_PROVIDER={name!r} is not a provider "
                f"(use 'gemini' or 'anthropic')")
        _CACHE[name] = mod
    return _CACHE[name]


def call_json(*args, **kwargs):
    return _impl().call_json(*args, **kwargs)


def warm(*args, **kwargs):
    return _impl().warm(*args, **kwargs)


def model_name(override: str | None = None) -> str:
    return _impl().model_name(override)


DEFAULT_MODEL_ATTR = "DEFAULT_MODEL"


def __getattr__(name):
    # DEFAULT_MODEL is read at import time by planner.py, so it has to resolve
    # through the active provider rather than being bound to one at module load.
    if name == "DEFAULT_MODEL":
        return getattr(_impl(), "DEFAULT_MODEL")
    raise AttributeError(name)
