"""`on` has to name something a detector can find, and an arrival names nobody.

Observed 2026-08-08 from the dry run. "Look at people coming to the room."
compiled to

    watch:  [{"all": [7], "on": "door", "label": "person entering room"}]
    detect: ["person", "door"]
    why:    "...translates to a person approaching the doorway"

and could not fire on any frame, ever. Two independent blocks, either fatal:

  * NO DETECTOR RETURNS A DOOR. A doorway is an opening -- an absence in a wall,
    with no boundary to box and no characteristic appearance. `[cv] does not see
    door -- it sees no objects`, for the whole run.
  * `on` HANGS THE ENTRY ON THAT BOX. `_focus_ok` requires the thing approached
    to be in {door}; a door was never detected, so nobody could have approached
    one, so `targets & want` was empty every frame. Silent, permanent refusal.

And the deeper error is the translation itself: PEOPLE ARRIVING IS NOT AN OBJECT
EVENT. It is a change in who is present, which is gathering(10) and needs no
object at all. Reaching for a door to stand in for a doorway converts a question
about people into a question about a thing that cannot be seen.

These assert the prompt still carries both halves. Whether the model obeys is
checked by running it -- and was, on five requests, before this file was written.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from planning.planner import build_prompt


def _flat(s):
    return " ".join(s.split())


def _p(ctx="Look at people coming to the room."):
    return _flat(build_prompt(ctx))


def test_the_undetectable_nouns_are_listed_by_name():
    """A rule stated only in the abstract ("something detectable") leaves the
    model to decide whether a door qualifies, and it decided wrong."""
    p = _p()
    for word in ("door", "doorway", "entrance", "room", "corner", "wall",
                 "hallway", "background"):
        assert word in p, f"{word!r} is not named as undetectable"


def test_it_says_what_they_have_in_common():
    """So the list generalises rather than being a blocklist to route around."""
    p = _p()
    assert "OPENINGS" in p or "openings" in p
    assert "PUT A BOX AROUND" in p


def test_the_failure_mode_is_stated():
    """The cost is invisible -- refused on every frame with nothing logged --
    which is exactly the kind a model will not infer from 'prefer X'."""
    p = _p()
    assert "refused on every frame" in p


def test_an_arrival_is_routed_to_gathering():
    p = _p()
    assert "AN ARRIVAL IS NOT AN OBJECT EVENT" in p
    assert "gathering(10)" in p and "takes no" in p


def test_approach_is_not_banned_outright():
    """It is right for arriving at something real. A rule that removed
    approach(7) would break "someone comes to my desk"."""
    p = _p()
    assert "approach(7) is for arriving AT SOMETHING DETECTABLE" in p
    assert "approach(7) on the" in p          # named, with its object


def test_but_a_bare_approach_is_forbidden():
    """The permission this file used to assert -- "may also be used bare" -- was
    written here by me and was wrong: `_focus_ok` treats 7 as object-directed, so
    a 7 with no `on` is checked against every focus object at once and matches
    none. The card looks correct in the plan and is suppressed on every frame.

    Observed 2026-08-09 on "Looking at people coming to the room": the planner
    took the permission, and `[gate] comes to room suppressed` repeated until the
    run was killed. planner.repair_bare_approach is the floor under this text."""
    p = _p()
    assert 'NEVER write approach(7) with no "on"' in p
    assert "the moment you mean is gathering(10)" in p


def test_the_desk_and_the_room_are_contrasted_where_the_lure_is():
    """The few-shot line "'comes to my desk' is approach(7)" sits forty-eight
    lines above the arrival rule, and "coming to the room" reads like it. The
    contrast has to be at the lure, not only in the rule."""
    p = _p()
    i = p.index("comes to my desk")
    assert "comes INTO THE ROOM" in p[i:i + 260]


def test_the_rule_travels_with_every_request():
    """The planner cannot know in advance which requests describe an arrival."""
    for ctx in ("", "plants", "tell me when someone enters"):
        assert "AN ARRIVAL IS NOT AN OBJECT EVENT" in _flat(build_prompt(ctx))


def test_the_object_grammar_is_still_stated_above_it():
    """The new rule constrains `on`; it has to arrive after the field is
    introduced or it constrains something the reader has not met."""
    p = _p()
    assert p.index('name it in that entry\'s "on" field') < p.index("MUST NAME SOMETHING")
