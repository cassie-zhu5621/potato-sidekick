"""The break-time demo loop actually moves.

Written after the first run of it did not. `request()` only records what the
player SHOULD be doing; the clip is fed to the bus by a worker thread that
`start()` spawns, and without that call the M5 screen changed on cue while the
head never moved and no sound played. That failure looks exactly like a
servo-power or wiring fault and is neither, which is why it gets a test rather
than a comment.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import robot.states as ST
from robot.tools import attract


def test_the_player_is_started():
    """The one line whose absence produces a robot that only blinks."""
    src = open(os.path.join(ROOT, "robot", "tools", "attract.py")).read()
    i = src.index("player = ClipPlayer(")
    assert "player.start()" in src[i:i + 900]
    assert src.index("player.start()") < src.index("while True:")


def test_every_beat_names_a_real_state():
    for state, hold, why in attract.BEATS:
        assert state in ST.STATES, state
        assert hold > 0 and why


def test_it_shows_the_whole_vocabulary():
    """A stand should show the grammar, not a favourite part of it."""
    seen = {s for s, _h, _w in attract.BEATS}
    assert seen == set(ST.STATES), sorted(set(ST.STATES) - seen)


def test_error_is_last():
    """It belongs in the loop -- it is part of the vocabulary -- but the stand
    should not spend its most visible seconds looking broken."""
    order = [s for s, _h, _w in attract.BEATS]
    assert order[-1] == "S8_ERROR"
    assert order[0] == "S1_IDLE"


def test_a_beat_waits_for_its_clip_before_holding():
    """`hold` is time ON TOP OF the clip's own length. Sleeping for `hold`
    alone would cut S2's 1.7 s turn off after 1.2 s, and an interrupted gesture
    reads as a twitch rather than as a turn."""
    src = open(os.path.join(ROOT, "robot", "tools", "attract.py")).read()
    i = src.index("player.request(state)")
    block = src[i:i + 700]
    assert "CLIP_S.get(state" in block
    assert "spec_len + hold" in block


def test_clip_lengths_are_measured_not_declared():
    """A hand-kept table of durations goes stale the first time anything is
    re-exported from Blender."""
    lens = attract._clip_seconds(os.path.join(ROOT, "motion", "clips"))
    assert lens["S3_ACK"] > 1.0
    assert lens["S4_PLAN"] > 5.0
    # loop clips have no end of their own and are excluded
    for looping in ("S1_IDLE", "S5B_TRACK", "S8_ERROR"):
        assert looping not in lens, looping


def test_it_says_it_is_not_the_system():
    """A stand performing the whole cycle with nobody touching it invites the
    reading that it is responding to the room. At a research stand that has to
    be corrected before it is made."""
    src = open(os.path.join(ROOT, "robot", "tools", "attract.py")).read()
    assert 'link.event("NAME", "DEMO LOOP")' in src
    assert "NOT the live system" in src
    assert len("DEMO LOOP") <= 16, "the board's name field is ASCII and short"


def test_the_joints_are_released_on_the_way_out():
    """Somebody will pick the robot up to look underneath it."""
    src = open(os.path.join(ROOT, "robot", "tools", "attract.py")).read()
    i = src.index("finally:")
    tail = src[i:]
    assert "player.stop()" in tail
    assert "bus.torque(sid, False)" in tail
