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
    cases = [("S1_IDLE", "choose"), ("S3_ACK", "room"), ("S4_PLAN", "room"),
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
    """Driven off the WIDTH, which is the dimension a two-line Japanese
    sentence actually runs out of, with a floor high enough that a small
    laptop window still shows it big -- that window is where it gets checked."""
    import re
    from webui.booth import PAGE
    css = re.sub(r"/\*.*?\*/", "", PAGE, flags=re.S)
    card = re.search(r"\.card\{[^}]*\}", css).group(0)
    size = re.search(r"font-size:clamp\((\d+)px", card)
    assert size and int(size.group(1)) >= 44, card
    assert "text-align:center" in card


def test_app_is_laid_out_once():
    """Two live #app rules gave it height:100% AND flex:1. With the strip above
    it the page ran past the viewport and overflow:hidden cut the bottom off --
    the grid on one screen, the second button on the other."""
    import re
    from webui.booth import PAGE
    css = re.sub(r"/\*.*?\*/", "", PAGE, flags=re.S)
    assert len(re.findall(r"#app\{", css)) == 1


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


def test_an_override_belongs_to_the_clip_that_was_playing_when_it_was_armed():
    """Both directions of this were bugs on 2026-08-18, and they pull opposite
    ways -- which is why the rule is about ORDER, not about interruption.

    Stale override left alive: the head tap arms S1_IDLE and plays S2, the
    visitor picks a task while S2 is still running, S2 is cut short, and
    S3_ACK's `then` (S4_PLAN) is replaced by S1_IDLE. Nod, sleep, no sweep.

    Cleared on the interrupt instead: OK arms S5B_TRACK and requests S3_ACK,
    that request interrupts S7b, and the arming meant for S3_ACK is wiped a
    microsecond after it was made -- S3_ACK falls back to S4_PLAN and the robot
    re-scans. Reported as "OK still goes back to scan".

    Only the caller knows which clip an override was for, and it says so by
    calling request() first and arm_next() second.
    """
    import inspect
    from robot.clip_player import ClipPlayer
    src = inspect.getsource(ClipPlayer.request)
    assert "self._next_override = None" in src

    play = open(os.path.join(ROOT, "robot", "clip_player.py")).read()
    i = play.index("completed = self._play_once(frames)")
    assert "_next_override" not in play[i:i + 400], "not on the interrupt path"


def test_every_caller_requests_first_and_arms_second():
    src = open(os.path.join(ROOT, "noticebot_loop.py")).read()
    for a, b in [('player.request("S3_ACK")', 'player.arm_next("S1_IDLE")'),
                 ('player.request("S2_LISTEN")', 'player.arm_next("S1_IDLE")')]:
        i = src.index(a)
        assert b in src[i:i + 200], f"{a} must be followed by {b}"


def test_ok_emits_the_state_before_the_arming():
    """The loop turns ("state", X) into player.request(X), which clears any
    pending override -- so the arming has to be emitted on the far side of it."""
    import session.session_flow as F
    f = F.SessionFlow(now=lambda: 0.0)
    f.state, f.transcript = "S7b", "x"
    kinds = [k for k, _ in f.feed("ok")]
    assert kinds.index("state") < kinds.index("ack_then")


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


def test_the_sixth_cell_shows_the_rule_in_the_developer_pages_words():
    """Five stations in a 3x2 grid leave one cell empty, and what belongs there
    is the condition. A visitor who can read hands-on + bag knows what to DO --
    which at a stand is the difference between a demo that gets triggered and
    one that does not."""
    from webui.booth import REL_NAMES
    import webui.server as WS
    st = _st(flow_state="S5B_TRACK", status=[{
        "label": "touching the bag", "onobj": "bag",
        "all": [9], "any": [], "not": [], "then": [],
        "sat": True, "cool": False, "on": {"9": True}}])
    w = booth_state(st, [], None)
    assert w["watch"][0]["on"] == "bag"
    assert w["watch"][0]["all"] == [9]
    assert w["watch"][0]["truth"]["9"] is True
    # the same ids the developer page names, so the two cannot drift
    assert set(REL_NAMES) == set(WS.REL_NAMES)
    assert REL_NAMES[9].startswith("hands")


def test_the_nod_does_not_get_a_screen_of_its_own():
    """1.7 s. A page that appears and vanishes inside two seconds is a flash,
    not information -- and S3_ACK happens in two different places (after a
    choice, after OK), so any one screen would be wrong in one of them."""
    from webui.booth import PAGE
    assert booth_state(_st(flow_state="S3_ACK"), [], None)["phase"] == "room"
    assert "phase==='ack'" not in PAGE


def test_it_reads_the_same_fields_the_live_panel_does():
    """build_status is the one place an entry becomes a row. Deriving these
    from the spec separately would give the tablet its own opinion of what is
    satisfied."""
    from webui.booth import PAGE
    i = PAGE.index("function specInner(")
    block = PAGE[i:i + 900]
    for k in ("w.all", "w.any", "w.then", "w.truth", "w.on", "w.sat"):
        assert k in block, k
    assert "THEN" in block and "AND" in block and "OR" in block


def test_the_grid_has_six_cells_and_the_last_is_the_rule():
    from webui.booth import PAGE
    i = PAGE.index("function cells(")
    block = PAGE[i:i + 1400]
    assert "i<5" in block, "five stations"
    assert "out.push(specCell())" in block, "and the rule in the sixth"


def test_only_the_watched_direction_is_live_and_it_survives_a_re_render():
    """An <img> on an MJPEG stream holds an open connection. Inside the
    innerHTML render() rewrites, that connection would be torn down and
    reopened five times a second -- a black flicker and a new TCP connection
    each poll. It sits outside #app and is positioned over the cell instead."""
    from webui.booth import PAGE
    assert "<img id=live" in PAGE
    i = PAGE.index("function cells(")
    grid = PAGE[i:i + 1400]
    assert "/stream.mjpg" not in grid, "the stream is not in the rewritten markup"
    assert 'id=livecell' in grid, "the chosen cell is an empty frame to lay it over"
    assert grid.count("/sweepimg/") == 1, "the other four stay as sweep stills"

    place = PAGE[PAGE.index("function place("):]
    assert "getBoundingClientRect" in place[:1800], "measured, not styled into the grid"
    assert "if(!el.src)" in place[:1800], "opened once, not every frame"


def test_expanding_dims_everything_but_the_picture_and_the_one_rule():
    """The rule is NOT redrawn beside the live view -- it is already in the sixth
    cell. A second copy is a second thing to keep in step, and the visitor has
    to find it again in a new place."""
    import re
    from webui.booth import PAGE
    assert 'onpointerdown="zoom()"' in PAGE
    i = PAGE.index("function place(")
    block = PAGE[i:i + 1600]
    assert "app.classList.toggle('dim',BIG)" in block
    assert "specInner()" not in block, "one copy of the rule, not two"
    assert "spec.getBoundingClientRect()" in block, "sized to stop short of it"

    css = re.sub(r"/\*.*?\*/", "", PAGE, flags=re.S)
    assert "#app.dim .cell.spec{opacity:1}" in css, "the rule stays lit"
    assert "#app.dim .bar{display:none}" in css, "the bottom row goes"


def test_the_prompt_is_never_behind_the_expanded_view():
    """The live view can fill most of the screen. The one thing that must never
    be behind it is the robot asking to be answered."""
    import re
    from webui.booth import PAGE
    css = re.sub(r"/\*.*?\*/", "", PAGE, flags=re.S)
    live = int(re.search(r"#live\{[^}]*z-index:(\d+)", css).group(1))
    veil = int(re.search(r"#veil\{[^}]*z-index:(\d+)", css).group(1))
    assert veil > live, f"veil {veil} must sit above live {live}"


def test_the_wait_has_a_face_and_a_bar_that_promises_nothing():
    """6-45 s with one small line on it reads as a machine that has stopped.
    Nothing here knows how long the narration will take, so a bar that claimed
    to would be lying -- it is a sign of life, not a measure."""
    import re
    from webui.booth import PAGE
    assert "class=wface" in PAGE and "class=wbar" in PAGE
    css = re.sub(r"/\*.*?\*/", "", PAGE, flags=re.S)
    bar = re.search(r"\.wbar i\{[^}]*\}", css).group(0)
    assert "animation:slide" in bar
    assert "width:38%" in bar, "a fixed sliver -- it is not tracking progress"


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
    assert s["n_stories"] == 9


def test_the_count_is_what_tells_the_tablet_a_report_has_landed():
    """After OK the tablet stops following the robot and waits. It cannot know a
    story has been written without a number that changes -- the robot has
    already gone back to watching, so its state says nothing about the report."""
    before = booth_state(_st(flow_state="S7b"), [{"note": "a"}], None)
    after = booth_state(_st(flow_state="S5B_TRACK"),
                        [{"note": "a"}, {"note": "b"}], None)
    assert after["n_stories"] > before["n_stories"]
    assert after["stories"][0]["note"] == "b", "newest first"


def test_the_tablet_leaves_the_robot_behind_after_ok():
    """The robot returns to watching immediately, which is right -- it has a job.
    The visitor is owed the report they just asked for, and it takes another
    6-45 s to write. Following the robot back to the room grid threw that away
    and made OK look like it had cancelled something."""
    from webui.booth import PAGE
    i = PAGE.index("async function ok(")
    block = PAGE[i:i + 400]
    assert "MODE='report'" in block
    assert "WAIT_FROM=S.n_stories" in block, "wait for the NEXT story, not any"


def test_a_new_prompt_does_not_cover_the_report_being_read():
    """The robot re-enters S7 on the next finding. A prompt reappearing over the
    report is the same interruption OK was pressed to end."""
    from webui.booth import PAGE
    i = PAGE.index("if(S.phase==='notice'")
    assert "MODE===null" in PAGE[i:i + 120]


def test_the_wall_is_a_chip_in_the_top_row_not_a_button_in_the_way():
    """It is a way out, not an offer. At the size of a state face, in the top
    corner, it cannot compete with the two things a visitor is actually being
    asked to choose between."""
    from webui.booth import PAGE
    assert 'class="f wallbtn" id=wb' in PAGE
    assert 'onpointerdown="wall()"' in PAGE
    assert "function back()" in PAGE, "and a way out of it"
    assert "これまでに気づいたこと（" not in PAGE, "the full-width button is gone"


def test_a_story_is_the_wide_strip_not_a_thumbnail():
    """The storyboard composites its panels into one wide jpg -- that IS the
    shape of a finding, several moments in a row. The thumbnail is one squashed
    copy and loses the thing that makes a story a story: that it went on."""
    from webui.booth import PAGE
    i = PAGE.index("function story1(")
    block = PAGE[i:i + 600]
    assert "/frame/" in block
    # thumb appears only as the fallback inside the same expression
    assert block.index("/frame/") < block.index("s.thumb")
    assert "class=pan" in block, "scrolled sideways, not squeezed"


def test_the_strip_scrolls_sideways_at_full_height():
    import re
    from webui.booth import PAGE
    css = re.sub(r"/\*.*?\*/", "", PAGE, flags=re.S)
    pan = re.search(r"\.pan\{[^}]*\}", css).group(0)
    assert "overflow-x:auto" in pan
    img = re.search(r"\.pan img\{[^}]*\}", css).group(0)
    assert "width:auto" in img, "let it be as wide as it is"


def test_the_exhibition_does_not_re_sweep_on_its_own():
    """A self-directed sweep is a good beat in a 15-minute session. At a stand it
    lands in the middle of a stranger's ninety seconds and the head swings off
    the thing they just asked it to watch."""
    import robot.states as ST
    assert ST.REPLAN_PERIOD_S == 0.0
    assert ST.REPLAN_IDLE_S == 0.0


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
