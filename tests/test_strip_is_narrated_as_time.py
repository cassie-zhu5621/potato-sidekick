"""The panels are narrated as separate frames in time, never as one montage.

Reported 2026-08-08: "why is one person doing two things in a row described as
two people?"

Two faults, and the second is the one that made the first possible.

1. WHETHER THE NARRATOR WAS TOLD IT HAD A SEQUENCE came down to

       multi = "\u2192" in story        # judge.py
       story = " -> ".join(...)     # storyboard.py

   -- U+2192 against an ASCII arrow. False for all 55 multi-panel strips in the
   record, so every one took the other branch, whose text reads "The image is a
   SINGLE frame -- one instant, NOT a sequence."

2. THE MODEL WAS BEING SENT THE COMPOSITED STRIP. One wide image containing the
   same person at several positions. Told it was a single instant, the only
   coherent reading is several people -- but even told otherwise, "how many
   people are in this image" has no right answer for a montage.

So the panels now go as N SEPARATE IMAGES, which is what the storyboard has been
holding all along; the strip is still built and saved, because it is what a
person opens from the feed, but it is not what the model reads. The question
cannot arise in that form.

AND THE ARC IS THE POINT. "Came in" is not the same finding as "came in and sat
down". A one-panel story is already described by the group judge's sentence and
asks nothing further; a longer one is narrated as a continuation of that
sentence, so the card develops instead of restating the trigger.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from planning.judge import ReportabilityTaste, _prompt

STORY = "gazing-at book, hands_on -> approach_depart -> gathering"


def _p(panels, story=STORY, opening=""):
    return " ".join(_prompt("", ReportabilityTaste(), story=story,
                            panels=panels, opening=opening).split())


def test_a_sequence_is_never_called_a_single_instant():
    """THE REGRESSION. This is the sentence that produced the extra people."""
    p = _p(3)
    assert "SINGLE frame" not in p
    assert "one instant" not in p


def test_the_frames_are_described_as_separate_not_composited():
    """The structural half of the fix. A montage has no correct answer to "how
    many people"; N images does not raise the question."""
    p = _p(3)
    assert "SEPARATE FRAMES" in p
    assert "side by side" not in p and "comic strip" not in p.lower()


def test_a_repeated_person_is_named_as_one_person():
    p = _p(3)
    assert "THE SAME PERSON later, not another person" in p
    assert "Count people WITHIN a frame, never across them" in p


def test_the_frame_count_is_told_not_implied():
    assert "3 SEPARATE FRAMES" in _p(3)
    assert "5 SEPARATE FRAMES" in _p(5)


def test_the_first_frame_is_named_as_the_trigger():
    """Without this the model weights all frames equally and describes whichever
    is most visually striking -- which is how a strip came to be summarised by
    its sharpest panel."""
    p = _p(3)
    assert "first frame is the moment that was noticed" in p
    assert "the rest are WHAT HAPPENED NEXT" in p


def test_the_opening_sentence_is_continued_not_repeated():
    """The arc. Given what was already said about the trigger, the card should
    add what followed rather than restate it."""
    p = _p(3, opening="A person walks in through the door.")
    assert "A person walks in through the door." in p
    assert "Do not restate it" in p
    assert "CONTINUE from there" in p


def test_no_opening_still_narrates():
    """The judge may have been slow or offline. The frames still tell a story."""
    p = _p(3)
    assert "Recount what happened across the frames" in p
    assert "CONTINUE from there" not in p


def test_one_panel_is_still_one_instant():
    """A single-panel story must not be narrated with a before and an after that
    no frame shows."""
    p = _p(1)
    assert "SINGLE frame" in p and "one instant" in p
    assert "SEPARATE FRAMES" not in p


def test_the_ascii_arrow_is_accepted_as_a_fallback():
    """`panels` is authoritative, but a caller that has not been updated must not
    silently fall back to the branch that caused this."""
    p = " ".join(_prompt("", ReportabilityTaste(), story=STORY).split())
    assert "SINGLE frame" not in p


# ------------------------------------------------------- what the caller does --
def test_a_one_panel_story_asks_the_model_nothing():
    """It is already described, by a call that read five separate frames with the
    request in hand. There is no arc to add."""
    import inspect
    from session import storyboard as SB
    src = inspect.getsource(SB.Storyboard._finalize)
    i = src.index("if n <= 1:")
    assert "note = describe or note" in src[i:i + 120]
    assert "run_judge" not in src[i:src.index("elif", i)]


def test_a_longer_story_sends_the_panels_not_the_strip():
    import inspect
    from session import storyboard as SB
    src = inspect.getsource(SB.Storyboard._finalize)
    i = src.index("elif not self.offline:")
    branch = src[i:i + 500]
    assert 'for f in b["shots"]' in branch, "the composited strip is being sent again"
    assert "opening=describe" in branch, "the arc does not continue the trigger"


def test_the_strip_is_still_built_and_saved():
    """It is what a person opens from the feed. Only the model stopped reading it."""
    import inspect
    from session import storyboard as SB
    src = inspect.getsource(SB.Storyboard._finalize)
    assert "make_strip(" in src
    assert "publish(strip," in src
