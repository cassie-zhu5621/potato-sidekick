"""What the detector said is what the plan is matched against.

Found 2026-08-08 by a screen full of

    [cv] 1 person(s), but the detector does not see cell phone -- it sees phone

against a plan whose every field said "phone". Two separate faults, and the
second is the one that mattered.

1. `why_not` read `_expand`'s output as a conjunction. `_expand` returns every
   label that WOULD SATISFY a noun -- an OR -- and had added COCO's "cell phone"
   beside "phone". Demanding both reported a synonym as missing. The damage was
   not the wrong line but that the wrong line RETURNED: the checks below it never
   ran, so the real blocker was never named. A diagnostic that fails this way is
   worse than none, because it is believed.

2. THE SYNONYM TABLE WAS BEING APPLIED AT THE WRONG END. Widening what a label
   MATCHES buys nothing on its own -- it compares against words the detector was
   never asked for and so cannot say. Widening what the detector is ASKED FOR
   buys frames: an open-vocab model finds only what it is prompted for, and a
   phone in a hand is small and half-occluded, so every extra name is another
   chance to see it.

   But a wide prompt has to be undone, or it breaks two things that key on the
   exact string: `_focus_ok` refuses a "smartphone" box against `on: "phone"`,
   and the hands-on clock -- keyed (pid, label) -- becomes two clocks that each
   restart, so a second of contact never accumulates on either.

So: wide at the prompt, canonical at the door, exact everywhere after. Which
also subsumes the COCO case, since a closed detector saying "cell phone" is
renamed by the same step.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from perception.perceive import Detection
from planning.spec_utils import (_canonicalised, _expand, _FilteredDetector,
                                 _focus_ok, canonical, prompt_terms)
from planning.plan_view import PlanView

SPEC = {"watch": [{"all": [9], "on": "phone", "label": "holding phone"}],
        "focus": ["person", "phone"], "detect": ["person", "phone"]}


def _view(spec=SPEC, dets=(), people=1, truth=None, clocks=None, sustain=1.0):
    v = object.__new__(PlanView)
    v.spec = spec
    v.truth = truth or {}
    v.viz = {"dets": [SimpleNamespace(label=d) for d in dets],
             "people": [object()] * people}
    v.relevance = {"focus": _expand(spec.get("focus")), "allow": None}
    v.engine = SimpleNamespace(_touch_since=dict(clocks or {}), sustain_s=sustain)
    return v


# --------------------------------------------------- wide at the prompt ------
def test_the_prompt_asks_for_every_name():
    """The recall half, and the reason the table exists at all."""
    terms = prompt_terms(["phone"])
    assert {"phone", "cell phone", "smartphone", "mobile phone"} <= terms


def test_asking_by_an_alias_also_asks_for_the_rest():
    """A plan that says "smartphone" should not get a narrower prompt than one
    that says "phone" -- they are the same request."""
    assert prompt_terms(["smartphone"]) == prompt_terms(["phone"])


def test_a_category_prompts_for_its_members():
    assert {"backpack", "handbag", "suitcase"} <= prompt_terms(["bag"])


def test_an_unknown_noun_is_asked_for_as_written():
    """Most nouns are in no group, and inventing terms for them would prompt the
    detector for objects the person never mentioned."""
    assert prompt_terms(["whiteboard"]) == {"whiteboard"}


# ------------------------------------------------- canonical at the door -----
def test_every_alias_resolves_to_one_name():
    for alias in ("cell phone", "smartphone", "Mobile Phone", " cellphone "):
        assert canonical(alias) == "phone", alias


def test_an_unknown_label_survives_unchanged():
    assert canonical("whiteboard") == "whiteboard"


def test_a_detection_is_renamed_on_arrival():
    d = _canonicalised(Detection("smartphone", (1, 2, 3, 4), 0.9))
    assert d.label == "phone"
    assert d.box == (1, 2, 3, 4) and d.score == 0.9, "only the name may change"


def test_renaming_does_not_mutate_the_detectors_object():
    """The detector may reuse or cache its results; renaming in place would edit
    something we do not own."""
    orig = Detection("smartphone", (1, 2, 3, 4), 0.9)
    _canonicalised(orig)
    assert orig.label == "smartphone"


def test_nothing_to_rename_costs_nothing():
    orig = Detection("phone", (1, 2, 3, 4), 0.9)
    assert _canonicalised(orig) is orig


def test_the_wrapper_renames_what_it_passes_on():
    """The one place every box in the system goes through."""
    rel = {"allow": None, "want_classes": None, "classes_ver": 0}
    base = SimpleNamespace(detect=lambda img: [Detection("cell phone", (0, 0, 1, 1))])
    assert [d.label for d in _FilteredDetector(base, rel).detect(None)] == ["phone"]


def test_the_whitelist_is_applied_after_renaming():
    """Order matters: filtering first would drop an alias the plan did ask for."""
    rel = {"allow": {"phone"}, "want_classes": None, "classes_ver": 0}
    base = SimpleNamespace(detect=lambda img: [Detection("smartphone", (0, 0, 1, 1))])
    assert len(_FilteredDetector(base, rel).detect(None)) == 1, (
        "a box the plan asked for was dropped because it came back under a "
        "different name -- the wide prompt would then be pure loss")


def test_a_flickering_name_is_one_clock_not_two():
    """The hands-on clock is keyed (pid, label). Two names for one object means
    two clocks that each restart, and 1.0 s never accumulates on either."""
    seen = [Detection("phone", (0, 0, 1, 1)), Detection("smartphone", (0, 0, 1, 1))]
    assert len({_canonicalised(d).label for d in seen}) == 1


# ----------------------------------------------------------- the diagnostic --
def test_a_detected_object_is_not_reported_missing():
    """THE REGRESSION. Plan says 'phone', detector says 'phone'."""
    why = _view(dets=["person", "phone"]).why_not()
    assert why is None or "does not see" not in why, why


def test_a_genuinely_absent_object_is_still_reported():
    why = _view(dets=["person", "laptop"]).why_not()
    assert why and "does not see phone" in why and "laptop" in why


def test_no_people_outranks_everything():
    assert "no pose at all" in _view(dets=[], people=0).why_not()


# ------------------------------------------------- what it says once past it --
def test_a_running_contact_clock_is_reported_as_progress():
    """The instruction to someone acting this out is completely different from
    'move your hand closer', so the two cases must not share a message."""
    import time
    clocks = {(1, "phone"): [time.time() - 0.6, time.time()]}
    why = _view(dets=["person", "phone"], clocks=clocks).why_not()
    assert "HAND is on the phone" in why
    assert "0.6s of the 1.0s" in why, why


def test_no_contact_at_all_names_the_hand():
    why = _view(dets=["person", "phone"]).why_not()
    assert "no HAND POINT is inside any object box" in why
    # Which body part, still -- but the part changed on 2026-08-09. Relation 9
    # tests the wrist AND the index/pinky knuckles now, so telling someone to
    # "hold it so the WRIST overlaps" was instructing them to press their forearm
    # against a plant to satisfy a rule that no longer asks for it.
    assert "index/pinky knuckle" in why


def test_a_satisfied_relation_reports_nothing():
    assert _view(dets=["person", "phone"], truth={9: True}).why_not() is None


def test_a_non_handson_plan_does_not_talk_about_wrists():
    spec = {"watch": [{"all": [1], "on": "whiteboard"}], "focus": ["whiteboard"]}
    why = _view(spec, dets=["person", "whiteboard"]).why_not()
    assert "wrist" not in why and "gaze not landing" in why


# -------------------------------------------------------------- the gate ----
def test_the_gate_accepts_what_the_plan_asked_for():
    entry = {"all": [9], "on": "phone"}
    viz = {"dets": [], "hits": [], "handson": [(1, "phone")]}
    assert _focus_ok(entry, viz, {"phone"})


def test_the_gate_accepts_a_plan_written_in_an_alias():
    """`on` is canonicalised too, so which word the planner reached for stops
    mattering the moment it is compiled."""
    entry = {"all": [9], "on": "cell phone"}
    viz = {"dets": [], "hits": [], "handson": [(1, "phone")]}
    assert _focus_ok(entry, viz, {"phone"}), (
        "a box the detector was prompted for was refused because the plan said "
        "it differently -- this refuses on every frame and logs nothing")


def test_the_gate_does_not_accept_a_word_that_is_not_in_the_group():
    """`book` and `notebook` are the shape of the risk: a planner may list both,
    and declaring them synonymous would take away its ability to distinguish."""
    entry = {"all": [9], "on": "book"}
    viz = {"dets": [], "hits": [], "handson": [(1, "notebook")]}
    assert not _focus_ok(entry, viz, {"book"})


def test_a_category_accepts_its_member_but_not_the_reverse():
    viz = {"dets": [], "hits": [], "handson": [(1, "backpack")]}
    assert _focus_ok({"all": [9], "on": "bag"}, viz, {"bag"})
    viz2 = {"dets": [], "hits": [], "handson": [(1, "handbag")]}
    assert not _focus_ok({"all": [9], "on": "backpack"}, viz2, {"backpack"})


def test_an_unrelated_object_never_passes():
    entry = {"all": [9], "on": "phone"}
    viz = {"dets": [], "hits": [], "handson": [(1, "chair")]}
    assert not _focus_ok(entry, viz, {"phone"})


# ------------------------------------------------------------- the flag -----
def test_the_narrow_prompt_asks_only_what_the_plan_said(monkeypatch):
    """`--no-synonym-prompt`. The wide prompt is an untested claim about this
    rig -- an open-vocab model finds only what it is asked for, so more names
    SHOULD mean more frames, but the failure it would cause instead (slightly
    lower recall on the one object being watched) looks exactly like the object
    being hard to see. Only a run with it off separates them."""
    import planning.plan_view as pv
    for on, expect in ((False, {"person", "phone"}),
                       (True, {"person", "phone", "cell phone", "cellphone",
                               "mobile", "mobile phone", "smartphone"})):
        v = object.__new__(PlanView)
        v.relevance = {"allow": None, "focus": set(),
                       "want_classes": None, "classes_ver": 0}
        v._synonym_prompt = on
        v.apply_relevance(SPEC)
        assert v.relevance["want_classes"] == expect, on


def test_the_flag_never_touches_matching():
    """Only the prompt widens. `allow` and `focus` stay canonical either way, so
    turning the flag off cannot resurrect the gate mismatch it was introduced
    beside."""
    for on in (False, True):
        v = object.__new__(PlanView)
        v.relevance = {"allow": None, "focus": set(),
                       "want_classes": None, "classes_ver": 0}
        v._synonym_prompt = on
        v.apply_relevance(SPEC)
        assert v.relevance["allow"] == {"person", "phone"}
        assert v.relevance["focus"] == {"person", "phone"}
