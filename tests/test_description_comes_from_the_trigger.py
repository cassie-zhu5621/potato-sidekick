"""The sentence is written at the trigger, not from a montage afterwards.

Two VLM calls used to describe the same moment:

  * the GROUP JUDGE reads five SEPARATE frames at the instant the CV gate fired,
    with the participant's request in hand, and answers pass + describe;
  * Storyboard._finalize then made a SECOND call on a composited strip, seconds
    later, and it was that one whose sentence reached the feed card.

The strip is the worse witness of the two, measurably:

  * it is one wide image containing the same person at several positions, so
    "how many people" has no correct answer. Reported 2026-08-08: one person
    doing two things in a row described as two people.
  * it does not carry the request, so the sentence is about whatever is most
    visually salient rather than about what was asked for.
  * it arrives after `linger`, so the card lags the robot's own report.

The judge's sentence has none of those problems and was already paid for -- it
was printed once and discarded. So it now travels with the finding, and the
strip call survives only where there would otherwise be no sentence at all.
"""
from __future__ import annotations

import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import noticebot_loop  # noqa: F401
from session import storyboard as SB


def _finalize_src():
    return inspect.getsource(SB.Storyboard._finalize)


def test_a_story_that_never_developed_asks_nothing_further():
    """One panel means the event was over as soon as it started, and the group
    judge already described exactly that instant from five separate frames with
    the request in hand. A second call could only paraphrase it."""
    src = _finalize_src()
    i = src.index("if n <= 1:")
    assert "note = describe or note" in src[i:i + 120]
    assert "run_judge" not in src[i:src.index("elif", i)]


def test_a_developing_story_is_narrated_from_the_trigger_onward():
    """The arc is the finding: "came in" and "came in and sat down" are
    different reports. The second call continues the judge's sentence rather
    than starting over."""
    src = _finalize_src()
    i = src.index("elif not self.offline:")
    assert "opening=describe" in src[i:i + 500]


def test_the_trigger_sentence_is_the_floor_everywhere():
    """Offline, model error, or a judge that has not landed: the card falls back
    to the sentence written at the trigger, never to the raw relation trace,
    which reads `hands_on -> quiet` and means nothing to a participant."""
    src = _finalize_src()
    assert src.count("describe or note") >= 3


def test_the_sentence_is_cleared_between_tasks():
    """It belongs to the finding it was written for. Left standing, the next
    task's first card wears the last one's words."""
    src = inspect.getsource(SB.Storyboard.reset)
    assert 'self.describe = ""' in src
    assert "self.judge_agreed = None" in src


def test_the_loop_hands_it_over_on_the_normal_path():
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index('candidate_gate["winner"] = winner_label')
    assert "story.describe =" in src[i:i + 500]


def test_a_late_judge_supplies_it_too():
    """`linger` is 6 s, so a verdict that lands after the deadline fired is
    usually still ahead of the story closing. The card should get the good
    sentence even then."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index("story.judge_agreed = agreed")
    assert "if not story.describe:" in src[i:i + 400]


def test_the_late_path_does_not_overwrite_a_sentence_already_there():
    """Two writers, one field. The trigger's sentence is the one that belongs to
    the finding; a late verdict must not replace it."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index("story.judge_agreed = agreed")
    branch = src[i:i + 400]
    assert "if not story.describe:" in branch
