"""The strip is a record of the room, not of the head that carries the camera.

Reported 2026-08-08: "even when I do not move at all, every story is 3 shots".

The keyframe test was not broken. It asks whether the picture changed --

    changed = truth-vector differs, OR mean |pixel diff| > scene_diff

-- and the camera is mounted ON THE HEAD, while a story opens at the instant S7
starts swinging it. A head turn changes every pixel, so `changed` was true at
every interval no matter how still the room was, and the count stopped meaning
anything about the event.

The saved strip that settled it (e2e_20260805_155419): panel 1 blurred mid-turn,
panel 2 sharp with the participant facing the lens -- because the robot had just
turned to face her -- panel 3 a wall with her shoulder at the edge. The narration
was written from panel 2: "A woman is sitting in an orange chair looking towards
the camera." That describes her REACTING TO THE ROBOT, which is the one thing the
feed is not for.

So the storyboard now takes frames under the same stillness gate perception has
used all along. Two things follow, and the second is the one worth guarding:

  * a still room yields ONE panel again, which is the designed behaviour -- an
    instantaneous event is one panel, a long one is up to burst_n.
  * `ctxd["frame"]` is NOT gated with it. That is the frame handed to a finding,
    and a finding may be reported at any moment; withholding it while the head
    moved would leave a story with no opening panel at all.
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import noticebot_loop  # noqa: F401  -- loop body read as source
import session.storyboard as SB


class _Stat:
    def __init__(self, v):
        self.satisfied = v


def _play(moving_until, sat_for, gate, hz=4.0, seconds=60, room_moves=False):
    """Run a real Storyboard through a finding whose first seconds are spent
    with the head in motion. -> number of panels in the finished strip."""
    clock = [1000.0]
    real, SB.time.time = SB.time.time, lambda: clock[0]
    published = []
    try:
        sb = SB.Storyboard(feed_dir="/tmp/sb_test", offline=True)
        sb._finalize = lambda b: published.append(len(b["shots"]))
        room = np.full((360, 640, 3), 120, np.uint8)
        sb.open({"label": "x", "all": [9]}, room, {9: True}, {}, 0)
        rng = np.random.default_rng(1)
        for k in range(1, int(seconds * hz)):
            clock[0] = 1000.0 + k / hz
            elapsed = clock[0] - 1000.0
            moving = elapsed < moving_until
            if moving:
                # a head turn: every pixel different from the last frame
                frame = np.full((360, 640, 3), 40 + int(elapsed * 30) % 200, np.uint8)
            else:
                # the room, still, with sensor noise well under scene_diff
                frame = np.clip(room.astype(int) + rng.integers(-3, 4, room.shape),
                                0, 255).astype(np.uint8)
                if room_moves:
                    # something in the room actually moves: a block crossing
                    # the frame. The head is not moving, so this is the change
                    # the keyframe test is supposed to be sensitive to.
                    x = int((elapsed * 90) % 500)
                    frame[80:280, x:x + 140] = 20
            if gate and moving:
                continue                      # the settled gate
            # truth is the LIVE relation; the status is the latched
            # within_s-windowed one. They are different, and the storyboard now
            # asks the first -- see test_the_floor_is_not_set_by_within_s.
            sb.step(frame, {9: elapsed < sat_for}, {},
                    [_Stat(elapsed < sat_for + 5.0)])
            if published:
                break
        return published[0] if published else None
    finally:
        SB.time.time = real


def test_a_still_room_is_two_panels_not_a_swing():
    """THE REPORT. The head swings from the moment the story opens; nothing else
    in the room moves.

    TWO, not one: the panel at `open`, and one more the moment the head settles
    -- `next` was overdue while the gate was skipping frames, so the first still
    frame is taken immediately. That second panel is the room after the robot
    stopped turning, which is exactly the follow-through the strip is for. What
    matters is that neither is a photograph of the swing, and that the count is
    not the four-plus that the swing itself used to generate."""
    assert _play(moving_until=4.6, sat_for=2.0, gate=True) == 2


def test_without_the_gate_the_robots_own_motion_adds_panels():
    """The symptom, reproduced -- and the proof that the panels were counting
    the robot's own motion rather than anything in the room. The same scene that
    yields one panel through the gate yields more without it, and every extra
    one is a photograph of the head turning."""
    assert _play(moving_until=4.6, sat_for=2.0, gate=False) > 1


def test_a_room_that_keeps_changing_still_gets_its_panels():
    """The gate must not turn the storyboard off. With the head still and the
    scene genuinely changing, panels accumulate as before."""
    n = _play(moving_until=0.0, sat_for=40.0, gate=True, seconds=60,
              room_moves=True)
    assert n is not None and n > 1, n


def test_the_loop_gates_the_storyboard_on_the_same_flag_as_perception():
    """One definition of 'the head is still'. A second one drifts, and the two
    layers then disagree about which frames were real."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index("story.step(frame,")
    window = src[i - 400:i]
    assert "and settled" in window, (
        "the storyboard is being fed frames grabbed mid-turn again -- see this "
        "module's docstring for what that does to the strip and the narration")
    # the same flag perception uses, not a private recomputation
    assert 'settled = snap["settled_ms"] > SETTLE_MS' in src


def test_the_finding_frame_is_not_gated():
    """`ctxd["frame"]` is what a finding is recorded with, and a finding can be
    reported while the head is moving. Gating it too would leave a story with no
    opening panel."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index('ctxd["frame"] = frame\n')
    line_start = src.rfind("if ", 0, i)
    assert "settled" not in src[line_start:i], (
        "the finding's own frame is now withheld while the head moves, so a "
        "story opened during S7 has nothing to open with")


# ---------------------------------------------- what sets the story's length --
def test_the_floor_is_not_set_by_within_s():
    """Reported 2026-08-08: "I tried taking my hand away straight away and it is
    still a 3-shot story."

    `ends_at` used to be pushed while `EntryStatus.satisfied`, which means "this
    entry's condition was met within the last `within_s` seconds". That is the
    COMPOSITION window -- it exists so `all: [1, 9]` need not have both
    relations true on the same frame -- and it is latched by design. Read as
    "the event is still happening", it kept every story alive for within_s after
    the room went quiet: with the planner's usual within_s=5 and linger=6, a
    floor of 11 s on every story, panels at 0, 4 and 8. A hand placed and
    withdrawn produced the same three panels as a minute of activity.

    The storyboard now asks the live truth vector instead, so the strip's length
    is the event's length again.
    """
    brief = _play(moving_until=0.0, sat_for=0.5, gate=True, room_moves=True)
    long_ = _play(moving_until=0.0, sat_for=20.0, gate=True, room_moves=True)
    assert brief is not None and long_ is not None
    assert brief < 3, f"a 0.5 s event still yields {brief} panels"
    assert long_ > brief, (
        f"duration no longer changes the strip at all ({brief} vs {long_})")


def test_a_manual_finding_still_closes():
    """A researcher-forced finding has no spec entry, so it has no relation ids
    to ask about. It must close on `linger` rather than hang open forever."""
    import numpy as np
    clock = [1000.0]
    real, SB.time.time = SB.time.time, lambda: clock[0]
    published = []
    try:
        sb = SB.Storyboard(feed_dir="/tmp/sb_test", offline=True)
        sb._finalize = lambda b: published.append(len(b["shots"]))
        room = np.full((360, 640, 3), 120, np.uint8)
        sb.open({"label": "noticed #1 (researcher)"}, room, {}, {}, -1)
        for k in range(1, 200):
            clock[0] = 1000.0 + k / 4.0
            sb.step(room, {}, {}, [])
            if published:
                break
        assert published, "a manual finding never closed"
    finally:
        SB.time.time = real
