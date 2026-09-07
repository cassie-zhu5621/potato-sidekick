"""A finding is not thrown away because the run ended a few seconds later.

Two ways a real event used to vanish, both found on 2026-08-12 in one session.

AN OPEN STORY DIED WITH THE PROCESS. A burst stays open through `linger` so the
follow-through is on record -- "...and then they sat down" -- which means Ctrl-C
inside that window discarded the strip, the sentence and the log line. That
session had three judge-CONFIRMED findings and one report: the first was cancelled
by a re-brief, the last was still collecting when the run stopped.

A SUPPRESSED CARD PAID A COOLDOWN IT HAD NOT EARNED. The gate can only carry one
moment at a time, so a card firing while an earlier one is still being reported is
dropped -- and it had already spent `_fired_at`, so the next fifteen seconds were
refused too. Reported as "it is always suppressed and cooling and never fires
again". The cooldown is the price of having reported something; a card dropped
before it reached anybody reported nothing.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import noticebot_loop  # noqa: F401  -- read as source
import session.storyboard as SB
from perception.watch_exec import WatchExecutor

SPEC = {"watch": [{"all": [9], "on": "plant", "within_s": 5, "label": "touch plant"}]}


# ------------------------------------------------- the strip survives Ctrl-C --
def _one_open_story():
    d = tempfile.mkdtemp()
    sb = SB.Storyboard(feed_dir=d, offline=True)
    room = np.full((360, 640, 3), 120, np.uint8)
    sb.open({"label": "touch plant", "all": [9]}, room, {9: True}, {}, 0)
    for k in range(3):
        sb.step(room, {9: True}, {}, [], t=time.time() + k * 4)
    return d, sb


def test_an_open_story_is_written_rather_than_dropped():
    d, sb = _one_open_story()
    try:
        assert sb.bursts, "the fixture is meant to leave one open"
        assert sb.flush("test") == 1
        recs = [json.loads(l) for l in open(os.path.join(d, "attention_log.jsonl"))]
        assert len(recs) == 1 and recs[0]["label"] == "touch plant"
        assert recs[0]["shots"] >= 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_it_says_the_strip_was_cut_short():
    """Its length is when Ctrl-C was pressed, not when the event ended. Pooled
    with the others it would make every strip-length statistic a statement about
    how long the researcher left the run going."""
    d, sb = _one_open_story()
    try:
        sb.flush("test")
        rec = json.loads(open(os.path.join(d, "attention_log.jsonl")).read().strip())
        assert rec["truncated"] is True
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_normal_story_is_not_marked():
    d = tempfile.mkdtemp()
    try:
        sb = SB.Storyboard(feed_dir=d, offline=True, linger=0.0)
        room = np.full((360, 640, 3), 120, np.uint8)
        sb.open({"label": "x", "all": [9]}, room, {9: True}, {}, 0)
        for k in range(4):                       # runs past linger; closes itself
            sb.step(room, {9: False}, {}, [], t=time.time() + 10 + k * 5)
        for _ in range(40):
            if os.path.exists(os.path.join(d, "attention_log.jsonl")):
                break
            time.sleep(0.05)
        rec = json.loads(open(os.path.join(d, "attention_log.jsonl")).read().strip())
        assert rec["truncated"] is False
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_flush_is_the_first_thing_the_teardown_does():
    """After it come prints and port closes; a story written after a serial port
    is shut is a story written into a traceback."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    # The loop's own teardown, not the first `finally:` in the file -- there is
    # one in the terminal-mode helper too, and anchoring on the keyword alone
    # asserted against the wrong function.
    end = src.index("frames offered to perception")
    tail = src[src.rindex("    finally:\n", 0, end):end]
    assert "story.flush" in tail, tail


def test_the_teardown_flush_is_not_on_a_thread():
    """`_finalize` normally runs on a daemon thread. During teardown that thread
    is killed as the interpreter exits, which is the failure being fixed.

    Was `"Thread" not in flush`. STOP now flushes too (see test_storyboard_reset)
    and that one MUST be threaded -- the session continues and `_finalize` makes
    a narration call. So the property is now about the default and who overrides
    it, not about the word being absent.
    """
    import inspect
    assert inspect.signature(SB.Storyboard.flush).parameters["threaded"].default is False
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    end = src.index("frames offered to perception")
    tail = src[src.rindex("    finally:\n", 0, end):end]
    assert "threaded" not in tail, "the teardown must take the inline default"


# ------------------------------------------- the cooldown is only for reports --
#
# Rewritten 2026-08-12 when firing stopped requiring a fresh rising edge. These
# tested `unfire`, a FULL refund, which was safe only because `_was_sat` held the
# card back until the relation broke and re-formed. With the edge gone a zero
# cooldown re-fires on the very next frame; the refund is now a short cooldown.
def test_a_dropped_card_does_not_pay_the_full_cooldown():
    ex = WatchExecutor(SPEC, persist=1, cooldown=15)
    fired, st = ex.step({9: True}, 0.0)
    assert len(fired) == 1 and st[0].cooldown_remaining_s == 15.0
    ex.recool(fired[0], 2.0)
    _, st = ex.step({9: True}, 0.1)
    assert st[0].cooldown_remaining_s <= 2.0


def test_and_is_armed_again_within_seconds():
    """The occurrence is still lost -- the gate was busy and nobody saw it. What
    must not also be lost is everything for the next fifteen seconds."""
    def run(refund):
        ex = WatchExecutor(SPEC, persist=1, cooldown=15)
        fired, _ = ex.step({9: True}, 0.0)
        if refund:
            ex.recool(fired[0], 2.0)
        return [t for t in (1.0, 3.0, 5.0, 8.0) if ex.step({9: True}, t)[0]]

    assert run(False) == [], "the old behaviour, kept here as the contrast"
    assert run(True) == [3.0]


def test_the_refund_is_not_zero():
    """Zero means it re-fires on the next frame, straight back into the gate that
    is still busy, once per frame until it clears."""
    ex = WatchExecutor(SPEC, persist=1, cooldown=15)
    fired, _ = ex.step({9: True}, 0.0)
    ex.recool(fired[0], 2.0)
    assert ex.step({9: True}, 0.25)[0] == []


def test_consecutive_rejections_back_off():
    """A relation that is continuously true and continuously wrong would occupy
    the judge every four seconds otherwise -- and the gate is single-file, so
    whatever occupies it suppresses every other card."""
    ex = WatchExecutor(SPEC, persist=1, cooldown=15)
    got = []
    for _ in range(4):
        fired, st = ex.step({9: True}, 0.0)
        assert fired, "fixture: cooldown is zeroed below so it fires each pass"
        ex.recool(fired[0], 4.0, backoff=True)
        got.append(ex._fired_cd[0])
        ex._fired_at[0] = -1e9        # skip ahead past the cooldown
    assert got == [4.0, 8.0, 15.0, 15.0], got


def test_the_backoff_resets_when_the_situation_changes():
    ex = WatchExecutor(SPEC, persist=1, cooldown=15)
    fired, _ = ex.step({9: True}, 0.0)
    ex.recool(fired[0], 4.0, backoff=True)
    ex.recool(fired[0], 4.0, backoff=True)
    assert ex._fired_cd[0] == 8.0
    for t in (1.0, 3.0, 5.0, 7.0):                 # relation lets go
        ex.step({9: False}, t)
    fired, _ = ex.step({9: True}, 20.0)
    ex.recool(fired[0], 4.0, backoff=True)
    assert ex._fired_cd[0] == 4.0


def test_the_loop_gives_back_what_the_busy_gate_drops():
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index("suppressed later group of")
    assert "recool" in src[i - 900:i + 200]


def test_the_loop_backs_off_on_rejection_but_not_on_suppression():
    """A rejection cost a VLM call and got an answer; a gate collision cost
    nothing and got no answer. Only the first escalates."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index("suppressed later group of")
    assert "backoff" not in src[i - 900:i + 200]
    assert src.count("backoff=True") == 2, "both rejection sites"


# ------------------------------------- nor by waiting for a hand to let go --
#
# Firing required a fresh rising edge until 2026-08-12: satisfied, and not
# satisfied on the previous frame. Cassie removed it, on her reasoning about the
# room -- an actor does not stand there repeating themselves, they finish and
# leave, so nothing real was protected. What it DID protect was one accidental
# trigger by somebody sitting nearby, which latched the entry for as long as
# their hand stayed put and locked out the genuine event at that spot afterwards.
def _run(script, cooldown=15):
    ex = WatchExecutor(SPEC, persist=1, cooldown=cooldown)
    return [t for t, v in script if ex.step({9: bool(v)}, t)[0]]


def test_a_hand_that_never_lets_go_still_reports_again():
    """The reported symptom, in one line: hold it there and nothing ever fires
    again. The page showed the entry stuck on "held . release to rearm"."""
    held = [(float(t), 1) for t in range(0, 46)]
    assert _run(held) == [0.0, 15.0, 30.0, 45.0]


def test_an_accidental_trigger_does_not_lock_out_the_real_one():
    """Somebody rests a hand at the spot at t=0 and leaves it there. The event
    the card is actually for happens at t=20. Under the edge rule the relation
    never broke, so there was no second edge and it could not fire -- ever."""
    hand_stays = [(float(t), 1) for t in range(0, 30)]
    assert 15.0 in _run(hand_stays), "the real event at t=20 is inside this window"


def test_the_cooldown_is_still_the_throttle():
    """Removing the edge must not mean firing every frame."""
    assert _run([(t / 4.0, 1) for t in range(0, 4 * 20)]) == [0.0, 15.0]


# ------------------------------------------ and it survives the loop's copy --
def test_the_refund_works_on_the_copy_the_loop_actually_holds():
    """The loop never hands back the object it was given. It does
    `entries = [dict(e) for e in order_coincident_candidates(fired)]` so the
    judge can annotate a candidate without writing into watch state, and it is
    that copy that reaches unfire/recool.

    Matching on `e is entry` therefore never matched in the live loop: the
    refund was written, passed its test against the original object, and did
    nothing in the room. Reported 2026-08-12 as "suppressed still shows 14 s".
    """
    ex = WatchExecutor(SPEC, persist=1, cooldown=15)
    fired, _ = ex.step({9: True}, 0.0)
    assert ex.recool(dict(fired[0]), 2.0) is True
    _, st = ex.step({9: True}, 0.1)
    assert st[0].cooldown_remaining_s <= 2.0


def test_a_relabelled_copy_is_still_the_same_card():
    """The judge rewrites the label on its copy -- that is the sentence the
    participant reads, not an identity. Keying on it would make the refund miss
    exactly the entries that got as far as being judged."""
    ex = WatchExecutor(SPEC, persist=1, cooldown=15)
    fired, _ = ex.step({9: True}, 0.0)
    copy = dict(fired[0])
    copy["label"] = "someone picked up the plant"
    assert ex.recool(copy, 2.0) is True


def test_an_entry_from_another_spec_is_not_matched():
    ex = WatchExecutor(SPEC, persist=1, cooldown=15)
    ex.step({9: True}, 0.0)
    assert ex.recool({"all": [7], "on": "whiteboard"}, 2.0) is False
