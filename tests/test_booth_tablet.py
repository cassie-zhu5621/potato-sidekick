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
    # Scanning and watching are ONE screen -- see the note in booth_state. The
    # visitor's question in both is which way it is looking, and the grid
    # answers that continuously; "tracking..." over an empty page said nothing
    # the robot standing in front of them was not already saying.
    cases = [("S1_IDLE", "choose"), ("S3_ACK", "ack"), ("S4_PLAN", "room"),
             ("S5A_SETTLE", "room"), ("S5B_TRACK", "room"),
             ("S6_FINETUNE", "room"), ("S7a", "notice"), ("S7b", "notice")]
    for state, phase in cases:
        assert booth_state(_st(flow_state=state), [], None)["phase"] == phase, state


def test_the_wait_after_the_sweep_is_not_a_second_choice():
    """The head finishes S4 and moves on while the VLM is still out -- 5 to 20 s
    of it. Falling back to `choose` there would offer the visitor a second
    choice while the first is still being compiled."""
    s = booth_state(_st(flow_state="S5A_SETTLE", plan_pending=True), [], None)
    assert s["phase"] == "room"


def test_the_red_frame_follows_the_aim_so_a_tap_has_an_answer():
    """Correcting the head is a gesture that has to be answered on the tablet.
    Reddening the sweep's own highest-scoring station instead would leave the
    frame where it was and make the tap look ignored."""
    meta = {"dir": "d", "richest_pan": -60,
            "shots": [{"pan": p, "file": f"p{p}.jpg", "dets": []}
                      for p in (-60, -30, 0, 30, 60)]}
    # mid-sweep: no aim yet, so the sweep's own pick stands in
    assert booth_state(_st(flow_state="S4_PLAN"), [], meta)["chosen_pan"] == -60
    # watching where the plan aimed it
    assert booth_state(_st(flow_state="S5B_TRACK", aimed_pan=-58.0),
                       [], meta)["chosen_pan"] == -60
    # tapped, re-aimed: the frame moves with it
    assert booth_state(_st(flow_state="S6_FINETUNE", aimed_pan=28.0),
                       [], meta)["chosen_pan"] == 30


def test_the_loop_publishes_where_it_is_aimed():
    src = open(os.path.join(ROOT, "noticebot_loop.py")).read()
    assert 'UI.STATE["aimed_pan"] = ctxd.get("aimed_pan")' in src


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


def test_waking_it_does_not_take_the_choice_away():
    """The visitor touches the head, the robot lifts it, and the next thing they
    have to do is pick a task. Mapping S2_LISTEN to its own screen removed both
    buttons at exactly that moment."""
    assert booth_state(_st(flow_state="S2_LISTEN"), [], None)["phase"] == "choose"


def test_the_strip_says_the_state_instead():
    """A state change is worth a glance, not a screen."""
    from webui.booth import FACES, face_key
    s = booth_state(_st(flow_state="S2_LISTEN"), [], None)
    assert s["face"] == "S2_LISTEN"
    assert s["phase"] == "choose", "the strip changes, the page does not"
    assert [f[0] for f in s["faces"]][0] == "S1_IDLE"


def test_the_faces_are_the_robots_own():
    """Copied from the firmware's uiFace(). Two surfaces, one vocabulary -- a
    visitor looking from the tablet to the robot sees the same thing twice
    rather than having to learn a second code."""
    from webui.booth import FACES
    ino = open(os.path.join(ROOT, "robot", "firmware", "cores3_sidekick",
                            "cores3_sidekick.ino")).read()
    for state, face, _label in FACES:
        if state == "S4_PLAN":
            continue          # planning wears no face on the robot, by design
        # The .ino is C: a backslash in the face is written doubled there. What
        # has to match is what the two screens DISPLAY, not how each language
        # spells it.
        as_c = face.replace("\\", "\\\\")
        assert f'"{as_c}"' in ino, f"{state}: {face} is not what the board shows"


def test_every_state_lights_exactly_one_lamp():
    from webui.booth import FACES, face_key
    keys = {k for k, _f, _l in FACES}
    for state in ("S1_IDLE", "S2_LISTEN", "S3_ACK", "S4_PLAN", "S5A_SETTLE",
                  "S5B_TRACK", "S6_FINETUNE", "S7a", "S7b", "S8_ERROR", ""):
        assert face_key(state) in keys, state


def test_the_choice_is_set_large_enough_to_read_standing_up():
    from webui.booth import PAGE
    assert "font-size:clamp(40px,min(7.6vw,8.4vh),104px)" in PAGE
    assert "text-align:center" in PAGE


def test_no_font_shorthand_ending_in_inherit():
    """`font: 700 40px/1.3 inherit` is invalid CSS -- `inherit` is a CSS-wide
    keyword, legal only as an entire value, never as the shorthand's family
    slot. The browser drops the whole declaration, so the element renders at the
    inherited default and the size is silently ignored. Every size on this page
    was being thrown away that way, and setting a bigger number changed nothing.
    """
    import re
    from webui.booth import PAGE
    # Comments out first -- the rule is explained in one, and the explanation
    # necessarily quotes the thing it forbids.
    css = re.sub(r"/\*.*?\*/", "", PAGE, flags=re.S)
    bad = [d for d in re.findall(r"font:[^;}]*", css) if "inherit" in d]
    assert not bad, bad


def test_the_player_drops_a_stale_override_when_a_clip_is_cut_short():
    """arm_next overrides the NEXT `then`. An interrupted clip never reaches a
    `then`, so the arming is stale -- and it then hijacked the next transition.
    The head tap arms S1_IDLE and plays S2; the visitor picks a task while S2 is
    still running; S3_ACK finishes and goes to S1_IDLE instead of S4_PLAN. The
    robot nodded and went back to sleep, and the sweep never happened."""
    src = open(os.path.join(ROOT, "robot", "clip_player.py")).read()
    i = src.index("completed = self._play_once(frames)")
    block = src[i:i + 1400]
    assert "self._next_override = None" in block
    assert block.index("self._next_override = None") < block.index("break")


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


def test_the_tablet_address_is_printed_at_startup(capsys):
    """So nobody has to guess which interface is Wi-Fi. `ipconfig getifaddr en0`
    is the usual advice and it is wrong often enough to matter -- en0 is not
    always Wi-Fi, and the venue changes the number anyway."""
    W.serve(__import__("argparse").Namespace(feed_dir=".", web_port=8125))
    out = capsys.readouterr().out
    ip = W.lan_address()
    assert ip is None or f"http://{ip}:8125/booth" in out
    # A URL, not the word -- the line deliberately SAYS "http, not https",
    # because Safari autocompletes to https and then fails on a certificate
    # error, which sends you looking at the wrong layer entirely.
    assert "https://" not in out, "the server is plain HTTP"
    assert ".local" not in out, "mDNS is intermittently refused on iOS"


def test_finding_the_address_sends_no_packet():
    """A UDP connect() only picks a route. Anything that actually reached the
    network would hang for the timeout when the venue's wifi is down, which is
    exactly when this line is being read."""
    import inspect
    src = inspect.getsource(W.lan_address)
    assert "SOCK_DGRAM" in src
    assert "192.0.2." in src, "TEST-NET-1: reserved, guaranteed never routed"


def test_both_screens_can_take_the_ok():
    """The CoreS3 sends IN OK; this is the same event from the tablet. The loop
    drains one flag, so whichever arrives first is the one that counts."""
    src = open(os.path.join(ROOT, "noticebot_loop.py")).read()
    i = src.index('UI.STATE.get("pending_ok")')
    assert 'ui_events.append("ok")' in src[i:i + 240]
    assert 'UI.STATE["pending_ok"] = False' in src[i:i + 240]


def test_the_tablet_fields_are_published_on_a_normal_run():
    """They were written inside the `view is None` arm -- the fallback for a
    detector that failed to load -- so on every real run they never executed.
    The tablet sat on the choose screen for the whole session: no sweep, no red
    frame, no notice. Nothing raised, because an unset flow_state is just a
    string that matches no state.

    Asserted by POSITION, since that is what was wrong: the publish has to come
    before the branch, not inside either half of it.
    """
    src = open(os.path.join(ROOT, "noticebot_loop.py")).read()
    pub = src.index('UI.STATE["flow_state"] = flow.state')
    branch = src.index("                if view is not None:\n"
                       "                    view.publish(")
    assert pub < branch, "the booth publish is inside the detector-failed arm again"
    for field in ("plan_pending", "noticed_n", "describe", "sweep_meta"):
        assert f'UI.STATE["{field}"]' in src[pub:branch], field


# ------------------------------------------------- one gesture, two meanings --
def test_a_tap_wakes_it_from_idle_and_corrects_it_while_watching():
    src = open(os.path.join(ROOT, "noticebot_loop.py")).read()
    i = src.index('elif "BODYTAP" in line:')
    block = src[i:i + 1800]
    assert 'flow.state == "S1_IDLE"' in block
    assert 'player.request("S2_LISTEN")' in block, "the clip that lifts out of the bow"
    assert 'player.arm_next("S1_IDLE")' in block, "or it holds facing the visitor"
    assert 'events.append("tap")' in block, "every other state still means 'not that one'"
