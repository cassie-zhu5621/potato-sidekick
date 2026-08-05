"""The station score decides where the robot points, so the Gemini rule has to
be provably the same formula it was before it was moved into its own file.

The first two tests are the load-bearing ones: everything else here can be
wrong and the study still runs on the behaviour it was rehearsed with.
"""
import pytest

from planning.station_score import (
    score_capped_context, score_gemini, station_score, rule_name)


def _old_inline_rule(boxes):
    """Verbatim copy of what sweep_plan.py held before station_score.py existed:

        sc = sum(3 if tier == "focus" else 1 for _, tier, _ in per[i])

    Kept as an independent expression on purpose. If someone edits
    `score_gemini`, this catches it -- which a test that called `score_gemini`
    twice would not.
    """
    return sum(3 if tier == "focus" else 1 for _, tier, _ in boxes)


# The five stations of the recorded 2026-08-04 23:24 sweep, as (label, tier, box)
# triples in the shape sweep_plan builds. Boxes are the real returned values.
GEMINI_SWEEP = {
    0: [("person", "focus", [0, 0, 0.83, 1])],
    1: [("person", "focus", [0.49, 0, 1, 1]),
        ("coffee cup", "context", [0.82, 0.58, 0.99, 0.91])],
    2: [("person", "focus", [0, 0, 1, 1]),
        ("coffee cup", "context", [0.55, 0.49, 0.61, 0.77])],
    3: [("laptop", "context", [0, 0.52, 0.49, 1])],
    4: [("laptop", "context", [0.27, 0.44, 0.96, 1])],
}

ANTHROPIC_SWEEP = {
    0: [],
    1: [("person", "focus", [0, 0, 0.95, 1])],
    2: [("person", "focus", [0.65, 0, 1, 1]),
        ("chair", "context", [0.05, 0.28, 0.35, 0.60]),
        ("chair", "context", [0.70, 0.25, 0.90, 0.55]),
        ("table", "context", [0, 0.30, 0.65, 0.65]),
        ("whiteboard", "context", [0.25, 0.05, 0.60, 0.30])],
    3: [("door", "context", [0.50, 0, 0.65, 0.75]),
        ("chair", "context", [0.55, 0.28, 0.75, 0.50]),
        ("table", "context", [0.60, 0.25, 1, 0.45]),
        ("whiteboard", "context", [0, 0, 0.40, 0.25])],
    4: [("door", "context", [0.80, 0.05, 1, 0.60]),
        ("chair", "context", [0, 0.40, 0.30, 0.75]),
        ("whiteboard", "context", [0, 0, 0.85, 0.25])],
}


def _richest(per, rule):
    """sweep_plan picks with `sc > best[0]`, so a tie goes to the LOWER index."""
    best = (-1, None)
    for i in sorted(per):
        sc = rule(per[i])
        if sc > best[0]:
            best = (sc, i)
    return best[1]


def test_gemini_rule_is_the_old_inline_formula():
    for per in (GEMINI_SWEEP, ANTHROPIC_SWEEP):
        for boxes in per.values():
            assert score_gemini(boxes) == _old_inline_rule(boxes)


def test_the_recorded_gemini_sweep_scores_and_aims_exactly_as_before():
    scores = {i: score_gemini(b) for i, b in GEMINI_SWEEP.items()}
    assert scores == {0: 3, 1: 4, 2: 4, 3: 1, 4: 1}
    assert _richest(GEMINI_SWEEP, score_gemini) == 1


def test_the_cap_does_not_move_the_recorded_gemini_sweep():
    """No Gemini station held more than one context box, so the two rules agree
    on this data. That is the evidence the cap is safe, not an assumption."""
    for i, boxes in GEMINI_SWEEP.items():
        assert score_gemini(boxes) == score_capped_context(boxes), i
    assert _richest(GEMINI_SWEEP, score_capped_context) == 1


def test_furniture_decided_the_anthropic_aim_under_the_old_rule():
    scores = {i: score_gemini(b) for i, b in ANTHROPIC_SWEEP.items()}
    assert scores == {0: 0, 1: 3, 2: 7, 3: 4, 4: 3}
    # v2 beats v3 by furniture count, and v3 -- the station with the door, in a
    # sweep whose request was "watching for a new person to come" -- outscores
    # the station holding an actual person.
    assert scores[3] > scores[1]


def test_the_cap_stops_context_outvoting_a_person():
    scores = {i: score_capped_context(b) for i, b in ANTHROPIC_SWEEP.items()}
    assert scores == {0: 0, 1: 3, 2: 4, 3: 1, 4: 1}
    assert scores[1] > scores[3]
    assert _richest(ANTHROPIC_SWEEP, score_capped_context) == 2


def test_a_phantom_context_box_cannot_outweigh_a_focus_object():
    phantom = [("door", "context", [0.8, 0, 1, 0.6])] * 4
    assert score_capped_context(phantom) < score_capped_context(
        [("person", "focus", [0, 0, 1, 1])])


def test_dispatch_follows_the_provider(monkeypatch):
    monkeypatch.setenv("NOTICEBOT_LLM_PROVIDER", "gemini")
    assert rule_name() == "score_gemini"
    assert station_score(ANTHROPIC_SWEEP[2]) == 7

    monkeypatch.setenv("NOTICEBOT_LLM_PROVIDER", "anthropic")
    assert rule_name() == "score_capped_context"
    assert station_score(ANTHROPIC_SWEEP[2]) == 4


def test_an_unknown_provider_falls_back_to_the_untouched_rule(monkeypatch):
    monkeypatch.delenv("NOTICEBOT_LLM_PROVIDER", raising=False)
    assert rule_name() == "score_gemini"


def test_dict_shaped_boxes_score_too():
    """`provider_ab.py` and plan.json hold boxes as dicts, not triples."""
    assert score_gemini([{"label": "p", "tier": "focus"},
                         {"label": "c", "tier": "context"}]) == 4
