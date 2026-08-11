"""The robot must sit where the object is, not where the people are.

Reported 2026-08-08: "the sweep clearly saw the plant and it still locked onto a
direction with no plant in it; the plant was only the next best."

`station_score` counted focus boxes at 3 apiece, and `person` is a focus label in
every plan the planner writes -- it has to be, since every relation needs a person.
Person boxes are also the ones a detector returns most readily. So a station with
people in it beat the station holding the thing the brief was about. Three
consecutive sweeps for "tell me if someone touches my plant":

    e2e_214220   pan-60 [person] 3 · pan+30 [PLANT] 3 · pan+60 [person] 3
                 three-way tie -> falls through to sweep order -> pan-60
    e2e_213944   pan-30 [person, person] 6 · pan+0 [PLANT] 3 · pan+30 [PLANT] 3
                 two people outscore one plant outright -> pan-30
    e2e_211320   pan-60 [person] 1 · pan+60 [PLANT] 3 -> pan+60, correct

Twice out of three the robot spent the whole session pointed away from the plant,
and nothing downstream could recover: the camera is the only sensor, and a station
is held until the re-plan.

THE ARGUMENT FOR THE ANCHOR, since aiming at people is not absurd on its face --
these relations do all need a person. Only one of the two moves. A person will
walk over to the plant; the plant will never walk over to the person. Aiming at
the half that cannot come to you is therefore strictly better, and the more people
a station holds the more certain it is that they will move.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from planning.station_score import (ANCHOR_WEIGHT, spec_anchors, station_score)


def _b(*labels, tier="focus"):
    return [(l, tier, [0, 0, 10, 10]) for l in labels]


def _aim(stations, anchors):
    """-> index of the station the robot would settle on."""
    scored = [station_score(b, "gemini", anchors=anchors) for b in stations]
    return max(range(len(scored)), key=lambda i: (scored[i], -i))


# ------------------------------------------------- the three recorded sweeps --
def test_a_tie_no_longer_falls_through_to_sweep_order():
    """e2e_214220. Every station scored 3, so the aim was decided by which angle
    the sweep happened to visit first."""
    assert _aim([_b("person"), [], [], _b("plant"), _b("person")], ["plant"]) == 3


def test_two_people_no_longer_outscore_the_object_asked_about():
    """e2e_213944. The clearest case: 6 against 3, no tie-break involved, the
    rule simply preferred people."""
    assert _aim([[], _b("person", "person"), _b("plant"), _b("plant"), []],
                ["plant"]) == 2


def test_the_sweep_that_was_already_right_is_unchanged():
    """e2e_211320. A fix that only moved the failures would be indistinguishable
    from one that moved everything."""
    assert _aim([_b("person", tier="context"), [], [], [], _b("plant")],
                ["plant"]) == 4


def test_a_dense_station_still_wins_when_it_holds_the_anchor():
    """e2e_203927, the laptop brief: pan-30 held laptop+desk+chair+backpack and
    was already correct. The anchor bonus must not invert a station that was
    right for the ordinary reason."""
    dense = _b("laptop", "desk") + _b("chair", "backpack", tier="context")
    thin = _b("laptop", "desk")
    assert _aim([[], dense, thin, [], _b("person")], ["desk"]) == 1


# ------------------------------------------------------- what does not change --
def test_a_plan_with_no_object_scores_exactly_as_before():
    """An arrivals card is `gathering(10)` with no `on:` at all -- the planner is
    instructed to write it that way because a door is not reliably detectable. Those
    plans must not change behaviour, and they are the ones where aiming at people
    is right."""
    stations = [_b("person", "person"), _b("plant")]
    assert [station_score(b, "gemini") for b in stations] == \
           [station_score(b, "gemini", anchors=[]) for b in stations]
    assert _aim(stations, []) == 0


def test_the_anchor_is_read_off_the_cards_not_guessed():
    spec = {"watch": [{"all": [9], "on": "potted plant"}, {"all": [10]},
                      {"all": [1], "on": "  "}]}
    assert spec_anchors(spec) == ["potted plant"]
    assert spec_anchors({}) == []


def test_the_object_is_matched_loosely_enough_to_be_found():
    """The card says `plant`; the planner boxes it as `potted plant`. Requiring
    string equality would leave the bonus permanently unclaimed, which is a
    failure that looks exactly like no fix at all."""
    assert station_score(_b("potted plant"), "gemini", anchors=["plant"]) >= ANCHOR_WEIGHT
    assert station_score(_b("plant"), "gemini", anchors=["potted plant"]) >= ANCHOR_WEIGHT
    assert station_score(_b("chair"), "gemini", anchors=["plant"]) < ANCHOR_WEIGHT


def test_tier_is_ignored_when_the_card_named_the_object():
    """The planner has called the same potted plant `focus` in one sweep and
    `context` in another. Tier is its opinion about salience; the card naming the
    object is the person's instruction."""
    assert station_score(_b("plant", tier="context"), "gemini",
                         anchors=["plant"]) >= ANCHOR_WEIGHT


def test_the_bonus_cannot_be_reached_by_stacking_ordinary_boxes():
    """If it could, the rule would be a preference rather than a precedence, and
    a crowded doorway would win back."""
    crowd = _b(*["person"] * 12) + _b(*["chair"] * 12, tier="context")
    assert station_score(crowd, "gemini") < ANCHOR_WEIGHT


def test_both_provider_rules_still_apply_underneath():
    """The anchor term is added to whichever rule the provider uses, not in place
    of it -- so among stations that all hold the anchor, the old ordering stands."""
    one = _b("plant")
    two = _b("plant", "person")
    assert station_score(two, "gemini", anchors=["plant"]) > \
           station_score(one, "gemini", anchors=["plant"])
    # and the capped-context rule is still the capped-context rule
    ctx = _b("plant") + _b("chair", "table", "door", tier="context")
    assert station_score(ctx, "anthropic", anchors=["plant"]) == ANCHOR_WEIGHT + 3 + 1
