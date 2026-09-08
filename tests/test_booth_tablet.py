"""The exhibition tablet: one state, two screens, and no dead ends.

The stand's whole interface is an iPad, and the robot is unchanged -- English
words and faces, no Japanese anywhere on it. That division only works if the
tablet and the robot never disagree about which moment they are in, so `phase`
is decided ONCE, in the loop, and both surfaces read it.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import webui.server as W
from webui.booth import CHOICES, booth_state, english_for


def _st(**kw):
    s = dict(W.STATE)
    s.update(kw)
    return s


# ------------------------------------------------------------- the phases --
def test_the_phase_follows_the_robot_not_the_tablet():
    """Every screen is a function of the flow state, so a visitor cannot get the
    tablet onto a page the robot is not on."""
    cases = [("S1_IDLE", "choose"), ("S3_ACK", "ack"), ("S4_PLAN", "sweep"),
             ("S5B_TRACK", "watch"), ("S6_FINETUNE", "watch"),
             ("S7a", "notice"), ("S7b", "notice")]
    for state, phase in cases:
        assert booth_state(_st(flow_state=state), [], None)["phase"] == phase, state


def test_the_wait_after_the_sweep_is_still_the_sweep_screen():
    """The head finishes S4 and moves on while the VLM is still out -- 5 to 20 s
    of it. Falling back to `choose` there would offer the visitor a second
    choice while the first is still being compiled."""
    s = booth_state(_st(flow_state="S5A_SETTLE", plan_pending=True), [], None)
    assert s["phase"] == "sweep"


# ------------------------------------------------------ the visitor's tap --
def test_the_tap_sends_english_to_the_planner():
    """The visitor reads Japanese; the system is unchanged and reads English."""
    for c in CHOICES:
        assert english_for(c["id"]) == c["en"]
        assert c["en"].isascii(), "the planner never sees Japanese"
        assert not c["ja"].isascii(), "the visitor never sees English"


def test_an_id_we_did_not_write_installs_nothing():
    assert english_for("'; DROP") is None
    assert english_for("") is None
    assert english_for(None) is None


# ------------------------------------------------------------ the sweep --
def test_the_chosen_station_is_marked_and_the_rest_are_not():
    meta = {"dir": "20260817_181729", "richest_pan": -60,
            "shots": [{"pan": p, "file": f"pan_{p:+04d}.jpg",
                       "dets": [{"tier": "focus"}] if p == -60 else []}
                      for p in (-60, -30, 0, 30, 60)]}
    s = booth_state(_st(flow_state="S4_PLAN"), [], meta)
    assert len(s["shots"]) == 5
    assert s["chosen_pan"] == -60
    assert [sh["dir"] for sh in s["shots"]] == ["20260817_181729"] * 5


def test_no_sweep_yet_is_empty_not_broken():
    s = booth_state(_st(flow_state="S4_PLAN"), [], None)
    assert s["shots"] == [] and s["chosen_pan"] is None


# ------------------------------------------------------------ the notice --
def test_ok_is_never_answered_by_a_blank_screen():
    """The strip takes another 6-45 s to close, but the judge's sentence was
    written from five frames BEFORE S7 played -- so it is already here at the
    moment the visitor is asked to press OK."""
    s = booth_state(_st(flow_state="S7b", describe="Someone reached for the bag."),
                    [], None)
    assert s["phase"] == "notice"
    assert s["describe"] == "Someone reached for the bag."


def test_the_wall_shows_the_most_recent_first():
    recs = [{"note": f"n{i}", "thumb": f"t{i}.jpg", "time": "12:00"}
            for i in range(9)]
    s = booth_state(_st(flow_state="S5B_TRACK"), recs, None)
    assert [r["note"] for r in s["stories"]][:3] == ["n8", "n7", "n6"]
    assert len(s["stories"]) == 6, "a wall, not an archive"


# ------------------------------------------------------------ the routes --
def test_the_page_and_the_poll_serve(tmp_path):
    W.ARGS = __import__("argparse").Namespace(feed_dir=str(tmp_path), web_port=8123)
    W.serve(W.ARGS)
    time.sleep(0.4)
    base = "http://127.0.0.1:8123"
    page = urllib.request.urlopen(base + "/booth", timeout=5).read().decode()
    assert "user-scalable=no" in page, "an iPad will pinch-zoom the layout apart"
    assert "setInterval(poll,200)" in page, "1200ms lags visibly behind the chirp"
    assert 'lang=ja' in page

    # The Japanese lives in the POLL, not in the page. One reload is not needed
    # to change the wording, and more importantly the page holds no copy of the
    # sentences that could drift from the ones the planner is given.
    assert "荷物に触ったら教えて" not in page
    raw = urllib.request.urlopen(base + "/booth.json", timeout=5).read()
    assert "荷物に触ったら教えて" in raw.decode("utf-8")
    data = json.loads(raw)
    assert data["phase"] in ("choose", "ack", "sweep", "watch", "notice")
    assert [c["id"] for c in data["choices"]] == [c["id"] for c in CHOICES]


def test_both_screens_can_take_the_ok():
    """The CoreS3 sends IN OK; this is the same event from the tablet. The loop
    drains one flag, so whichever arrives first is the one that counts."""
    src = open(os.path.join(ROOT, "noticebot_loop.py")).read()
    i = src.index('UI.STATE.get("pending_ok")')
    assert 'ui_events.append("ok")' in src[i:i + 240]
    assert 'UI.STATE["pending_ok"] = False' in src[i:i + 240]


# ------------------------------------------------- one gesture, two meanings --
def test_a_tap_wakes_it_from_idle_and_corrects_it_while_watching():
    src = open(os.path.join(ROOT, "noticebot_loop.py")).read()
    i = src.index('elif "BODYTAP" in line:')
    block = src[i:i + 1800]
    assert 'flow.state == "S1_IDLE"' in block
    assert 'player.request("S2_LISTEN")' in block, "the clip that lifts out of the bow"
    assert 'player.arm_next("S1_IDLE")' in block, "or it holds facing the visitor"
    assert 'events.append("tap")' in block, "every other state still means 'not that one'"
