"""The group judge: one verdict on the MOMENT, one sentence, nothing else.

Cut on 2026-08-05 after judge calls ran 40-278 s against a planner at 5-17 s on
the same model, the same five 1280x720 frames, a LONGER prompt and a DEEPER
schema -- so the difference was what the judge was being ASKED to do. Replaying
the ten slowest recorded calls through the new prompt: median 142.2 s -> 2.3 s,
same images, same cards, same provider.

Dropped, and why:

  - four 0-1 axes, read ZERO times downstream. `continuity` ("a follow-up on
    something noticed before") was unanswerable as well as unused: nothing about
    what was noticed before is in the request.
  - per-card verification of CV's geometric claim, plus a three-criteria ranking
    to name one winner. The subject is now the five frames, not the cards.
  - `note` and `feedback`, two registers of one sentence.

Verification was given up on purpose. Over 42 recorded calls it caught ONE case
(CV claimed "gazing at AND drinking water"; the person held the bottle without
drinking). It is affordable because THE CARD LABEL NEVER REACHES THE
PARTICIPANT -- they get the spoken sentence, which is written from the images.
"""
import json

import pytest

from planning.judge import (GROUP_JUDGE_SCHEMA, ReportabilityTaste,
                            _group_prompt, judge_candidate_group)

CARDS = [{"index": 0, "label": "reading book", "all": [9]},
         {"index": 1, "label": "gazing at book", "all": [1]}]
REQ = "Looking at people reading books."


def _flat(p):
    return " ".join(p.split())


def test_the_schema_asks_for_two_things():
    assert set(GROUP_JUDGE_SCHEMA["required"]) == {"pass", "describe"}
    assert set(GROUP_JUDGE_SCHEMA["properties"]) == {"pass", "describe"}


def test_the_request_frames_the_hint_rather_than_trailing_it():
    p = _group_prompt(CARDS, ReportabilityTaste(), REQ)
    assert REQ in p
    assert p.index(REQ) < p.index("Motion detection thinks it saw")


def test_the_cards_are_a_hint_not_a_claim_to_check():
    """Five frames of a room with no hint is an invitation to describe the wrong
    corner -- but the labels must not be presented as facts to adjudicate."""
    p = _flat(_group_prompt(CARDS, ReportabilityTaste(), REQ))
    assert "reading book, gazing at book" in p
    assert "hint about where to look, not a claim to check" in p


def test_a_standing_state_satisfies_a_standing_request():
    """"people reading books" is satisfied by someone reading; it does not have
    to begin while the camera watches. An earlier wording ("false if nothing
    much happens, if it is routine") failed exactly that case on real frames."""
    p = _flat(_group_prompt(CARDS, ReportabilityTaste(), REQ))
    assert "A standing state counts" in p
    assert "does not have to start or change" in p


def test_the_removed_asks_are_gone():
    p = _flat(_group_prompt(CARDS, ReportabilityTaste(), REQ))
    for gone in ("continuity", "consequence", "axes", "selected_index", "0-1"):
        assert gone not in p, f"{gone!r} is still being asked for"


def test_the_reply_is_english_whatever_the_request_was():
    p = _flat(_group_prompt(CARDS, ReportabilityTaste(), REQ))
    assert "English" in p


def test_no_request_leaves_the_prompt_clean():
    p = _group_prompt(CARDS, ReportabilityTaste(), "")
    # The header, not the phrase: the `pass` rule itself says "what they asked
    # for", so a bare substring match reports a request that is not there.
    assert 'THEY ASKED FOR: "' not in p
    assert _group_prompt(CARDS, ReportabilityTaste(), "   ") == p


def test_request_and_learned_taste_stay_separate():
    t = ReportabilityTaste()
    t.about = "the robotics corner"
    p = _group_prompt(CARDS, t, REQ)
    assert "the robotics corner" in p and REQ in p
    assert t.about == "the robotics corner", "the request must not overwrite the lean"


# --------------------------------------------------------------------------- #
def _run(monkeypatch, ok, entries, describe="A person is reading."):
    import planning.judge as J
    monkeypatch.setattr(J, "call_json",
                        lambda *a, **k: ({"pass": ok, "describe": describe}, "{}"))
    return judge_candidate_group([b"x"] * 5, entries, ReportabilityTaste())


def test_one_verdict_covers_every_card(monkeypatch):
    """The model judged the MOMENT, so each card that pointed at it inherits
    that verdict -- the web UI still shows a row per card."""
    out = _run(monkeypatch, True, [{"all": [9]}, {"all": [1]}])
    assert out["confirmed"] is True
    assert [r["confirmed"] for r in out["candidate_results"]] == [True, True]


def test_a_failed_moment_confirms_nothing(monkeypatch):
    out = _run(monkeypatch, False, [{"all": [9]}, {"all": [1]}], describe="")
    assert out["confirmed"] is False and out["selected_index"] == -1
    assert [r["confirmed"] for r in out["candidate_results"]] == [False, False]


@pytest.mark.parametrize("entries,want,label", [
    ([{"all": [9]}, {"all": [1, 9]}], 1, "more relation ids says more"),
    ([{"all": [1, 9]}, {"all": [9]}], 0, "and does not depend on position"),
    ([{"all": [9]}, {"all": [3]}], 0, "equally specific -> CV's own order"),
    ([{"all": [9]}, {"any": [1, 3], "then": [9]}], 1,
     "any/then count too -- reading only `all` scored every card 0"),
])
def test_which_card_labels_the_story(monkeypatch, entries, want, label):
    """Bookkeeping only: the model is not asked, because nothing it could say
    here would reach the person."""
    assert _run(monkeypatch, True, entries)["selected_index"] == want, label


def test_the_return_shape_is_unchanged_for_callers(monkeypatch):
    out = _run(monkeypatch, True, [{"all": [9]}])
    assert set(out) == {"worth", "why", "note", "feedback", "axes",
                        "confirmed", "selected_index", "candidate_results"}
    assert out["note"] == out["feedback"] == "A person is reading."
