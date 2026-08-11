"""Every panel in a strip is taken from the same head pose, or not at all.

Reported 2026-08-08: "the storyboard's viewpoint keeps changing, sometimes quite
a lot."

The camera is on the head, so a panel is framed by wherever the neck happens to
be. A story opens while S5b watches at tilt -12 and its later panels are taken
during S7's rests at tilt -22 -- ten degrees, about a third of the vertical
field. The strip cuts between its first panel and all the others.

The existing `settled` gate could not catch this: it asks whether the head has
STOPPED, and during S7b's four-second rest it certainly has. The question is not
whether the head is still but whether it is still THERE, and only the pose
answers that. A pixel difference cannot: a room that changed and a head that
moved look the same.

TWO THINGS FOLLOW FROM SKIPPING, and the second is what makes it work.

Skipping the panels alone would leave the story to expire during the
performance -- `linger` is 6 s and S7a+S7b is about 8 -- so every long event
would be recorded as its opening frame and nothing else. So the clock is held
too: while the shot is wrong, nothing is photographed AND nothing ages. The
follow-through is still there to be caught when the robot returns to watching.

MAX_STORY_S is the ceiling on that. S7b loops until OK or 30 s, so a pause of
half a minute is legitimate; longer means the robot has been re-aimed somewhere
else and the story is about a place it is no longer looking at.
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import noticebot_loop  # noqa: F401
import session.storyboard as SB

WATCH = {"pan": -25.0, "tilt": -12.0, "nod": 12.0}     # S5b
S7 = {"pan": -25.0, "tilt": -22.0, "nod": 12.0}        # S7a/S7b rest
NUDGE = {"pan": -25.0, "tilt": -14.0, "nod": 12.0}     # within tolerance


class _Stat:
    def __init__(self, v):
        self.satisfied = v


def _run(poses, hz=4.0, live_for=999.0):
    """poses: one per tick. -> (panels, seconds lived, closed?).

    A story held off-shot may still be open when the poses run out; the panel
    count is read from the live burst in that case, because "how many panels did
    it take" is the question either way."""
    clock = [1000.0]
    real, SB.time.time = SB.time.time, lambda: clock[0]
    out = []
    try:
        sb = SB.Storyboard(feed_dir="/tmp/sb_shot", offline=True)
        sb._finalize = lambda b: out.append((len(b["shots"]), clock[0] - 1000.0))
        room = np.full((360, 640, 3), 120, np.uint8)
        sb.open({"label": "x", "all": [9]}, room, {9: True}, {}, 0, pose=WATCH)
        rng = np.random.default_rng(4)
        for k, pose in enumerate(poses, start=1):
            clock[0] = 1000.0 + k / hz
            el = clock[0] - 1000.0
            fr = np.clip(room.astype(int) + rng.integers(-3, 4, room.shape),
                         0, 255).astype(np.uint8)
            fr[80:280, int((el * 90) % 460):int((el * 90) % 460) + 150] = 30
            sb.step(fr, {9: el < live_for}, {}, [_Stat(el < live_for)], pose=pose)
            if out:
                break
        if out:
            return out[0][0], out[0][1], True
        live = sb.bursts[0] if sb.bursts else None
        return (len(live["shots"]) if live else None,
                clock[0] - 1000.0, False)
    finally:
        SB.time.time = real


def test_panels_off_the_shot_are_not_taken():
    """THE REPORT. The head drops ten degrees into S7 and stays there; nothing
    is photographed from the new angle."""
    n, _, _ = _run([S7] * 40, live_for=0.0)
    assert n == 1, f"{n} panels, so frames were taken from the S7 pose"


def test_a_small_nudge_is_still_the_same_shot():
    """Servo settling and calibration drift are not a cut. A tolerance tight
    enough to reject those would reject every panel."""
    n, _, _ = _run([NUDGE] * 40, live_for=999.0)
    assert n is not None and n > 1


def test_the_story_does_not_expire_while_the_shot_is_wrong():
    """`linger` is 6 s and S7 runs about 8. Ageing during the performance would
    make every long event a one-panel story."""
    _, lived, closed = _run([S7] * 60, live_for=0.0)
    assert not closed, (
        f"closed after {lived}s -- the clock ran while the camera was elsewhere")


def test_the_follow_through_is_caught_when_the_head_comes_back():
    """The whole reason for holding the clock rather than just skipping."""
    poses = [S7] * 32 + [WATCH] * 40           # 8 s away, then watching again
    n, _, _ = _run(poses, live_for=999.0)
    assert n is not None and n > 1, (
        "nothing was photographed after the robot returned to the shot")


def test_a_head_that_never_comes_back_still_publishes():
    """Held forever, a story would never reach the feed at all."""
    n, lived, closed = _run([S7] * 400, live_for=999.0)
    assert closed, "the story never closed"
    assert lived <= SB.Storyboard.MAX_STORY_S + 1.0, lived


def test_the_cap_outlasts_a_whole_ignored_s7():
    """S7b loops until OK or S7_IGNORED_TIMEOUT_S. A cap shorter than that would
    cut off stories during ordinary, correct behaviour."""
    from robot import states as ST
    assert SB.Storyboard.MAX_STORY_S > ST.S7_IGNORED_TIMEOUT_S + 6.0


def test_no_pose_data_behaves_as_before():
    """The offline stub and any caller that has not been updated must not have
    every panel silently refused."""
    assert SB.Storyboard._same_shot(None, WATCH, 3.0)
    assert SB.Storyboard._same_shot(WATCH, None, 3.0)


def test_the_loop_supplies_the_pose_at_both_ends():
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index("story.open(e, fr, truth, viz, idx")
    assert "pose=" in src[i:i + 200], "the story opens with no shot recorded"
    j = src.index("story.step(frame,")
    assert "pose=snap.get(\"pose_deg\")" in src[j:j + 300]
