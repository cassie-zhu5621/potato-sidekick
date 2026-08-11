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


ANCHOR_WEIGHT = 100      # dominates any plausible box count -- see station_score


def _labels(boxes: Iterable[Sequence]) -> list[str]:
    out = []
    for b in boxes:
        if isinstance(b, dict):
            out.append(str(b.get("label", "")).lower())
        else:
            out.append(str(b[0]).lower() if len(b) else "")
    return out


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


def _anchor_hits(boxes: Iterable[Sequence], anchors) -> int:
    """How many boxes here are the object some card is actually anchored to."""
    from planning.spec_utils import canonical
    want = {canonical(a) for a in anchors if str(a).strip()}
    if not want:
        return 0
    n = 0
    for lab in _labels(boxes):
        c = canonical(lab)
        # containment as well as equality: a card anchored to `plant` must be
        # satisfied by a box the planner called `potted plant`.
        if c in want or any(w in c or c in w for w in want):
            n += 1
    return n


def station_score(boxes: Iterable[Sequence], provider: str | None = None,
                  anchors=()) -> int:
    """-> how much this station is worth as a place to sit and watch.

    THE ANCHOR OUTRANKS EVERYTHING, and it has to, because `person` is a focus
    label in every plan and person boxes are the easiest ones a model returns.
    Recorded 2026-08-08, three sweeps in a row for "tell me if someone touches my
    plant":

        e2e_214220   view0 [person] 3 · view3 [PLANT] 3 · view4 [person] 3
                     -> three-way tie, falls through to view order -> view0
        e2e_213944   view1 [person, person] 6 · view2 [PLANT] 3 · view3 [PLANT] 3
                     -> two people outscore the plant outright -> view1
        e2e_211320   view0 [] 1 · view3 [PLANT] 3 · view4 [PLANT] 3   -> correct

    Twice out of three the robot settled on an angle with no plant in it and spent
    the session there. The brief was about a plant.

    WHY THE ANCHOR AND NOT THE PEOPLE. Every one of these relations needs a person
    too, so aiming at people is not absurd on its face. But only one of the two
    moves: a person will walk over to the plant, and the plant will never walk over
    to the person. Aiming at the fixed half is therefore strictly better -- it is
    the half that cannot come to you.

    The weight is 100 rather than a tuple so that both provider rules keep working
    underneath unchanged; no station will ever hold 33 focus boxes. A plan with no
    `on:` anywhere -- an arrivals card is `gathering(10)` with no object -- passes
    `anchors=()` and gets exactly the old number.

    Tier is deliberately ignored when counting anchors. The planner has called the
    same potted plant `focus` in one sweep and `context` in another; that is its
    opinion about salience, whereas the card naming the object is the person's
    instruction, and the instruction wins.
    """
    if provider is None:
        from planning.provider import provider_name
        provider = provider_name()
    base = _RULES.get(provider, score_gemini)(boxes)
    return ANCHOR_WEIGHT * _anchor_hits(boxes, anchors) + base


def spec_anchors(spec) -> list:
    """The objects the plan's cards are anchored to -> what to aim at."""
    return [e["on"] for e in ((spec or {}).get("watch") or [])
            if isinstance(e.get("on"), str) and e["on"].strip()]


def rule_name(provider: str | None = None) -> str:
    if provider is None:
        from planning.provider import provider_name
        provider = provider_name()
    return _RULES.get(provider, score_gemini).__name__
