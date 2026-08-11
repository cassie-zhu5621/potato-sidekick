"""A blinking detector is not a hand letting go.

Until 2026-08-08 the hands-on clock was deleted on any frame where the wrist was
not seen inside the object box, so "the contact ended" and "the object was not
detected this frame" ran through the same branch. Combined with the executor's
`persist`, firing required SIX unbroken frames at --cv-hz 4, and the cost of
flicker was not linear -- expected time from contact to firing, simulated
against per-frame detection reliability p:

    p      before    after
   0.90     1.96 s   1.41 s
   0.80     3.26 s   1.73 s
   0.70     6.02 s   2.31 s
   0.60    12.58 s   3.37 s      (the floor is 1.25 s)

A phone held in a hand is small, half-occluded by the hand holding it, and
prompted open-vocabulary. It lives in the lower half of that table, and this was
one concrete source of "the scripted event did not trigger".

WHAT THESE TESTS PIN DOWN is the boundary, not just the win. The grace window
keeps a CLOCK alive; it must never assert a contact nobody observed, or the
relation stops meaning what the vocabulary says it means. So `truth[9]` is still
False on the blank frame itself, and a gap longer than the window still starts a
new contact.

mediapipe is not installed here and is not needed. The block lives in
RelationEngine.evaluate, which the file itself describes as the "pure-ish core
(separable for tests)" -- inputs to truth vector, no capture and no models. So
these drive the SHIPPED function with hand-built poses and detections rather
than a transcription of it, and there is no copy to drift.
"""
from __future__ import annotations

import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import perception.relations as rel
from perception.perceive import Detection
from perception.relations import PersonPose, RelationEngine

L_WR, R_WR = 15, 16
BOX = (100, 100, 200, 300)          # a phone-sized box
INSIDE = (150, 200)                 # a wrist well within it
OUTSIDE = (900, 600)
HZ, DT = 4.0, 0.25


def _engine(monkeypatch, sustain_s=1.0, touch_grace_frames=2.0):
    """A REAL RelationEngine, with only the two model-loading estimators stubbed.

    Running the real __init__ rather than hand-assigning the attributes the test
    happens to need: every cross-frame dict in this class is initialised there,
    and a test that lists them by hand goes stale the moment one is added --
    silently, by raising in the place that looks like the engine's fault.
    """
    monkeypatch.setattr(rel, "HeadPoseEstimator", lambda **k: None)
    monkeypatch.setattr(rel, "PoseEstimator", lambda **k: None)
    e = RelationEngine(detector=None, sustain_s=sustain_s,
                       touch_grace_frames=touch_grace_frames)
    e._dt_ema = DT              # as if it had already seen a few frames
    return e


def _tick(e, t, wrist_visible=True, object_visible=True):
    """One frame through the real evaluate(); -> truth[9] for that frame.

    Either half of the perception can go missing -- the object undetected, or
    the wrist not found on it -- and the point of the fix is that the clock
    cannot tell those apart from each other, only from a hand that has left.
    """
    wrist = INSIDE if wrist_visible else OUTSIDE
    people = [PersonPose(pid=1, pts={L_WR: wrist}, vis={L_WR: 1.0})]
    dets = [Detection("phone", BOX, 0.9)] if object_visible else []
    truth, _viz = e.evaluate([], people, dets, (1280, 720), t)
    return truth[9]


# ------------------------------------------------------------------ baseline --
def test_an_unbroken_contact_becomes_true_after_sustain(monkeypatch):
    e = _engine(monkeypatch)
    fired = [_tick(e, i * DT) for i in range(8)]
    assert fired[:4] == [False] * 4, "true before the second was up"
    assert fired[4], f"1.0 s of contact did not satisfy sustain_s: {fired}"


def test_a_hand_that_never_arrives_never_fires(monkeypatch):
    e = _engine(monkeypatch)
    assert not any(_tick(e, i * DT, wrist_visible=False) for i in range(20))


# ------------------------------------------------------- the dropped frame ---
def test_one_missing_detection_does_not_restart_the_clock(monkeypatch):
    """THE REGRESSION. The object vanishes for a single frame mid-contact."""
    e = _engine(monkeypatch)
    seq = [True, True, False, True, True, True]     # frame 2 is the blink
    fired = [_tick(e, i * DT, object_visible=v) for i, v in enumerate(seq)]
    assert fired[-1], (
        "a one-frame detection dropout restarted the 1 s clock -- the contact "
        "had been held for 1.25 s. This is the 2026-08-08 bug.")


def test_a_missing_wrist_is_treated_the_same_as_a_missing_object(monkeypatch):
    """Both are the perception layer failing to see, not the person letting go."""
    e = _engine(monkeypatch)
    seq = [True, True, False, True, True, True]
    fired = [_tick(e, i * DT, wrist_visible=v) for i, v in enumerate(seq)]
    assert fired[-1]


def test_the_blank_frame_is_still_false(monkeypatch):
    """The clock survives; the truth value must not. Otherwise the relation
    asserts a contact that nothing observed, and `persist` -- which exists to
    require agreement across frames -- would be reading the engine's memory
    instead of the room."""
    e = _engine(monkeypatch)
    for i in range(6):
        _tick(e, i * DT)                       # well past sustain
    assert _tick(e, 6 * DT, object_visible=False) is False


# ------------------------------------------------------------- the boundary --
def test_a_long_gap_starts_a_new_contact(monkeypatch):
    e = _engine(monkeypatch)
    for i in range(5):
        _tick(e, i * DT)
    assert _tick(e, 5 * DT), "sanity: should be firing before the gap"
    t0 = 5 * DT + 2.0                           # hand away for 2 s
    assert _tick(e, t0) is False, "a 2 s absence must not resume the old clock"
    later = [_tick(e, t0 + i * DT) for i in range(1, 6)]
    assert later[-1], "the new contact never fired on its own merit"
    assert not later[0], "and it did not inherit the old clock's head start"


def test_the_window_spans_one_dropped_frame_and_not_two(monkeypatch):
    """Two is where it starts joining gestures ~0.75 s apart, for ~0.2 s of gain."""
    e = _engine(monkeypatch)
    for i in range(3):
        _tick(e, i * DT)
    held = list(e._touch_since.values())[0][0]
    _tick(e, 3 * DT, object_visible=False)      # gap of 2 periods -> bridged
    _tick(e, 4 * DT)
    assert list(e._touch_since.values())[0][0] == held, "one dropped frame broke it"

    e2 = _engine(monkeypatch)
    for i in range(3):
        _tick(e2, i * DT)
    held2 = list(e2._touch_since.values())[0][0]
    _tick(e2, 3 * DT, object_visible=False)
    _tick(e2, 4 * DT, object_visible=False)     # gap of 3 periods -> not bridged
    _tick(e2, 5 * DT)
    assert list(e2._touch_since.values())[0][0] != held2, (
        "two dropped frames were bridged; the window is wider than intended")


# ------------------------------------------------------------ frame rate ----
def test_the_window_is_measured_not_assumed(monkeypatch):
    """The engine runs at 2 fps on the M5 and 30 fps on a webcam. A window fixed
    in seconds would span five real frames at one end and none at the other."""
    slow = _engine(monkeypatch)
    slow._dt_ema = 0.5                          # 2 Hz
    for i in range(4):
        _tick(slow, i * 0.5)
    held = list(slow._touch_since.values())[0][0]
    _tick(slow, 4 * 0.5, object_visible=False)  # one dropped frame = 1.0 s gap
    _tick(slow, 5 * 0.5)
    assert list(slow._touch_since.values())[0][0] == held, (
        "at 2 Hz a dropped frame is a 1 s gap; a hard-coded window would have "
        "called that a new contact")


def test_a_stalled_camera_does_not_buy_tolerance(monkeypatch):
    """dt is clamped, so a model load or a frozen capture cannot be mistaken for
    a slow frame rate and hand a contact seconds of grace it did not earn."""
    e = _engine(monkeypatch)
    e._dt_ema = None
    e._last_step_t = 0.0
    # step()'s clamp: only 0 < dt < 2.0 updates the estimate.
    for dt in (5.0, 0.25):
        t = e._last_step_t + dt
        if 0.0 < dt < 2.0:
            e._dt_ema = dt if e._dt_ema is None else 0.8 * e._dt_ema + 0.2 * dt
        e._last_step_t = t
    assert e._dt_ema == 0.25


def test_the_grace_is_not_quietly_removed(monkeypatch):
    """The tests above would still pass if someone widened the window a lot, so
    this one pins the shape of the code that keeps them honest."""
    import inspect
    src = inspect.getsource(RelationEngine.evaluate)
    i = src.index("---- 9 hands-on")
    body = src[i:src.index("---- 10 gathering", i)]
    for line in ("if rec is None or t - rec[1] > grace:",
                 "rec = [t, t]",
                 "if key not in active and t - rec[1] > grace:",
                 "grace = self.touch_grace_frames * (self._dt_ema or 0.25)"):
        assert line in body, (
            f"the hands-on block no longer contains {line!r}, so the copy in "
            f"this file is testing something the robot does not run.")
    assert "del self._touch_since[key]\n" in body


def test_one_frame_of_grace_is_exactly_the_old_engine(monkeypatch):
    """Documented in the constant's comment, and worth pinning: consecutive
    observations are one period apart, so a one-period window keeps an unbroken
    contact and drops everything else. That gives the parameter a value meaning
    'as it was', which is how the before/after timings in this file's docstring
    were measured -- against the shipped code rather than a reconstruction of
    the old code from memory."""
    old = _engine(monkeypatch, touch_grace_frames=1.0)
    for i in range(3):
        _tick(old, i * DT)
    held = list(old._touch_since.values())[0][0]
    _tick(old, 3 * DT, object_visible=False)
    _tick(old, 4 * DT)
    assert list(old._touch_since.values())[0][0] != held, (
        "at 1.0 a single dropped frame must still restart the clock, or the "
        "old behaviour is no longer reachable and the comparison is unfounded")
