"""A reply can be correct and still be packaged wrongly.

The case these guard is real: on 2026-08-05, with a request Whisper had garbled
into "Expecting a prison, drinking, a holding a bottle", the planner returned

    {"watch": "{\"watch\": [...3 good entries...], \"seen\": [...], ...}"}

-- the whole spec JSON-encoded a second time inside one field. Both the first
attempt and the retry did it, so it is a stable response to a confusing prompt.
The session went to S8 over a reply that contained a working plan.
"""
import json

import pytest

from planning.planner import unwrap_double_encoded, validate

INNER = {
    "watch": [
        {"all": [9], "on": "bottle", "within_s": 4, "label": "holding bottle"},
        {"all": [3], "within_s": 5, "label": "eye contact"},
    ],
    "seen": ["person", "bottle"], "detect": ["person", "bottle"],
    "focus": ["person"], "boxes": [], "single_ok": [], "duration_s": 600,
    "why": "x", "missing": None,
}


def test_the_whole_spec_encoded_into_watch_is_recovered():
    broken = {"watch": json.dumps(INNER)}
    assert validate(broken), "must not pass validation unrepaired"
    fixed = unwrap_double_encoded(broken)
    assert validate(fixed) == []
    assert [e["label"] for e in fixed["watch"]] == ["holding bottle", "eye contact"]
    # the rest of the spec rides along inside the same string and must survive:
    # detect/focus are the detector's vocabulary, so losing them would leave the
    # robot watching for relations about objects it was never told to look for.
    assert fixed["detect"] == ["person", "bottle"]
    assert fixed["focus"] == ["person"]


def test_a_bare_encoded_list_is_recovered_too():
    fixed = unwrap_double_encoded({"watch": json.dumps(INNER["watch"]), "seen": []})
    assert isinstance(fixed["watch"], list) and len(fixed["watch"]) == 2


@pytest.mark.parametrize("spec", [
    {"watch": []},                          # genuinely nothing to watch
    {},                                     # genuinely missing
    {"watch": "nope"},                      # not JSON
    {"watch": json.dumps({"seen": [1]})},   # JSON, but no watch inside
    {"watch": json.dumps("just a string")},
])
def test_it_does_not_invent_a_plan(spec):
    """A real failure must still reach S8. The repair is for packaging only."""
    assert validate(unwrap_double_encoded(spec)), spec


def test_a_valid_spec_is_untouched():
    ok = dict(INNER)
    assert unwrap_double_encoded(ok) is ok


def test_the_violation_says_what_it_found():
    """`watch: missing or empty` was reported for a 2 kB string -- neither
    missing nor empty -- which pointed the next reader at the model's judgement
    instead of its packaging."""
    assert validate({"watch": []}) == ["watch: empty list"]
    assert validate({}) == ["watch: missing"]
    msg = validate({"watch": "{...}"})[0]
    assert "got str" in msg and "expected a list" in msg
