"""How rich a sweep station is — one rule per provider, in separate functions.

WHY THIS IS NOT ONE SHARED RULE.

`sweep_plan.py` turns the robot to the station that scores highest, so this
number decides where the robot ends up looking. The original rule is

    3 per focus box, 1 per context box

which reads as "prefer the angle with the most relevant stuff in it". It is not
actually that. It counts BOXES, and how many boxes come back is a property of
the model, not of the room. Measured on the same five frames (the 2026-08-04
23:24 sweep, request "Watching for a new person to come"):

    gemini      7 boxes    v0:3  v1:4  v2:4  v3:1  v4:1   -> v1
    anthropic  13 boxes    v0:0  v1:3  v2:7  v3:4  v4:3   -> v2

Anthropic's winning station scores 7, and 4 of those 7 are chair, chair, table
and whiteboard — furniture that has nothing to do with a person arriving. Strip
the context boxes from both and every station ties on 3, so the aim falls
through to station order. The rule was calibrated, implicitly, against one
model's boxing density; a chattier model silently converts it into "aim at the
most furniture".

So the rule belongs with the provider that it was tuned for. Gemini's is kept
here VERBATIM -- same formula, same result on the same input -- because the
Gemini path is the one that has been run and must not move. The dispatch is the
only thing that chooses between them.

    CONTEXT_CAP is the whole of the second rule: context boxes together
    contribute at most 1, so they can break a tie between two stations but can
    never outvote a focus object.

This also blunts a bad box rather than depending on the model not to produce
one. Anthropic reported a door at view 4 in both runs, where the frame holds a
wall, two light switches and a conduit; under the original rule that phantom is
worth a third of a person, and under the cap it cannot exceed a tie-break.
"""
from __future__ import annotations

from typing import Iterable, Sequence

FOCUS_POINTS = 3
CONTEXT_POINTS = 1
CONTEXT_CAP = 1


def _tiers(boxes: Iterable[Sequence]) -> list[str]:
    """`sweep_plan` holds each box as (label, tier, [x0,y0,x1,y1])."""
    out = []
    for b in boxes:
        if isinstance(b, dict):
            out.append(str(b.get("tier", "context")))
        else:
            out.append(str(b[1]) if len(b) > 1 else "context")
    return out


def score_gemini(boxes: Iterable[Sequence]) -> int:
    """The original rule, unchanged. Do not tune this to fix another provider."""
    return sum(FOCUS_POINTS if t == "focus" else CONTEXT_POINTS
               for t in _tiers(boxes))


def score_capped_context(boxes: Iterable[Sequence]) -> int:
    """Focus counts fully; all context boxes together are worth at most one.

    Identical to `score_gemini` for any station holding 0 or 1 context boxes,
    which is every station of every Gemini sweep recorded so far -- so this is a
    change in what a DENSE boxer does, not a change in the existing behaviour.
    """
    tiers = _tiers(boxes)
    focus = sum(1 for t in tiers if t == "focus")
    context = len(tiers) - focus
    return focus * FOCUS_POINTS + min(context, CONTEXT_CAP) * CONTEXT_POINTS


_RULES = {"gemini": score_gemini, "anthropic": score_capped_context}


def station_score(boxes: Iterable[Sequence], provider: str | None = None) -> int:
    if provider is None:
        from planning.provider import provider_name
        provider = provider_name()
    return _RULES.get(provider, score_gemini)(boxes)


def rule_name(provider: str | None = None) -> str:
    if provider is None:
        from planning.provider import provider_name
        provider = provider_name()
    return _RULES.get(provider, score_gemini).__name__
