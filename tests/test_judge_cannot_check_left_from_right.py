"""The judge is shown pixels and no pan angle, so it cannot check "the RIGHT board".

Reported 2026-08-08: "cv got rejected by the vlm again, this time drawing on the
whiteboard". Six rejections in one session, every one of a real event.

The request was "Tell me if someone draws on the right board." The five frames
show exactly that: a person at a whiteboard, arm up, green marker on the surface,
for the whole window. And the room has TWO boards, so the frame contains the one
being used and, further right, an empty one.

WHAT THE PLANNER DID WITH THE WORD. It swept the room, and resolved "right board"
to an object -- `right whiteboard`, view_index 2, box [0.00, 0.00, 0.47, 0.64].
The head pans toward that board, so in that view it occupies the LEFT HALF OF THE
FRAME. The CV gate then required a wrist inside that box, and got one. The word
"right" had been checked, by the one stage that sees more than one view.

WHAT THE JUDGE DID WITH IT. Re-resolved it against the picture, because that is
the only thing in front of it, and answered a different question:

    "A person is drawing on the LEFT whiteboard, while the whiteboard on the
     right wall remains untouched."

Correct about the picture. Wrong about the room. Measured on the saved frames,
two calls each:

    "draws on the right board"                          pass=False, False
    "draws on the board"                                pass=True,  True
    "draws on the right board" + a paragraph saying the head turns, left/right
        mean places in the room, do not fail on them    pass=False, False

THE THIRD ROW IS THE FINDING. Told plainly and at length to disregard the word,
the model still refused, and still narrated the empty board as the one that had
been asked about. So this is not a prompt that needs better wording -- the word
has to be absent. And with it absent the gate is not looser: the same prompt
rejects an empty room, and rejects a person walking up to the board who has not
started drawing ("reaches for a marker, but does not draw").
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from planning import judge as J
from planning.event_frames import EVENT_OFFSETS_S
from planning.judge import ReportabilityTaste
from planning.spec_utils import viewpoint_free


def _p(request="", labels=("drawing on right board",)):
    cands = [{"index": i, "label": l} for i, l in enumerate(labels)]
    return J._group_prompt(cands, ReportabilityTaste(), request)


# ------------------------------------------------------- the word is removed --
def test_the_measured_case():
    assert viewpoint_free("Tell me if someone draws on the right board.") == \
        "Tell me if someone draws on the board."


def test_it_goes_from_the_request_and_from_the_card_label():
    """Both reach the model, and one is as good as the other at re-anchoring it.
    The card label is the likelier survivor of the two, because it is generated
    rather than spoken and nobody reads it on the way past."""
    p = _p("Tell me if someone draws on the right board.")
    assert "right board" not in p
    assert "draws on the board" in p
    assert "drawing on board" in p          # the label, stripped too


def test_trailing_deixis_does_not_leave_a_dangling_preposition():
    """"the person on the left" -- there is no following noun to strip the word
    off, so the whole phrase goes or the sentence ends on "on"."""
    assert viewpoint_free("watch the person on the left") == "watch the person"
    assert viewpoint_free("the cup on the left side of the table") == \
        "the cup on the table"


def test_scene_relations_are_kept():
    """"in front of" and "behind" hold between two things in the room and are
    true from any viewpoint. "in front of" especially: a person standing in front
    of a plant read as touching it is THE false report the judge exists to catch,
    so deleting the phrase would remove the question rather than the ambiguity."""
    for s in ("a cup in front of the plant", "the bag behind the chair",
              "two people facing each other", "tell me when people come in"):
        assert viewpoint_free(s) == s, s


def test_a_request_with_no_deixis_is_untouched_and_unexplained():
    """The note costs tokens and narrows attention. It is only true when
    something was actually removed, and only earns its place then."""
    p = _p("Tell me if someone draws on the board.")
    assert "YOU ARE CHECKING THE ACTION" not in p


def test_the_note_appears_when_something_was_removed():
    p = _p("Tell me if someone draws on the right board.")
    assert "YOU ARE CHECKING THE ACTION, NOT THE LOCATION" in p
    assert "wrong side of the" in p


# --------------------------------------------- the planner keeps every word --
def test_only_the_judge_is_shielded():
    """Stripping earlier would be a different and much worse bug: resolving these
    words IS the planner's job, it is the only stage that sees more than one
    view, and `on:` has to keep naming a specific object or the CV gate has
    nothing to enforce."""
    import inspect
    from planning import planner as P
    assert "viewpoint_free" not in inspect.getsource(P), (
        "the planner is stripping the words it exists to resolve")


# ------------------------------------------------- what the frames are said to be --
def test_the_frame_times_are_read_off_the_offsets():
    """They were typed out once and went stale. The prompt announced
    "t-1.0s, t-0.5s, onset, t+0.5s, t+1.0s" long after the window moved to
    -4.0 .. 0.0, so the judge was told two of its frames were the AFTERMATH of a
    moment that all five predate. Asked what happened next, it had to invent it."""
    p = _p()
    for offset in EVENT_OFFSETS_S:
        assert ("onset" if offset == 0 else f"t{offset:+.1f}s") in p, offset
    assert "t+0.5s" not in p


def test_the_per_image_labels_come_from_the_same_place():
    """Two lists of times in one request is one list too many."""
    labels = J._frame_labels(len(EVENT_OFFSETS_S), event_window=True)
    assert labels[-1] == "t0_onset"
    assert labels[0] == f"t{EVENT_OFFSETS_S[0]:+.1f}s"
    assert J._frame_labels(3, event_window=True) == ["panel_0", "panel_1", "panel_2"]


def test_a_five_panel_strip_is_not_labelled_as_the_event_window():
    """Two different sets of images reach this module: the judge's five
    historical frames, and the storyboard's strip. Keyed on `n == 5` alone -- as
    the hardcoded list it replaced effectively was -- a five-panel story would be
    handed to the narrator labelled "t-4.0s .. onset", i.e. told that up to 45 s
    of room is the four seconds BEFORE the moment it is describing."""
    assert J._frame_labels(5) == [f"panel_{i}" for i in range(5)]


def test_the_storyboard_asks_for_panels():
    """`judge()` is the storyboard's entry point and never sees the event window,
    so it must not opt in."""
    import inspect
    src = inspect.getsource(J.judge)
    assert "_frame_labels(len(images))" in src
    assert "event_window" not in src


def test_it_says_the_window_is_all_in_the_past():
    p = _p()
    assert "BEFORE the moment" in p


# ------------------------------------------------------ a rejection says why --
def test_describe_is_written_even_on_a_rejection():
    """Every one of the six rejections logged "[confirm] group rejected: " with
    nothing after the colon, because the prompt said to leave `describe` empty
    when pass is false. The verdict is in `pass`; the sentence is the only trace
    a rejection leaves, and without it the evening was spent guessing."""
    p = _p("Tell me if someone draws on the board.")
    assert "ALWAYS WRITE IT" in p
    assert "including when pass is false" in p
    assert "Empty if pass is false" not in p
