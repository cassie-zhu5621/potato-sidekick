"""A participant names two things in one breath, and the plan has to survive it.

The study asks every participant to pick two of three staged events and speak
them together (S2_EVENT_COVERAGE). Observed 2026-08-09 21:35 on

    "Look at people drawing on the white board. Look at people touching my plants."

the planner wrote the correct pair --

    watch[0]  all=[9]  on='whiteboard'
    watch[1]  all=[9]  on='plant'

-- and `validate` threw the whole plan away: "watch[1]: duplicate entry". The
duplicate key was built from the relation ids alone, so two cards watching the
same ACTION on different THINGS were indistinguishable. Both retries produced the
same correct shape and both failed; the session went to S8, waited out
S8_RECOVER_S, and returned to idle with nothing to watch.

Of the three pairs the card offers, this was the only one that could never
compile -- and it is the pair the sweep aims at best, because both objects sit in
a single station (206 against 103 for either alone).
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from planning.planner import validate


def _spec(*cards):
    return {"watch": list(cards), "seen": ["whiteboard", "potted plant", "person"],
            "boxes": [], "detect": ["whiteboard", "potted plant", "person"],
            "focus": ["whiteboard", "potted plant", "person"],
            "single_ok": [], "duration_s": 600, "why": "x", "missing": None}


def _hands_on(on, label):
    return {"all": [9], "on": on, "within_s": 5, "label": label}


def _dupes(spec):
    return [x for x in validate(spec) if "duplicate entry" in x]


# --------------------------------------------------- the three offered pairs --
def test_plant_and_whiteboard_compiles():
    """THE REPORT. Same relation, different objects -- the pair the aim handles
    best, and the only one that used to be impossible."""
    assert _dupes(_spec(_hands_on("whiteboard", "draw"),
                        _hands_on("plant", "touch"))) == []


def test_plant_and_arrivals_compiles():
    assert _dupes(_spec(_hands_on("plant", "touch"),
                        {"all": [10], "within_s": 5, "label": "arrive"})) == []


def test_all_three_at_once_compiles():
    """They pick two, but nothing should break if a participant names all three
    of their own accord."""
    assert _dupes(_spec(_hands_on("whiteboard", "d"), _hands_on("plant", "p"),
                        {"all": [10], "within_s": 5, "label": "a"})) == []


# ------------------------------------------- what the check still has to catch --
def test_the_same_action_on_the_same_object_is_still_a_duplicate():
    assert _dupes(_spec(_hands_on("plant", "p1"), _hands_on("plant", "p2")))


def test_an_alias_does_not_smuggle_a_duplicate_through():
    """The same object came back as `plant` on one attempt and `potted plant` on
    the next, in this very audit. Comparing raw strings would call those two
    different cards and let a real duplicate stand."""
    assert _dupes(_spec(_hands_on("plant", "p1"), _hands_on("potted plant", "p2")))


def test_two_object_less_cards_are_still_duplicates():
    """`gathering` takes no object, so there is nothing to tell two of them
    apart -- and nothing they could usefully do differently."""
    assert _dupes(_spec({"all": [10], "within_s": 5, "label": "a"},
                        {"all": [10], "within_s": 5, "label": "b"}))


def test_the_label_alone_does_not_make_a_card_distinct():
    """Labels are the model's prose and vary between attempts; two cards that
    differ only in wording are one card."""
    assert _dupes(_spec(_hands_on(None, "somebody handling something"),
                        _hands_on(None, "a person manipulating an object")))
