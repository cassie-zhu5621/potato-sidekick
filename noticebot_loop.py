#!/usr/bin/env python3
"""
noticebot_loop.py — the conductor for a user-study session.

Three devices, one loop, on the laptop:

  SERVOS  FE-URT2 -> 3x SCS0009, playing the v2 motion library as timed clips
          (robot_motion/hardware/clip_player.py)
  CAMERA  UVC module ON THE HEAD (OV4688, 32x32mm, H-FOV 58 deg)
  CoreS3  screen / sound / touch, unchanged protocol (cores3_link.py)

HYBRID CONTROL. Perception proposes, the researcher can always override. A
session with a participant in it cannot be paused to debug a detector, and a run
that dies mid-study costs a participant -- so every state is one keypress away
at all times, and perception is one small function that may return None forever
without anything else noticing.

  perception -> proposes a state    (perceive(), the hook at the bottom)
  researcher -> forces a state      (keys 1-8, always wins)
  CoreS3     -> participant input   (head tap = "not that one", buttons)

HEAD-MOUNTED CAMERA. Frames are only handed to perception once the commanded
pose has been still for SETTLE_MS. A frame grabbed mid-move is blurred AND is
attributed to a pose the head has already left; during S4 that would mean
scoring a station using someone else's picture. The gating falls out of the
player's settled_ms, and lines up with S4's per-station dwells for free.

Run:
  export NOTICEBOT_PORT=/dev/cu.usbmodem5B790340551
  python3 noticebot_loop.py --list-cams
  python3 noticebot_loop.py --cam 0
  python3 noticebot_loop.py --cam 0 --cores3 /dev/cu.usbmodemXXXX
  python3 noticebot_loop.py --cam 0 --no-view          # headless
"""
from __future__ import annotations
import argparse, os, sys, threading, time
from collections import deque

import cv2

ROOT = os.path.dirname(os.path.abspath(__file__))

from robot.scs import Bus, open_bus          
from robot.clip_player import ClipPlayer     
from session.stt import STT                   
from robot.pose import IDS                   
from robot import states as ST                    
from robot.pose import UNITS_PER_DEG, resolve 
from session.session_flow import SessionFlow, transcript_usable 
from planning.event_frames import select_temporal_frames

# The five S4 stations, as keyboard stand-ins for the web UI's pan buttons. Used
# in S6 to re-aim: the tap said "wrong direction", not "wrong task", so only the
# direction changes and the watch-spec survives.
PAN_KEYS = {"z": 60.0, "x": 30.0, "c": 0.0, "v": -30.0, "b": -60.0}

SETTLE_MS = 180.0        # stillness required before a frame is usable
DEFAULT_PERCEIVE_HZ = 1.0  # Grounding DINO default; YOLO-World can use --cv-hz 4


# --------------------------------------------------------------------------- #
# camera: newest-frame-only, on its own thread
# --------------------------------------------------------------------------- #
class HeadCam:
    """Keeps only the latest frame. A queue would be wrong here: an old frame is
    worse than no frame, because the head has moved since it was taken."""

    def __init__(self, index, width=1280, height=720):
        self.cap = cv2.VideoCapture(index)
        if width:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        # MJPG so a 720p+ UVC stream is not limited to a few fps over USB 2
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open camera {index} -- try --list-cams")
        self.latest, self.seq, self._stop = None, 0, False
        self.event_frames = deque(maxlen=40)  # ~4 seconds at 10 Hz, JPEG-compressed
        self._last_event_frame = 0.0
        threading.Thread(target=self._run, daemon=True).start()
        t0 = time.time()
        while self.latest is None:
            if time.time() - t0 > 8:
                raise RuntimeError(f"camera {index} opened but produced no "
                                   f"frames in 8s")
            time.sleep(0.05)
        h, w = self.latest.shape[:2]
        print(f"[cam] {index}: {w}x{h}")

    def _run(self):
        while not self._stop:
            ok, fr = self.cap.read()
            if ok:
                self.latest, self.seq = fr, self.seq + 1
                now = time.time()
                if now - self._last_event_frame >= 0.1:
                    ok_jpg, jpg = cv2.imencode(
                        ".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 82])
                    if ok_jpg:
                        self.event_frames.append((now, jpg.tobytes()))
                        self._last_event_frame = now
            else:
                time.sleep(0.05)

    def close(self):
        self._stop = True
        time.sleep(0.1)
        self.cap.release()


# --------------------------------------------------------------------------- #
# web UI: THE PLAN on the main page, the state machine on its own
# --------------------------------------------------------------------------- #
# This used to write state rows into `entries`/`status` -- attention_ui's plan
# slots -- so the main page showed the eight states and the watch-spec, relation
# ribbon, VLM "why" and detection tiers all disappeared. Wrong trade: the state
# machine is something I need while debugging, the plan is what the system is FOR
# and what a reader of the paper is being shown. It now goes to the ROBOT tab, and
# THE PLAN is published by plan_view.PlanView exactly as attention_system did.
#
# Worth preferring over the cv2 window during real sessions: it is viewable from
# another machine, so nobody has to stand over the laptop next to the robot, and
# the participant never sees a debug overlay. A screen of bounding boxes beside
# the robot changes what a participant believes the robot is, which is a validity
# problem rather than a cosmetic one.
def state_rows(snap, flow):
    """For the ROBOT tab. Small enough to be obviously not the main event."""
    out = []
    for name, spec in ST.STATES.items():
        out.append({"name": name, "on": name == snap["state"],
                    "screen": spec["screen"], "note": spec["note"][:70]})
    return out


def stdin_key():
    """A waiting keypress from the terminal, or 255. Needed because --no-view has
    no cv2 window to take keys from, and the researcher override must not depend
    on a preview window being open."""
    import select, termios, tty
    if not sys.stdin.isatty():
        return 255
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        if select.select([sys.stdin], [], [], 0)[0]:
            return ord(sys.stdin.read(1))
        return 255
    except Exception:
        return 255
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


import contextlib


@contextlib.contextmanager
def _quiet_fd2():
    """Swallow OpenCV's probing noise. It writes 'out device of bound' straight
    to fd 2 from C++, so Python-level redirection does not catch it, and the
    warnings drown the answer they are printed alongside."""
    try:
        old = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 2)
        os.close(devnull)
        yield
    finally:
        try:
            os.dup2(old, 2)
            os.close(old)
        except Exception:
            pass


def cam_verdict(w, h):
    """Which device is this, from the mode it agreed to.

    Resolution alone does not separate them -- both report 1920x1080 by default.
    What does separate them is what each one does when asked for 2560x1440:

      * the InnoMaker OV4688 module has that exact mode (and 2688x1520), so it
        complies and returns a 16:9 frame wider than 1920.
      * Apple silicon Macs have a SQUARE sensor for Center Stage cropping and
        answer with something like 1552x1552. Square is the giveaway, not size --
        1552 is taller than 1080, so a height test would get this backwards.
    """
    if abs(w - h) < 0.15 * max(w, h):
        return "the Mac's built-in (square sensor = Center Stage)"
    if w >= 2560:
        return "<-- HEAD CAM (InnoMaker 4MP, took the 2K mode)"
    return "unknown -- use the unplug test"


def list_cams(n=6):
    """Identify the head camera without guessing.

    Indices move whenever anything USB is replugged, so this is a per-session
    check, not a number to write down.
    """
    print("probing camera indices (asking each for 2560x1440)...")
    found = 0
    for i in range(n):
        with _quiet_fd2():
            c = cv2.VideoCapture(i)
            opened = c.isOpened()
            row = None
            if opened:
                ok, fr = c.read()
                if not ok:
                    row = (f"  {i}: opens but yields no frame (Continuity Camera "
                           f"or a virtual device -- ignore)")
                else:
                    native = f"{fr.shape[1]}x{fr.shape[0]}"
                    c.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                    c.set(cv2.CAP_PROP_FRAME_WIDTH, 2560)
                    c.set(cv2.CAP_PROP_FRAME_HEIGHT, 1440)
                    ok2, fr2 = c.read()
                    if ok2:
                        w, h = fr2.shape[1], fr2.shape[0]
                        row = (f"  {i}: native {native}, at 2K request -> {w}x{h}"
                               f"   {cam_verdict(w, h)}")
                    else:
                        row = f"  {i}: native {native}, 2K request failed"
            c.release()
        if not opened:
            if found:
                break            # past the last real device
            continue
        found += 1
        print(row)
    print("\nStill ambiguous? Unplug the head camera, run this again, and see "
          "which index disappears.")


# --------------------------------------------------------------------------- #
# perception hook -- the whole autonomous half lives behind this signature
# --------------------------------------------------------------------------- #
def perceive(frame, snap, ctx):
    """Called with a SETTLED frame. Return an EVENT NAME, or None.

    An event, not a state -- "finding", not "S7a". The rules for what an event
    means live in session_flow.py, which is the design contribution; perception
    reports, the flow decides. The events it may emit are the same vocabulary
    every other device uses:

        "finding"          a watch entry was satisfied  -> S7a, counter, story
        "tap"              the body was touched         -> S6 (already wired)
        "transcript:<txt>" (STT owns this one)

    Anything else is ignored with a log line rather than crashing the session.

    Deliberately a stub. Returning None forever is a valid, running system: the
    researcher drives, the motion library gets exercised, the study can happen.
    That property is the point of putting perception behind one function --
    integrating the detector/gaze/judge stack becomes a change in one place
    instead of a rewrite of the loop.

    `snap` is the player's status (state, settled_ms, loops). `ctx` is a dict
    that survives across calls; use it for streaks, cooldowns, captures.

    Where the existing modules plug in:
        from perception.perceive import make_detector          # object slots
        from perception.gaze import HeadPoseEstimator, gazing_at, joint_attention
        from planning.judge import judge, ReportabilityTaste
    S4 is the state that wants frames: one per station, and settled_ms already
    isolates the dwells. Score them, remember the best, and the clip's own
    return-to-richest is where that choice becomes visible.
    """
    ctx["frames_seen"] = ctx.get("frames_seen", 0) + 1
    if snap["state"] == "S4_PLAN" and snap["settled_ms"] > 400:
        ctx.setdefault("stations", []).append(snap["pose"]["pan"])
    return None


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", type=int, default=0)
    ap.add_argument("--cores3", nargs="?", const="auto",
                    help="attach the CoreS3. Bare --cores3 auto-detects it.")
    ap.add_argument("--port", default=os.environ.get("NOTICEBOT_PORT"))
    ap.add_argument("--baud", type=int,
                    default=int(os.environ.get("NOTICEBOT_BAUD", 1_000_000)))
    ap.add_argument("--clips", default=os.path.join(ROOT, "motion", "clips"),
                    help="the exported motion CSVs. These are BUILD ARTEFACTS: "
                         "to change a movement, edit the .blend in motion/src "
                         "and re-export -- never hand-edit a CSV.")
    ap.add_argument("--start", default="S1_IDLE")
    ap.add_argument("--no-view", action="store_true", help="no preview window")
    ap.add_argument("--serve", action="store_true",
                    help="reuse attention_ui.py's web UI: live stream + panel + "
                         "the context box, viewable from another machine")
    ap.add_argument("--web-port", type=int, default=8000)
    ap.add_argument("--feed-dir",
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "session_feed"),
                    help="where findings and sweeps are written, and where the "
                         "web UI reads them from. Absolute by default and next "
                         "to this file, not relative to the shell's cwd -- a feed "
                         "that lands in a different folder each time you launch "
                         "from somewhere else is a session you cannot find later.")
    ap.add_argument("--list-cams", action="store_true")
    ap.add_argument("--no-stt", action="store_true",
                    help="skip Whisper; the web UI text box is then the only "
                         "transcript source. The flow is identical either way.")
    ap.add_argument("--no-cam", action="store_true", help="servos + CoreS3 only")
    # ---- the VLM compiler + CV layer (same defaults as attention_system.py) ----
    ap.add_argument("--no-cv", action="store_true",
                    help="no detector/pose/relations. THE PLAN panel stays empty "
                         "and findings come only from the 'f' key.")
    ap.add_argument("--detector", default="gdino",
                    choices=["yolo", "yoloworld", "gdino", "mock"])
    ap.add_argument("--vocab", default="person,laptop,chair,cup,bottle,book,"
                                       "cell phone,backpack,keyboard,mouse")
    ap.add_argument("--conf", type=float, default=0.3)
    ap.add_argument("--cv-hz", type=float,
                    default=float(os.environ.get("NOTICEBOT_CV_HZ", DEFAULT_PERCEIVE_HZ)),
                    help="perception samples per second (default 1 for Grounding DINO; "
                         "try 4 with YOLO-World)")
    ap.add_argument("--persist", type=int, default=2,
                    help="frames a relation must hold before it counts")
    ap.add_argument("--cooldown", type=float, default=60.0,
                    help="seconds before the same entry can fire again")
    ap.add_argument("--tau-gap", type=float, default=3.0,
                    help="THEN-gate: max seconds between ordered relations")
    # ---- storyboard (same defaults as attention_system.py) ----
    ap.add_argument("--burst-n", type=int, default=10,
                    help="max panels in one story strip")
    ap.add_argument("--burst-interval", type=float, default=4.0,
                    help="min seconds between panels")
    ap.add_argument("--linger", type=float, default=6.0,
                    help="keep shooting this long after the relation drops, so "
                         "the follow-through is recorded too")
    ap.add_argument("--scene-diff", type=float, default=8.0,
                    help="mean pixel change that counts as a new keyframe when "
                         "the truth vector did not change")
    ap.add_argument("--offline", action="store_true",
                    help="no Gemini calls; use deterministic planner/judge results")
    ap.add_argument("--feedback", choices=["console", "robot"], default="console",
                    help="confirmed-event output; console is the safe default and "
                         "does not enter S7 or fire CoreS3 feedback")
    a = ap.parse_args()

    if a.cv_hz <= 0:
        ap.error("--cv-hz must be greater than zero")
    if a.offline:
        os.environ["SECONDATTN_OFFLINE"] = "1"

    if a.list_cams:
        list_cams()
        return

    # Probed, not configured. macOS renames usbmodem devices by USB location id,
    # so a stored path breaks whenever anything is replugged -- and it breaks as
    # "the robot is dead", not as "wrong port".
    try:
        bus, port = open_bus(a.port, a.baud)
    except IOError as e:
        sys.exit(str(e))
    missing = [n for n, sid in IDS.items() if not bus.ping(sid)[1]]
    if missing:
        sys.exit(f"no reply from {missing} on {port}")

    # link is created below, so the LED callback resolves it late
    holder = {}

    # Counted, and reported at exit. "The LED did not change" has five different
    # causes and they are indistinguishable by looking at the robot; a number
    # splits them in half immediately. Zero sent = the laptop side (no --cores3,
    # or every clip's led column is flat). Thousands sent and still nothing =
    # the board side (firmware not reflashed, wrong port, or the Grove lead).
    counts = {"led": 0, "sfx": 0}

    def on_led(level):
        if holder.get("link"):
            counts["led"] += 1
            holder["link"].led(level)

    def on_sfx(name):
        if holder.get("link"):
            counts["sfx"] += 1
            holder["link"].sfx(name)

    player = ClipPlayer(bus, a.clips, on_led=on_led, on_sfx=on_sfx)
    cam = None if a.no_cam else HeadCam(a.cam)

    # ---- CoreS3: participant-facing I/O. Optional so a servo-only run works.
    link = None
    if a.cores3:
        from session.cores3_link import CoreS3Link, find_cores3
        if a.cores3 == "auto":
            a.cores3 = find_cores3(exclude=(port,))
            if not a.cores3:
                sys.exit("no CoreS3 found (it greets with 'cores3_sidekick' on "
                         "boot; flashed and plugged in?)")
        # A hand-typed --cores3 pointing at the SERVO adapter opens fine, accepts
        # every write and lights nothing, with no error anywhere -- macOS names
        # both usbmodem-<location id>, so they are easy to swap. --led-test has
        # guarded this for a while; the main path had not.
        elif port and os.path.realpath(a.cores3) == os.path.realpath(port):
            sys.exit(f"--cores3 {a.cores3} is the SERVO bus, not the CoreS3. "
                     f"Both are named usbmodem-<id>; use --cores3 on its own to "
                     f"auto-detect by PING instead of guessing.")
        pending = []

        def on_input(line):
            pending.append(line)
            print(f"[cores3] {line}")

        link = CoreS3Link(a.cores3, on_input=on_input)
        holder["link"] = link
        link.event("UI", "idle")     # the board boots to idle; this re-syncs on reconnect
        link.event("NOTICED", 0)
    else:
        pending = []

    def announce(state):
        if link:
            spec = ST.STATES[state]
            link.hue(spec["hue"])           # colour = state; level = clip
            # ONE command, and it owns exactly one channel. The v1 EVT STEP and
            # EVT MODE that used to be sent from here are gone from the firmware:
            # each set the antenna colour or the status text as a side effect, so
            # STEP repainted S6's red negation blue and MODE painted the old
            # three-button EAGER screen over the participant UI. The screen is
            # owned by the ("ui", ...) events out of session_flow; the antenna
            # LEVEL is owned by the clip player. Nothing arbitrates here.
            # Sound is fired by the player from states.py `sfx`, not here: the
            # S4 shutter has to come off the clip's own LED edge, and having two
            # places that make sounds is how you end up with two of them.
            # Sound is fired by the player from states.py `sfx`, not here: the
            # S4 shutter has to come off the clip's own LED edge, and having two
            # places that make sounds is how you end up with two of them.

    UI = None
    if a.serve:
        from webui import server as attention_ui
        UI = attention_ui.serve(argparse.Namespace(web_port=a.web_port,
                                                   feed_dir=a.feed_dir))

    # The VLM compiler + CV layer. Built BEFORE the session starts, never during:
    # it imports mediapipe and loads a detector checkpoint, which is seconds of
    # stall, and it must not be seconds that a participant spends watching a
    # motionless robot.
    view = None
    if not a.no_cv:
        try:
            from planning.plan_view import PlanView
            print("[cv] loading detector + pose estimator ...")
            view = PlanView(detector=a.detector,
                            vocab=[v.strip() for v in a.vocab.split(",")],
                            conf=a.conf, persist=a.persist,
                            cooldown=a.cooldown, tau_gap=a.tau_gap)
            print(f"[cv] ready ({a.detector})")
        except Exception as e:
            # Said loudly, and the session still runs: the motion machine and the
            # researcher's keys do not depend on perception, and finding out at
            # this point that mediapipe is unhappy should not cost a booked slot.
            print(f"[cv] UNAVAILABLE: {e}\n"
                  f"[cv] running without perception -- 'f' is the finding key")

    print("\nkeys: 1-8 = force a state, c = cycle, r = relax, q = quit")
    for k, v in ST.KEYS.items():
        print(f"  {k} -> {v:<12} {ST.STATES[v]['note'][:60]}")

    player.start()
    player.request(a.start)
    announce(a.start)

    # ---------------------------------------------------------------- #
    # flow -> devices. One place, so a rule change cannot half-apply.
    # ---------------------------------------------------------------- #
    flow = SessionFlow()
    transcripts, ui_events = [], []

    def on_transcript(text, source):
        # The source is carried, not flattened. Both go through the same
        # acceptance rule in session_flow, but a researcher's typo must not send
        # the robot into its error performance -- see the comment there.
        #
        # Published to the page HERE rather than after the plan is accepted: the
        # moment you most need to read what Whisper heard is the moment it was
        # rejected and the robot went to S8, and at that point no plan ever runs.
        if view is not None:
            ok, why = transcript_usable(text)
            view.transcript = (text if ok
                               else f"{text or '(silence)'}   [rejected: {why}]")
        transcripts.append(("typed" if source == "manual" else "transcript", text))

    stt = STT(on_transcript, enabled=not a.no_stt)

    def act(kind, val):
        if kind == "state":
            player.request(val)
        elif kind == "ui":
            if link:
                link.event("UI", val)
        elif kind == "noticed":
            if link:
                link.event("NOTICED", int(val))
            # EVERY finding opens a story, whatever produced it. This hangs off
            # the flow's `noticed` emission rather than off the detector, because
            # a finding has three possible sources -- a watch entry firing, the
            # researcher's `f`, a forced state -- and hanging the recording off
            # only the first one is why the counter on the board read 3 while the
            # NOTICED feed read 0. The board and the feed were counting different
            # events. One door: if the flow says something was noticed, it gets
            # recorded.
            if story is not None:
                # Deliberately NOT conditional on `view`: if the detector failed
                # to load, a researcher-forced finding is the only kind there is,
                # and that is exactly the run where losing the record hurts most.
                truth = view.truth if view is not None else {}
                viz = view.viz if view is not None else {}
                e = ctxd.pop("entry", None)
                fr = ctxd.get("frame")
                if fr is None and cam is not None:
                    fr = cam.latest
                if fr is None:
                    print("[story] no frame yet -- nothing to record")
                else:
                    if e is None:            # manual: not a spec entry
                        e = {"label": f"noticed #{int(val)} (researcher)"}
                        idx = -1
                    else:
                        try:
                            idx = view.entries().index(e)
                        except (ValueError, AttributeError):
                            idx = -1
                    # The RAW frame goes in the panel, not the annotated one: the
                    # strip is a record of what happened in the room and will be
                    # read by people who are not debugging a detector.
                    story.open(e, fr, truth, viz, idx)
        elif kind == "rec":
            if val == "start":
                stt.start()
            else:
                stt.stop_and_transcribe()
        elif kind == "plan":
            # The request now EXISTS as text -- so show it immediately and arm the
            # sweep. The VLM call itself waits for S4 to finish collecting.
            # Publishing here rather than after the plan is deliberate: "I heard
            # you." and the sentence on the page are the same claim, and the page
            # should not be the last to know what the robot just acknowledged.
            ctxd["request"] = val
            ctxd["plan_generation"] = int(ctxd.get("plan_generation", 0)) + 1
            if view is not None:
                view.transcript = val
                view.context = val
            sweep.begin()
        elif kind == "pan":
            # Set the aim and stop. Do NOT also announce "arrived:S5_TRACK" here:
            # S6's shake may still be playing, and claiming the watching state
            # early would let a finding be accepted while the head is still
            # saying "not that one". The loop already reports the player's real
            # state change, which is the only honest source for it.
            player.set_pan_deg(float(val))
        elif kind == "idle":
            # STOP means STOP. The task is discarded, so the watching must end
            # too: the executor is torn down, the detector whitelist is released
            # and THE PLAN empties. Previously only the robot went back to idle
            # while perception carried on firing against the old spec -- the page
            # kept updating, entries kept satisfying, and findings could still be
            # recorded for a task the participant had cancelled.
            sweep.active = False
            ctxd["plan_generation"] = int(ctxd.get("plan_generation", 0)) + 1
            if view is not None:
                view.executor, view.spec = None, None
                view.context, view.transcript = "", ""
                view.statuses, view.suppressed = [], []
                view.apply_relevance(None)
            print("[flow] watching stopped -- plan cleared")
        elif kind == "pan_next":
            deg, sc = next_best_pan(player.snapshot()["pose_deg"]["pan"])
            if deg is None:
                print("[aim] no other angle available -- holding")
            else:
                print(f"[aim] nobody answered; moving to pan {deg:+.0f}"
                      + (f" (score {sc})" if sc is not None else ""))
                player.set_pan_deg(deg)
        elif kind == "log":
            print(f"[flow] {val}")

    def run_planner(request, generation):
        """Compile a request into a watch-spec and INSTALL it in the view.

        Runs at the END of S4, not on the S2->S3 edge, because S4 IS the planning:
        the head sweeps its stations collecting one pure frame each, and those
        frames -- sent as independently labelled images -- are what Gemini reads. One
        call over the whole room beats one call per frame, and it is the only way
        the enumeration can cover angles the robot is not currently facing.
        """
        ctxd["request"] = request
        try:
            from planning.planner import plan

            def plan_fn(context, jpegs):
                r = plan(context, jpegs)
                if r.get("spec") is None or r.get("violations"):
                    print("[planner] invalid result; retrying once ...")
                    r = plan(context, jpegs)
                return r

            res, meta = sweep.plan(request, plan_fn, UI)
            v = (res or {}).get("violations") or []
            if generation != ctxd.get("plan_generation"):
                print("[planner] stale result discarded")
                return
            if (res or {}).get("spec") is None or v:
                raise ValueError("invalid Gemini plan: " + "; ".join(map(str, v)))
            ctxd["spec"] = res["spec"]
            print(f"[planner] {len(v)} violation(s); "
                  f"watch={len((ctxd['spec'] or {}).get('watch', []) or [])} entries")
            if v:
                print("  " + "; ".join(map(str, v)))
            if view is not None:
                view.set_plan(ctxd["spec"], request)
            ui_events.append("planned")
            if meta:
                # NOW move: the VLM has chosen. Until this line the head was
                # holding the last angle it swept, and the screen still said
                # "planning...". This is the handover -- the VLM picks where to
                # look, CV takes over there and stays.
                print(f"[aim] richest pan {meta['richest_pan']:+d} "
                      f"(score {meta['richest_score']}) -- taking over there")
                player.set_pan_deg(float(meta["richest_pan"]))
                # DID THE TARGET CHANGE? That is the whole question S5a exists
                # to answer, and only the planner can answer it -- the sweep is
                # additive, so most re-plans re-choose what was already being
                # watched. Changed: arm S5A_SETTLE, and the crane onto the thing
                # is an AUTHORED BEAT ("I have come to this one"). Unchanged: say
                # nothing, S4's `then` falls through to S5B, and the same crane
                # is an ordinary transition carrying no claim.
                #
                # Same joints, same endpoints, two different claims -- and what
                # separates them is only whether anybody authored the move. See
                # S4_S5_DESIGN.md sec 3.05.
                new_pan = float(meta["richest_pan"])
                prev = ctxd.get("aimed_pan")
                if prev is None or abs(new_pan - prev) > ST.AIM_CHANGED_DEG:
                    player.arm_next("S5A_SETTLE")
                    print(f"[aim] target CHANGED "
                          f"({'first' if prev is None else f'{prev:+.0f}'}"
                          f" -> {new_pan:+.0f}) -- S5a will announce the arrival")
                ctxd["aimed_pan"] = new_pan
        except Exception as e:
            # A failed plan must NOT strand the robot: S4/S5 still run, the
            # researcher can see the failure in the UI and retype or stop.
            print(f"[planner] failed: {e}")
            if view is not None:
                view.plan_error = f"planner failed: {e}"

    # A finding opens a STORY, not a screenshot. See storyboard.py -- the burst
    # keeps shooting while the moment unfolds, keyframed on truth-vector change,
    # and the caption is narrated from what the CV actually detected per panel.
    story = None
    if not a.no_cv:
        try:
            from session.storyboard import Storyboard
            story = Storyboard(feed_dir=a.feed_dir, ui=UI, burst_n=a.burst_n,
                               interval=a.burst_interval, linger=a.linger,
                               scene_diff=a.scene_diff, offline=a.offline)
        except Exception as e:
            print(f"[story] unavailable: {e}")

    def next_best_pan(current):
        """The next-best angle the sweep scored, excluding where it is now.

        Uses the sweep's own per-station scores (focus objects weigh 3x) rather
        than a random pick: "not that one" deserves the second-best guess, not a
        coin flip. Falls back to any other station if there are no scores yet,
        which at least guarantees the view CHANGES -- answering a rejection with
        the identical picture is the one response that is certainly wrong.
        """
        meta = sweep.last
        if meta and meta.get("shots"):
            ranked = sorted(
                ({"pan": sh["pan"],
                  "score": sum(3 if d["tier"] == "focus" else 1 for d in sh["dets"])}
                 for sh in meta["shots"]),
                key=lambda r: -r["score"])
            for r in ranked:
                if current is None or abs(r["pan"] - current) >= 8:
                    return float(r["pan"]), r["score"]
        others = [p for p in PAN_KEYS.values()
                  if current is None or abs(p - current) >= 8]
        if not others:
            return None, None
        import random
        return random.choice(others), None

    ctxd = {}
    ctx, last_state, last_perceive, last_pub, last_level = {}, None, 0.0, 0.0, 0.0
    shown = None            # the last frame the overlay was computed FROM
    event_frames = cam.event_frames if cam is not None else deque(maxlen=40)
    event_candidates = []                 # wait through t+1.0 before Gemini confirm
    confirmed_findings = []               # worker -> main-loop handoff
    from planning.sweep_plan import Sweep
    sweep = Sweep(feed_dir=a.feed_dir)
    os.makedirs(a.feed_dir, exist_ok=True)
    stt.warm()              # load Whisper now, not under the first participant

    def event_jpegs(onset):
        """Five nearest raw frames at -1,-.5,0,+.5,+1 seconds."""
        return select_temporal_frames(event_frames, onset)

    def confirm_candidate(candidate):
        from planning.judge import ReportabilityTaste, judge
        taste = story.taste if story is not None else ReportabilityTaste()
        entry = candidate["entry"]
        claim = entry.get("label", "requested event")
        if entry.get("on"):
            claim += f" on {entry['on']}"
        result = judge(candidate["images"], None, taste, confirm=claim)
        confirmed_findings.append((candidate, result))

    try:
        while True:
            snap = player.snapshot()

            # the browser's context box is a free remote input: type what the
            # participant asked and the designed cycle starts. Doubles as the
            # wizard channel when perception is not driving.
            if UI is not None:
                with UI.LOCK:
                    sentence = UI.STATE.get("pending_context")
                    UI.STATE["pending_context"] = None
                with UI.LOCK:
                    pan_req = UI.STATE.get("pending_pan")
                    UI.STATE["pending_pan"] = None
                if pan_req:
                    if str(pan_req).strip().lower() == "next":
                        deg, sc = next_best_pan(snap["pose_deg"]["pan"])
                        if deg is None:
                            print("[aim] nowhere else to look")
                        else:
                            print(f"[aim] next best: pan {deg:+.0f}"
                                  + (f" (score {sc})" if sc is not None else ""))
                            ui_events.append(f"reaim:{deg}")
                    else:
                        ui_events.append(f"reaim:{float(pan_req)}")
                if sentence:
                    # Two commands share this box, because adding UI to
                    # attention_ui.py for the second one is not worth a fork of a
                    # file that works.
                    low = sentence.strip().lower()
                    if low.startswith("pan "):
                        ui_events.append("reaim:" + low.split()[1])
                    else:
                        # SAME door as Whisper: identical usability rules,
                        # identical path through the state machine. The manual
                        # path is used exactly when a session is already going
                        # badly, so it must not be a second, subtly different
                        # route.
                        print(f"[ui] typed transcript: {sentence!r}")
                        stt.manual(sentence)
            if snap["state"] != last_state and snap["state"]:
                announce(snap["state"])
                # AND tell the flow, which is how the SCREEN follows the robot.
                # This was missing, so the screen only ever changed on events the
                # flow itself caused: S3 set "I heard you.", then the player ran
                # its own `then` chain S3 -> S4 -> S5 with nobody reporting it,
                # and the board still read "I heard you." through the whole scan
                # and the entire watch. announce() only ever set the LED colour --
                # which is exactly why it looked like a screen bug and not a
                # missing event.
                ui_events.append("arrived:" + snap["state"])
                if last_state == "S4_PLAN" and sweep.active:
                    # left S4 -> the sweep is complete. Plan off-thread: the call
                    # is seconds and the loop still owes the LED a heartbeat.
                    threading.Thread(target=run_planner,
                                     args=(ctxd.get("request", ""),
                                           ctxd.get("plan_generation", 0)),
                                     daemon=True).start()
                last_state = snap["state"]

            # The recording bar is the only live confirmation a participant gets
            # that the robot is hearing them, and nothing was sending it -- so it
            # sat at zero and read as frozen even when the mic was working. 10 Hz
            # is plenty; the firmware repaints only the bar's own rectangle.
            if link and flow.screen == "recording" and time.time() - last_level > 0.1:
                last_level = time.time()
                link.level(stt.level)

            # ---- every device is an event source; SessionFlow owns the rules
            events = []
            while confirmed_findings:
                candidate, decision = confirmed_findings.pop(0)
                if candidate["generation"] != ctxd.get("plan_generation"):
                    print("[confirm] stale candidate discarded")
                    continue
                if not decision.get("confirmed"):
                    print(f"[confirm] rejected: {decision.get('note', '')}")
                    continue
                feedback = decision.get("feedback") or decision.get("note") or candidate["entry"].get("label")
                print(f"[FEEDBACK] {feedback}")
                if a.feedback == "robot":
                    ctxd["entry"] = candidate["entry"]
                    ctxd["frame"] = candidate["frame"]
                    ui_events.append("finding")
            while pending:
                line = pending.pop(0)
                if "PTT_DOWN" in line:      events.append("ptt_down")
                elif "PTT_UP" in line:      events.append("ptt_up")
                elif "IN OK" in line:       events.append("ok")
                elif "IN STOP" in line:     events.append("stop")
                elif "BODYTAP" in line:     events.append("tap")
            while transcripts:
                kind, text = transcripts.pop(0)
                events.append(f"{kind}:{text}")
            while ui_events:
                events.append(ui_events.pop(0))
            flow.stt_busy = stt.busy
            events.append("tick")

            for ev in events:
                for kind, val in flow.feed(ev):
                    act(kind, val)

            # ---- perception
            # WHO LOOKS WHEN. During S4 the VLM is the only eye: the sweep takes
            # PURE frames, no detector and nothing drawn, because annotating a
            # frame before the model reads it feeds our guesses back as its
            # judgement. CV starts once there is a spec to watch, i.e. from S5 on.
            frame = None
            fired = []
            # From states.py, NOT a literal: this list had "S5_TRACK" in it,
            # which is the CLIP name. The state is S5B_TRACK, so the test was
            # false forever and CV never ran in the state whose whole job is
            # watching. Nothing raised -- a wrong string is just never equal.
            watching = snap["state"] in ST.WATCHING
            if not watching:
                shown = None          # do not carry a watch overlay into the sweep
            if cam is not None:
                frame = cam.latest
                now = time.time()
                settled = snap["settled_ms"] > SETTLE_MS
                if frame is not None and settled and snap["state"] == "S4_PLAN":
                    if sweep.offer(frame, snap["pose_deg"]["pan"]):
                        # Arm S5 to hold RIGHT HERE. Until the VLM answers, the
                        # last angle swept is the best available guess, and it is
                        # honest: the robot stays where it stopped looking rather
                        # than swinging to a hold pose it has no reason to prefer.
                        player.arm_pan_deg(snap["pose_deg"]["pan"])
                if (frame is not None and settled and watching
                        and now - last_perceive > 1.0 / a.cv_hz):
                    last_perceive = now
                    ctx["frames_seen"] = ctx.get("frames_seen", 0) + 1
                    if view is not None:
                        fired = view.step(frame, now)
                        # Keep the frame the overlay was COMPUTED FROM. Drawing a
                        # A sampled skeleton onto a faster raw stream is what made the
                        # skeleton lag behind the person -- the boxes were never
                        # late, they were just painted on somebody else's frame.
                        shown = view.draw(frame.copy())
                    # An EVENT into the flow, never player.request(). Perception
                    # reports what it saw; SessionFlow decides what that means.
                    # Requesting a state here bypassed the flow entirely: its
                    # self.state would silently disagree with the player's, the
                    # noticed counter would not increment, the CoreS3 screen would
                    # not change and no story would be opened -- a finding that
                    # moved the robot and left no record. This is also the exact
                    # boundary the perception work has to respect, so the loop
                    # should not be demonstrating the violation.
                    ev = perceive(frame, snap, ctx)
                    if ev:
                        ui_events.append(ev)

            # CV only proposes a candidate. Gemini sees ordered raw frames before
            # anything is reported or allowed to enter the S7 feedback motion.
            for e in fired:
                print(f"[watch] candidate: {e.get('label')}")
                event_candidates.append({
                    "entry": dict(e), "onset": now, "due": now + 1.0,
                    "generation": ctxd.get("plan_generation"),
                    "frame": frame.copy() if frame is not None else None,
                })

            for candidate in list(event_candidates):
                if candidate["generation"] != ctxd.get("plan_generation"):
                    event_candidates.remove(candidate)
                    print("[confirm] cancelled stale candidate before API call")
                    continue
                if now < candidate["due"]:
                    continue
                event_candidates.remove(candidate)
                candidate["images"] = event_jpegs(candidate["onset"])
                if len(candidate["images"]) != 5:
                    print("[confirm] skipped: five event frames unavailable")
                    continue
                threading.Thread(target=confirm_candidate, args=(candidate,),
                                 daemon=True).start()

            # Open bursts advance whenever there is a frame and an open burst --
            # NOT only while watching. A story that stops collecting because the
            # robot changed state loses its follow-through, which is the half that
            # says what the moment turned into.
            if story is not None and frame is not None:
                ctxd["frame"] = frame
                story.step(frame,
                           view.truth if view else {}, view.viz if view else {},
                           view.statuses if view else [])

            # ---- the live view
            # While watching, show the LAST ANNOTATED frame -- the one the overlay
            # was actually computed from, held until the next perception tick. It
            # is up to 250 ms old, and that is the honest choice: a fresh frame
            # wearing a stale skeleton looks like the CV is wrong, when what is
            # wrong is the pairing. Outside watching, the raw stream, undecorated,
            # because that is exactly what the VLM is being given.
            v = None
            if frame is not None and (not a.no_view or UI is not None):
                still = snap["settled_ms"] > SETTLE_MS
                head = (f"{snap['state']}  pan {snap['pose_deg']['pan']:+.0f}  "
                        + ("STILL" if still else "MOVING")
                        + ("" if watching else "   [VLM sees the raw frame]"))
                v = (shown if (watching and shown is not None) else frame).copy()
                col = (0, 220, 0) if still else (0, 160, 255)
                cv2.putText(v, head, (12, 30), cv2.FONT_HERSHEY_SIMPLEX,
                            0.7, (0, 0, 0), 4, cv2.LINE_AA)
                cv2.putText(v, head, (12, 30), cv2.FONT_HERSHEY_SIMPLEX,
                            0.7, col, 2, cv2.LINE_AA)
            if UI is not None and time.time() - last_pub > 1 / 12.0:
                last_pub = time.time()
                jpg = (cv2.imencode(".jpg", v)[1].tobytes()
                       if v is not None else None)
                rows = state_rows(snap, flow)
                if UI is not None:
                    with UI.LOCK:
                        UI.STATE["pan_now"] = round(snap["pose_deg"]["pan"], 1)
                        UI.STATE["pan_scores"] = [
                            {"pan": sh["pan"],
                             "score": sum(3 if d["tier"] == "focus" else 1
                                          for d in sh["dets"])}
                            for sh in ((sweep.last or {}).get("shots") or [])]
                if view is not None:
                    view.publish(UI, jpg, states=rows,
                                 collecting=(story.collecting() if story else []))
                else:
                    with UI.LOCK:
                        if jpg is not None:
                            UI.STATE["jpg"] = jpg
                        UI.STATE["states"] = rows

            if v is not None and not a.no_view:
                cv2.imshow("noticebot", v)
                k = cv2.waitKey(20) & 0xFF
            else:
                time.sleep(0.02)
                k = 255

            if k == 255 and a.no_view:
                k = stdin_key()          # headless still needs the override

            if k == ord("q"):
                break
            elif k < 128 and chr(k) in ST.KEYS:
                player.request(ST.KEYS[chr(k)])
            elif k < 128 and chr(k) in PAN_KEYS:
                ui_events.append(f"reaim:{PAN_KEYS[chr(k)]}")
            elif k == ord("f"):
                ui_events.append("finding")     # stand-in until the judge is wired
            elif k == ord("g"):
                player.request("S2_LISTEN")      # enters the designed cycle
            elif k == ord("r"):
                for sid in IDS.values():
                    bus.torque(sid, False)
                print("[loop] torque off -- press a state key to re-engage")
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        print(f"[loop] frames offered to perception: {ctx.get('frames_seen', 0)}")
        print(f"[loop] LED updates sent: {counts['led']}, sounds: {counts['sfx']}")
        if link and counts["led"] == 0:
            print("[loop] !! ZERO LED updates -- the LAPTOP side. Either every "
                  "clip's led column is flat (re-export: the generators write "
                  "the envelope, export_clip samples it) or on_led never fired.")
        elif not link:
            print("[loop] no --cores3, so the antenna ran the board's own "
                  "fallback breath the whole time. That is what 'the LED did "
                  "not change' usually means.")
        player.stop()
        if cam:
            cam.close()
        if link:
            link.rest()      # never leave a participant with the alarm flashing
            time.sleep(0.3)
            link.close()
        bus.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
