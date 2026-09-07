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
import argparse, datetime as dt, json, math, os, sys, threading, time
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
from perception.watch_exec import order_coincident_candidates
# Module level: the loop names the provider in five places, all inside
# functions. A nested import satisfies neither them nor pyflakes.
from planning.provider import _ensure_env, provider_name

# The five S4 stations, as keyboard stand-ins for the web UI's pan buttons. Used
# in S6 to re-aim: the tap said "wrong direction", not "wrong task", so only the
# direction changes and the watch-spec survives.
PAN_KEYS = {"z": 60.0, "x": 30.0, "c": 0.0, "v": -30.0, "b": -60.0}

# How long the main loop may go without an iteration before the watchdog
# assumes it is wedged and dumps the stacks. Generously above the slowest
# legitimate tick -- a perception step with YOLO-World plus BlazePose runs a few
# hundred ms, and a `_goto` across the pan range sleeps up to ~1 s -- so this is
# not a latency alarm. It answers one question only: is it still running.
STALL_WARN_S = 6.0

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
        # ~7 s at 10 Hz, JPEG-compressed. The judge reaches back 4 s
        # (EVENT_OFFSETS_S) and the deque has to outlive that with room to
        # spare, or the oldest offset resolves to whatever happens to still be
        # in it -- silently, since nearest-match always returns something.
        self.event_frames = deque(maxlen=70)
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
    ap.add_argument("--feed-dir", default=None,
                    help="directory for this run's frames, sweeps, and LLM audit. "
                         "By default a new session_feed/e2e_<timestamp> directory "
                         "is created for every process start.")
    ap.add_argument("--list-cams", action="store_true")
    ap.add_argument("--no-stt", action="store_true",
                    help="skip Whisper; the web UI text box is then the only "
                         "transcript source. The flow is identical either way.")
    ap.add_argument("--no-cam", action="store_true", help="servos + CoreS3 only")
    # ---- the VLM compiler + CV layer (same defaults as attention_system.py) ----
    ap.add_argument("--no-cv", action="store_true",
                    help="no detector/pose/relations. THE PLAN panel stays empty "
                         "and findings come only from the 'f' key.")
    # yoloworld, not gdino. Settled: ~4 Hz against gdino's ~1 Hz on the study
    # laptop, and it goes through ultralytics instead of transformers -- whose
    # version is unpinned and whose tokenizer failed with a TextEncodeInput error
    # that mentions neither vocabularies nor versions. gdino stays selectable so
    # old logs remain reproducible; it is not the path anything is run on.
    ap.add_argument("--detector", default="yoloworld",
                    choices=["yolo", "yoloworld", "gdino", "mock"])
    ap.add_argument("--vocab", default="person,laptop,chair,cup,bottle,book,"
                                       "cell phone,backpack,keyboard,mouse")
    ap.add_argument("--conf", type=float, default=0.3)
    ap.add_argument("--judge-deadline", type=float, default=None,
                    help=f"seconds to wait for the group judge before reporting "
                         f"anyway (default {ST.JUDGE_DEADLINE_S:.0f}). 0 = never "
                         f"wait: the CV gate alone decides when S7 plays, which "
                         f"removes the ~50/50 gated/ungated split the deadline "
                         f"introduces. The judge still RUNS and its verdict is "
                         f"still recorded, so the disagreement rate is measured "
                         f"either way -- it just stops being in the critical path.")
    ap.add_argument("--synonym-prompt", dest="synonym_prompt",
                    action=argparse.BooleanOptionalAction, default=True,
                    help="ask an open-vocab detector for every name of an object "
                         "('phone' also asks for 'cell phone', 'smartphone', ...). "
                         "More names should mean more frames in which a small "
                         "half-occluded object is found -- UNMEASURED on this rig, "
                         "so it is a flag. --no-synonym-prompt asks only for the "
                         "words the plan used. Compare with grep '[relevance]'.")
    ap.add_argument("--cv-hz", type=float,
                    default=float(os.environ.get("NOTICEBOT_CV_HZ", DEFAULT_PERCEIVE_HZ)),
                    help="perception samples per second (default 1 for Grounding DINO; "
                         "try 4 with YOLO-World)")
    # WAS 2. For relation 9 it was a second copy of a rule already enforced:
    # `sustain_s` requires the contact to HOLD, and `persist` then required the
    # result of that to hold again. Together with sustain=1.0 at --cv-hz 4 the
    # gate needed 1.5 s of unbroken contact before a story could even open, on
    # top of the reach that had to land in the box in the first place. The other
    # relations that need debouncing carry their own (`gathering` votes over a
    # 1.5 s window; approach and hands-on are time-based), so this layer was
    # mostly paying for itself twice. Raise it back with --persist if a run turns
    # out to be twitchy.
    ap.add_argument("--persist", type=int, default=1,
                    help="frames a relation must hold before it counts")
    ap.add_argument("--cooldown", type=float, default=15.0,
                    help="seconds before the same entry can fire again")
    ap.add_argument("--name", default="",
                    help="what the participant called it (ASCII, <=16). Normally "
                         "typed in the web UI when they choose it; this is for a "
                         "restart mid-session, so a board reset does not lose it.")
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

    # One feed directory is one auditable run.  Sweeps and stories already live
    # here; keep the model inputs/outputs beside them so an E2E result can be
    # reproduced without reconstructing evidence from terminal scrollback.
    if a.feed_dir is None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        a.feed_dir = os.path.join(ROOT, "session_feed", f"e2e_{stamp}")
        suffix = 1
        base = a.feed_dir
        while os.path.exists(a.feed_dir):
            a.feed_dir = f"{base}_{suffix:02d}"
            suffix += 1
    a.feed_dir = os.path.abspath(a.feed_dir)
    os.makedirs(a.feed_dir, exist_ok=True)
    # The tablet beside the robot is pointed at session_feed/latest.html once and
    # never again; this is what tells that page which run is current. Written
    # before anything else can fail, so the address is live even for a session
    # that goes on to produce nothing.
    try:
        from session.review import write_latest
        write_latest(a.feed_dir)
    except Exception as exc:
        print(f"[feed] latest.html not written: {str(exc)[:80]}")
    audit_dir = os.path.join(a.feed_dir, "llm")
    os.makedirs(audit_dir, exist_ok=True)
    audit_lock = threading.Lock()
    # READ .env BEFORE RECORDING WHAT WAS SENT.
    #
    # `_ensure_env` is lazy -- it runs on the first provider_name() -- and the
    # block below reads os.environ directly, so run.json was written from the
    # DEFAULTS while the calls themselves, made later, used .env. Found
    # 2026-08-17: .env said service_tier=standard and media_resolution=medium,
    # run.json for the same session said priority and None. The audit record was
    # describing a request nobody made, which is precisely what the comment
    # below was written to stop it doing.
    _ensure_env()
    with open(os.path.join(a.feed_dir, "run.json"), "w") as fh:
        json.dump({
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "argv": sys.argv,
            "feed_dir": a.feed_dir,
            "gemini": {
                "model": os.environ.get(
                    "NOTICEBOT_GEMINI_MODEL", "gemini-3.5-flash"),
                "thinking_level": os.environ.get(
                    "NOTICEBOT_GEMINI_THINKING_LEVEL", "minimal"),
                # None = the field is not sent and the service decides. Recorded
                # as it is ACTUALLY sent: this said "low" whenever the variable
                # was unset, which is a run.json asserting a request that was
                # never made.
                "media_resolution": os.environ.get(
                    "NOTICEBOT_GEMINI_MEDIA_RESOLUTION") or None,
                "service_tier": os.environ.get(
                    "NOTICEBOT_GEMINI_SERVICE_TIER", "priority"),
            },
        }, fh, ensure_ascii=False, indent=2)
    print(f"[run] artifacts -> {a.feed_dir}")

    def audit_write(kind, payload, images=()):
        # WHO ANSWERED, AND WHEN IT STARTED. Neither was recorded, and both were
        # needed the first time anyone read these files seriously:
        #
        #   provider/model -- the record held `latency_s` but not who produced
        #     it, so comparing a slow evening against a fast morning could not
        #     distinguish "the service got better" from "we changed provider".
        #     Only sweeps/plan.json carried it, and only for sweeps.
        #   started_at -- the DIRECTORY NAME is written after the call returns,
        #     so it is a FINISH time. Reading it as a start time makes calls look
        #     like they overlap: a 97 s judge appears to still be running when
        #     the next one begins. They were serialised correctly the whole time.
        if isinstance(payload, dict):
            try:
                from planning.provider import provider_name, model_name
                payload.setdefault("provider", provider_name())
                payload.setdefault("model", model_name())
            except Exception:
                pass
            # Token usage, when the provider reports it. See gemini_provider.
            try:
                from planning import gemini_provider as _gp
                if _gp.LAST_USAGE:
                    payload.setdefault("usage", dict(_gp.LAST_USAGE))
            except Exception:
                pass
            lat = payload.get("latency_s")
            if isinstance(lat, (int, float)):
                payload.setdefault(
                    "started_at",
                    dt.datetime.fromtimestamp(time.time() - lat).isoformat(
                        timespec="milliseconds"))
        stamp = time.strftime("%Y%m%d_%H%M%S_") + f"{time.time_ns() % 1_000_000_000:09d}"
        out = os.path.join(audit_dir, f"{kind}_{stamp}")
        with audit_lock:
            os.makedirs(out, exist_ok=False)
            for i, jpeg in enumerate(images):
                with open(os.path.join(out, f"frame_{i}.jpg"), "wb") as fh:
                    fh.write(jpeg)
            with open(os.path.join(out, "result.json"), "w") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
        print(f"[audit] {kind} -> {out}")
        return out

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
                            cooldown=a.cooldown, tau_gap=a.tau_gap,
                            synonym_prompt=a.synonym_prompt)
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

    stt = STT(on_transcript, enabled=not a.no_stt,
              record_dir=os.path.join(a.feed_dir, "audio"))
    # BEFORE A PARTICIPANT ARRIVES, not after they have spoken. A dead input
    # produces a correctly-sized wav of zeros and an empty transcript, which is
    # indistinguishable from someone saying nothing -- and on 2026-08-08 it ran
    # that way for half an hour. The check costs 0.4 s and names the device.
    if not a.no_stt:
        stt.probe()

    def act(kind, val):
        if kind == "state":
            # S7 deliberately clears the S5 pan override while it performs its
            # person <-> finding gesture. Returning to S5 must restore the plan's
            # bearing, not fall back to the clip's authored +25-degree template.
            if val == "S5B_TRACK" and ctxd.get("aimed_pan") is not None:
                player.arm_pan_deg(float(ctxd["aimed_pan"]))
            player.request(val)
        elif kind == "ack_then":
            # Arm the landing before the clip is requested. S3_ACK's `then` is
            # S4_PLAN, so a bare request walks into a sweep -- and both users of
            # this nod (the greeting, and OK) want it to land somewhere else.
            #
            # AND ARM THE BEARING WITH IT. The landing is reached by the PLAYER's
            # own `then`, which never passes through the ("state", ...) branch
            # above -- so the pan restore that lives there is skipped, and S5B
            # comes back at its authored +25 instead of where the plan is aimed.
            # The head swings out and back, which reads as the robot losing the
            # thing it was watching at the exact moment you told it you had seen
            # the last one.
            if val == "S5B_TRACK" and ctxd.get("aimed_pan") is not None:
                player.arm_pan_deg(float(ctxd["aimed_pan"]))
            player.arm_next(val)
            # Its screen says "I heard you.", which is true after a request and a
            # non-sequitur here. Held back for the length of the nod; see below.
            ctxd["hush_heard_until"] = time.time() + 1.9
        elif kind == "ui":
            # A BORROWED NOD KEEPS ITS MOTION, NOT ITS SCREEN. S3_ACK is the nod,
            # and its screen is "I heard you." -- true when it follows a request,
            # S3_ACK is the affirmation nod and its screen is "I heard you." --
            # true when it follows a request, a non-sequitur when the robot is
            # introducing itself or acknowledging that you looked at a report.
            # The flow sets the screen from the state, so without this the hello
            # slides in and is overwritten a frame later by a sentence about
            # listening.
            #
            # Only `heard` is held back, and only while the nod runs: whatever
            # arrives on the far side of it -- `idle` after the greeting,
            # `tracking` after OK -- must pass, or the screen never comes back.
            if val == "heard" and time.time() < ctxd.get("hush_heard_until", 0):
                pass
            elif link:
                link.event("UI", val)
        elif kind == "noticed":
            if link:
                link.event("NOTICED", int(val))
            if int(val) == 0:
                # A COUNT RESET ON THE BOARD, AND NOTHING ELSE (2026-08-08).
                #
                # This used to also wipe the page: feed, thumbs, frames. The two
                # halves of the feature disagreed. Every record is stamped with
                # `request` and `plan_generation` precisely so that "a session
                # where the participant asks twice" does not become one
                # undifferentiated list (Storyboard.__init__) -- provenance that
                # only means anything if records from different requests coexist.
                # On disk they did; on the page they never could.
                #
                # Nor was the wipe escapable: PTT is accepted only from S1_IDLE
                # and STOP is the only route there, so asking a second question
                # REQUIRED destroying the answer to the first. Observed on
                # 2026-08-08 -- a finding confirmed, spoken, and written to
                # attention_log.jsonl, showing as 0 on the page.
                #
                # Cancelling stories mid-collection is a different event and now
                # hangs off `idle`, next to the story.reset() it is the display
                # half of. It must not happen here, because OK resets the board
                # too and OK does NOT abandon anything: the story that produced
                # the finding is usually still collecting its later panels, and
                # dropping the spinner while the card still arrives later would
                # make the page contradict itself.
                return
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
                    story.open(e, fr, truth, viz, idx,
                               pose=player.snapshot().get("pose_deg"))
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
            if story is not None:
                # Stamp the feed so each finding names the request it answers.
                story.request = val
                story.plan_generation = ctxd["plan_generation"]
            if view is not None:
                view.transcript = val
                view.context = val
            if UI is not None:
                with UI.LOCK:
                    UI.STATE["judgments"] = {}
            candidate_gate["busy"] = False
            candidate_gate["winner"] = None
            sweep.begin()
        elif kind == "pan":
            # Set the aim and stop. Do NOT also announce "arrived:S5_TRACK" here:
            # S6's shake may still be playing, and claiming the watching state
            # early would let a finding be accepted while the head is still
            # saying "not that one". The loop already reports the player's real
            # state change, which is the only honest source for it.
            ctxd["aimed_pan"] = float(val)
            player.set_pan_deg(float(val))
        elif kind == "idle":
            # STOP means STOP. The task is discarded, so the watching must end
            # too: the executor is torn down, the detector whitelist is released
            # and THE PLAN empties. Previously only the robot went back to idle
            # while perception carried on firing against the old spec -- the page
            # kept updating, entries kept satisfying, and findings could still be
            # recorded for a task the participant had cancelled.
            sweep.active = False
            if story is not None:
                story.reset()
            # The display half of that reset. `collecting` is the spinner row for
            # stories still gathering panels, and after the reset there are none
            # -- not because STOP voided them (it no longer does; it closes them
            # and they arrive as cards a few seconds later) but because they are
            # no longer GATHERING. Leaving the row up would show a spinner for a
            # story that has already been written. The FINISHED cards are
            # untouched -- see the note in the `noticed` handler.
            if UI is not None:
                with UI.LOCK:
                    UI.STATE["collecting"] = []
            ctxd["plan_generation"] = int(ctxd.get("plan_generation", 0)) + 1
            if view is not None:
                view.executor, view.spec = None, None
                view.context, view.transcript = "", ""
                view.statuses, view.suppressed = [], []
                view.apply_relevance(None)
            if UI is not None:
                with UI.LOCK:
                    UI.STATE["judgments"] = {}
            candidate_gate["busy"] = False
            candidate_gate["winner"] = None
            print("[flow] watching stopped -- plan cleared")
        elif kind == "pan_next":
            # EXCLUDE THE ANGLE THAT WAS REJECTED, NOT THE ONE THE HEAD IS AT.
            #
            # This passed the head's live pose, which worked only by accident:
            # S6 used to play at +25, near the watching aim, so "where the head
            # is" and "what the person rejected" were the same number. S6 now
            # turns to FACE THE PERSON (-30) to be corrected, so the head is
            # nowhere near the rejected angle -- and the rejected angle, still
            # the highest scoring station, came straight back as the answer.
            #
            # Observed on hardware: three taps in a row, three times
            # "[aim] nobody answered; moving to pan +30" while already watching
            # +30. The tap did nothing, which is the one response a rejection
            # must never get.
            #
            # `aimed_pan` is the watching aim -- set by the sweep's choice and by
            # every re-aim -- so it is what the tap was about.
            rejected = ctxd.get("aimed_pan")
            if rejected is None:
                rejected = player.snapshot()["pose_deg"]["pan"]
            deg, sc = next_best_pan(rejected)
            if deg is None:
                print("[aim] no other angle available -- holding")
            else:
                print(f"[aim] nobody answered; moving to pan {deg:+.0f}"
                      + (f" (score {sc})" if sc is not None else ""))
                ctxd["aimed_pan"] = float(deg)
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
            planner_attempt = 0

            def plan_fn(context, jpegs):
                nonlocal planner_attempt
                planner_attempt += 1
                started = time.monotonic()
                r = plan(context, jpegs)
                audit_write("planner", {
                    "request": context, "generation": generation,
                    "attempt": planner_attempt,
                    "image_count": len(jpegs or []),
                    "latency_s": round(time.monotonic() - started, 3),
                    "response": r,
                }, jpegs or [])
                print(f"[timing] {provider_name()} planner attempt {planner_attempt}: "
                      f"{time.monotonic() - started:.2f}s")
                if r.get("spec") is None or r.get("violations"):
                    print("[planner] invalid result; retrying once ...")
                    planner_attempt += 1
                    started = time.monotonic()
                    r = plan(context, jpegs)
                    audit_write("planner", {
                        "request": context, "generation": generation,
                        "attempt": planner_attempt,
                        "image_count": len(jpegs or []),
                        "latency_s": round(time.monotonic() - started, 3),
                        "response": r,
                    }, jpegs or [])
                    print(f"[timing] {provider_name()} planner attempt {planner_attempt}: "
                          f"{time.monotonic() - started:.2f}s")
                return r

            res, meta = sweep.plan(request, plan_fn, UI)
            v = (res or {}).get("violations") or []
            if generation != ctxd.get("plan_generation"):
                print("[planner] stale result discarded")
                return
            if (res or {}).get("spec") is None or v:
                raise ValueError(f"invalid plan from {provider_name()}: "
                                 + "; ".join(map(str, v)))
            ctxd["spec"] = res["spec"]
            print(f"[planner] {len(v)} violation(s); "
                  f"watch={len((ctxd['spec'] or {}).get('watch', []) or [])} entries")
            # AND WHAT THEY ACTUALLY WATCH FOR, in words.
            #
            # "watch=3 entries" was the whole report, and on 2026-08-08 it cost an
            # evening: "Looking at people joining on the large board" produced
            # gaze(1), approach(7) and gathering(10) -- no hands_on(9) anywhere --
            # so a person drawing at that board could not fire any card in the
            # plan. She drew for several minutes. Every [cv] and [gate] line was
            # about why a relation was false; not one could say that the relation
            # she was performing was NOT BEING WATCHED AT ALL. That is the single
            # most consequential fact about a plan and it was the one thing the
            # plan did not print.
            from planning.judge import _RELATION_CLAIMS
            for e in (ctxd["spec"] or {}).get("watch", []) or []:
                ids = list(e.get("all") or []) + list(e.get("any") or []) + list(e.get("then") or [])
                what = " + ".join(_RELATION_CLAIMS.get(i, f"relation {i}") for i in ids)
                on = e.get("on")
                print(f"  will fire on: {what}{f' -- {on}' if on else ''}"
                      f"   [{e.get('label', '')}]")
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
                # DID THE TARGET CHANGE? That is the whole question S5a exists
                # to answer, and only the planner can answer it -- the sweep is
                # additive, so most re-plans re-choose what was already watched.
                # The planner starts only AFTER S4 has entered its S5 hold, so a
                # late arm_next() would be consumed by the next unrelated
                # one-shot (in practice S7a), hijacking S7a -> S7b. Changed aims
                # therefore request S5A now; unchanged aims simply retarget S5B.
                #
                # Same joints, same endpoints, two different claims -- and what
                # separates them is only whether anybody authored the move. See
                # S4_S5_DESIGN.md sec 3.05.
                new_pan = float(meta["richest_pan"])
                prev = ctxd.get("aimed_pan")
                if prev is None or abs(new_pan - prev) > ST.AIM_CHANGED_DEG:
                    player.arm_pan_deg(new_pan)
                    player.request("S5A_SETTLE")
                    print(f"[aim] target CHANGED "
                          f"({'first' if prev is None else f'{prev:+.0f}'}"
                          f" -> {new_pan:+.0f}) -- S5a will announce the arrival")
                else:
                    player.set_pan_deg(new_pan)
                ctxd["aimed_pan"] = new_pan
        except Exception as e:
            # A failed plan must NOT strand the robot: S4/S5 still run, the
            # researcher can see the failure in the UI and retype or stop.
            print(f"[planner] failed: {e}")
            if view is not None:
                view.plan_error = f"planner failed: {e}"
            ui_events.append(f"plan_failed:{e}")

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
    last_whynot = 0.0        # throttle for the [cv] diagnostic
    last_plan_led = 0.0      # throttle for the planning-hold breath
    shown = None            # the last frame the overlay was computed FROM
    event_frames = cam.event_frames if cam is not None else deque(maxlen=70)
    event_candidates = []                 # wait through t+1.0 before Gemini confirm
    # How long S7 waits on the judge. A CLI override of ST.JUDGE_DEADLINE_S, read
    # once: the value has to be the same for every finding in a session or the
    # data has two regimes in it, which is the exact problem it exists to remove.
    judge_deadline = (ST.JUDGE_DEADLINE_S if a.judge_deadline is None
                      else float(a.judge_deadline))
    if judge_deadline <= 0:
        print("[confirm] judge deadline 0 -- S7 fires on the CV gate alone; "
              "the judge still runs, for the record only")
    confirmed_findings = []               # worker -> main-loop handoff
    # TWO JOBS, TWO FIELDS. `busy` used to mean both "a candidate is queued"
    # and "a judge is in flight", and every release cleared both -- including
    # `kind == "plan"`, which runs on every re-plan. With REPLAN_IDLE_S at 60 s
    # that opened the gate on a schedule, regardless of whether a call was still
    # out, so the next fired group started a SECOND concurrent judge.
    #
    # Measured, e2e_20260805_150019, all gemini, interleaved with a planner that
    # stayed flat at 5-17 s:
    #     judge1 15:01:09 -> 15:03:04
    #     judge2 15:02:10 -> 15:03:51   (started inside judge1)
    #     judge3 15:03:21 -> 15:04:02   (started inside judge2)
    #     ... up to three at once, latency 114 -> 197 -> 278 s
    # Self-reinforcing: a slower judge is more likely to still be running when
    # the next re-plan releases the gate, which adds another concurrent call.
    #
    # `inflight` is owned by the worker alone -- incremented before the call,
    # decremented in a finally -- so no unrelated event can open the gate on a
    # request that has not come back.
    # `awaiting` is the candidate whose judge is out, held so the deadline can
    # find it. Cleared by whichever gets there first; a judge that lands after
    # the deadline finds `fired_early` on its own candidate and records rather
    # than fires.
    candidate_gate = {"busy": False, "winner": None, "inflight": 0,
                      "awaiting": None}
    from planning.sweep_plan import Sweep
    if a.name and link:
        # Sent, not greeted: a restart is a researcher action mid-session, and
        # replaying the hello would announce it to somebody already working.
        link.name(a.name[:16])
        print(f"[name] restored {a.name[:16]!r} from --name")
        if UI is not None:
            with UI.LOCK:
                UI.STATE["bot_name"] = a.name[:16]

    sweep = Sweep(feed_dir=a.feed_dir)
    os.makedirs(a.feed_dir, exist_ok=True)
    stt.warm()              # load Whisper now, not under the first participant
    if not a.offline:
        # Same reason, one subsystem over: the first Gemini call of a process
        # pays ~60 s of connection and cold model. Off the main thread, because
        # the point is that nobody waits for it -- including us.
        def _warm_gemini():
            try:
                from planning.provider import warm, provider_name
                print(f"[llm] provider: {provider_name()}")
                warm()
            except Exception as e:
                print(f"[gemini] warm-up unavailable: {e}")
        threading.Thread(target=_warm_gemini, daemon=True).start()

    def event_jpegs(onset):
        """The five nearest raw frames for the judge. See planning/event_frames:
        the offsets are all historical, so they exist the moment a candidate
        fires and nothing has to be waited for."""
        return select_temporal_frames(event_frames, onset)

    def publish_judgment(entry, status, note="", generation=None):
        if UI is None:
            return
        if generation is not None and generation != ctxd.get("plan_generation"):
            return
        label = str(entry.get("label") or "requested event")
        with UI.LOCK:
            UI.STATE.setdefault("judgments", {})[label] = {
                "status": status, "note": str(note or ""), "time": time.time(),
            }

    def confirm_candidate_group(candidate):
        """Send every coincident card in one Gemini request; emit one winner."""
        candidate_gate["inflight"] += 1
        try:
            _confirm_candidate_group(candidate)
        finally:
            candidate_gate["inflight"] -= 1

    def _confirm_candidate_group(candidate):
        from planning.judge import (ReportabilityTaste, confirmation_claim,
                                    judge_candidate_group)
        taste = story.taste if story is not None else ReportabilityTaste()
        entries = candidate["entries"]
        if candidate["generation"] != ctxd.get("plan_generation"):
            candidate_gate["busy"] = False
            return
        claims = [confirmation_claim(entry) for entry in entries]
        for entry in entries:
            publish_judgment(entry, "judging",
                             f"Checking {len(entries)} same-moment card(s) in one "
                             f"{provider_name()} call…",
                             candidate["generation"])
        started = time.monotonic()
        # The request travels WITH the candidate, not read live: a re-plan
        # while this call is out would otherwise judge these frames against
        # a request that arrived after they were captured.
        result = judge_candidate_group(candidate["images"], entries, taste,
                                       request=candidate.get("request", ""))
        elapsed = time.monotonic() - started
        if candidate_gate.get("awaiting") is candidate:
            candidate_gate["awaiting"] = None
        audit_write("judge_group", {
            "claims": claims, "entries": entries, "group_size": len(entries),
            "onset": candidate["onset"],
            "generation": candidate["generation"],
            "latency_s": round(elapsed, 3),
            "result": result,
        }, candidate["images"])
        print(f"[timing] {provider_name()} group judge ({len(entries)} cards, "
              f"one call): {elapsed:.2f}s")

        # THE DEADLINE GOT THERE FIRST. Do not fire again -- S7 is already
        # playing -- but do not throw the verdict away either. Whether the gate
        # would have agreed is the number that says what the deadline cost, and
        # it only exists if it is written down as it happens.
        if candidate.get("fired_early"):
            agreed = int(result.get("selected_index", -1)) >= 0
            # Onto the record, not just the screen. This is the only number that
            # says what firing on the CV gate alone costs, and a terminal line
            # is not a record -- it scrolls.
            if story is not None:
                story.judge_agreed = agreed
                # Late, but usually still ahead of the story closing (`linger`
                # is 6 s), so the card can carry the good sentence even on the
                # deadline path. Only if it does not arrive in time does the
                # strip fallback run.
                if not story.describe:
                    story.describe = str(result.get("note")
                                         or result.get("feedback") or "")
            note = (f"Landed {elapsed:.0f}s late, after the report had gone out. "
                    + ("It agrees." if agreed else
                       "IT WOULD HAVE REJECTED THIS: " + str(result.get("note") or "")))
            for entry in entries:
                publish_judgment(entry, "confirmed" if agreed else "rejected",
                                 note, candidate["generation"])
            print(f"[confirm] late judge ({elapsed:.1f}s) "
                  f"{'agrees' if agreed else 'DISAGREES'} with the deadline report")
            return

        selected = result.get("selected_index", -1)
        rows = {row.get("index"): row for row in result.get("candidate_results", [])}
        winner = (entries[selected] if isinstance(selected, int)
                  and 0 <= selected < len(entries) else None)
        winner_label = winner.get("label") if winner else None
        for index, entry in enumerate(entries):
            row = rows.get(index, {})
            reason = row.get("reason") or result.get("note") or ""
            if index == selected and winner is not None:
                publish_judgment(entry, "confirmed", reason, candidate["generation"])
            elif row.get("confirmed") and winner is not None:
                publish_judgment(
                    entry, "grouped",
                    f"Also confirmed in this moment; one notification uses {winner_label}. {reason}",
                    candidate["generation"],
                )
            else:
                publish_judgment(entry, "rejected", reason, candidate["generation"])

        if winner is None:
            candidate_gate["busy"] = False
            print(f"[confirm] group rejected: {result.get('note', '')}")
            # Serve the short cooldown, not the full one -- see ST.REJECTED_COOLDOWN_S.
            if view is not None and view.executor is not None:
                for entry in entries:
                    view.executor.recool(entry, ST.REJECTED_COOLDOWN_S,
                                         backoff=True)
            return
        candidate["entry"] = winner
        candidate_gate["winner"] = winner_label
        # THE SENTENCE TRAVELS WITH THE FINDING. Written from five separate
        # frames at the moment the gate fired, with the request in hand -- see
        # Storyboard._finalize for why that is a better witness than the strip.
        if story is not None:
            story.describe = str(result.get("note") or result.get("feedback") or "")
        confirmed_findings.append((candidate, result))

    # ---- WHEN IT HANGS, SAY WHERE ----------------------------------------
    #
    # 2026-08-08: the loop froze after PTT_UP and Ctrl-C would not reach it. A
    # KeyboardInterrupt is caught below and the cleanup is clean, so if the
    # signal never lands the main thread is inside something that does not hand
    # the interpreter back -- a C extension mid-call, or a lock held by a worker
    # that is itself stuck. From the outside those look identical, and neither
    # leaves a trace, so the session was lost with nothing to debug from.
    #
    # Two ways out of that, both cheap:
    #
    #   SIGUSR1  ->  every thread's stack, on demand, from another terminal.
    #   watchdog ->  the same dump WITHOUT being asked, the moment the loop
    #                stops ticking. It has to be unasked: the hang takes the
    #                terminal with it, so a diagnostic that needs the terminal
    #                is a diagnostic that is never run.
    #
    # The watchdog only ever prints. Killing the process itself is tempting and
    # wrong: torque would stay engaged on a robot nobody is watching, and the
    # STOP path below is what puts it down.
    import faulthandler
    import signal
    faulthandler.enable()
    try:
        faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False)
        print(f"[loop] pid {os.getpid()} -- `kill -USR1 {os.getpid()}` dumps "
              f"every thread's stack if it ever stops responding")
    except (AttributeError, ValueError):
        pass          # no SIGUSR1 on this platform; the watchdog still runs

    beat = {"t": time.time(), "warned": False}

    def _watchdog():
        while True:
            time.sleep(1.0)
            late = time.time() - beat["t"]
            if late > STALL_WARN_S and not beat["warned"]:
                beat["warned"] = True
                print(f"\n[loop] !! MAIN LOOP HAS NOT TICKED FOR {late:.0f}s. "
                      f"Stacks for every thread follow; the topmost frame of "
                      f"MainThread is where it is stuck. Ctrl-C may not reach "
                      f"it -- Ctrl-\\ (SIGQUIT) will.", flush=True)
                faulthandler.dump_traceback(all_threads=True)
            elif late <= STALL_WARN_S:
                beat["warned"] = False

    threading.Thread(target=_watchdog, daemon=True).start()

    try:
        while True:
            # First statement in the iteration on purpose: anything above it
            # would be invisible to the watchdog, and the last hang was in the
            # handling of an input event, not in the work that follows it.
            beat["t"] = time.time()
            snap = player.snapshot()

            # the browser's context box is a free remote input: type what the
            # participant asked and the designed cycle starts. Doubles as the
            # wizard channel when perception is not driving.
            if UI is not None:
                with UI.LOCK:
                    sentence = UI.STATE.get("pending_context")
                    UI.STATE["pending_context"] = None
                    new_name = UI.STATE.get("pending_name")
                    UI.STATE["pending_name"] = None
                    force_now = bool(UI.STATE.get("pending_finding"))
                    UI.STATE["pending_finding"] = False
                    resweep_now = bool(UI.STATE.get("pending_resweep"))
                    UI.STATE["pending_resweep"] = False
                # ---- THE NAME, AND THE ONE TIME IT GREETS --------------------
                #
                # Fired when the name ARRIVES rather than at process start, which
                # is also when it means something: the participant chooses a name
                # in the introduction, the researcher types it, and the robot
                # wakes up and says it back. Starting the loop is a researcher
                # action and there is nobody to greet yet.
                #
                # arm_next before request, because S3_ACK's `then` is S4_PLAN --
                # asking for the clip alone would walk straight into a sweep with
                # no request to plan for. That override exists for exactly this:
                # a tool that knows where a one-shot should land before it ends.
                if new_name and link:
                    ctxd["bot_name"] = new_name
                    link.name(new_name)
                    print(f"[name] it is called {new_name!r} -- greeting")
                    # S3_ACK is 1.67 s; hold the screen a little past it so the
                    # nod finishes on `hello` and only then falls to `idle`.
                    ctxd["hush_heard_until"] = time.time() + 1.9
                    link.ui("hello")
                    player.arm_next("S1_IDLE")
                    player.request("S3_ACK")
                # ---- THE TWO EMERGENCY CONTROLS ------------------------------
                #
                # A session is one shot. When the actor plays the scene and the
                # CV does not fire, the choice is between a void session and the
                # researcher taking over, and the second is worth having.
                #
                # `finding` is the flow's own event -- the same one the `f` key
                # sends -- so it takes the ordinary path: S7a performs the
                # notice, `noticed` opens a story, the keyframe rule collects the
                # panels and the narration judge writes the sentence into the
                # feed. What it skips is the trigger and the CONFIRMATION judge,
                # which are exactly the two things that failed.
                #
                # These replaced a panel for hand-editing the watch entries
                # (removed 2026-08-12): repairing the SPEC mid-session asks the
                # researcher to think in relation ids with an actor mid-scene and
                # a participant watching, and what is wanted at that moment is
                # not a better spec, it is this noticed now.
                if force_now:
                    if story is not None:
                        # THE PREVIOUS JUDGE'S SENTENCE MUST NOT TRAVEL. Nothing
                        # was judged here, and `describe` is read by _finalize as
                        # the opening line -- left standing, the forced card
                        # would be captioned with the last real finding's words.
                        story.describe = ""
                        story.judge_agreed = None
                    ui_events.append("finding")
                    print("[manual] forced finding -- skipping the trigger and "
                          "the judge; the story is collected as usual")
                if resweep_now:
                    ui_events.append("resweep")
                    print("[manual] re-sweep requested")
                with UI.LOCK:
                    pan_req = UI.STATE.get("pending_pan")
                    UI.STATE["pending_pan"] = None
                    # Sentinel, not falsiness: 0 degrees is a legitimate seat
                    # (dead ahead) and `if user_req:` would drop it. Same trap
                    # that once swallowed EVT LED 0 on the CoreS3.
                    user_req = UI.STATE.get("pending_user_pan", "unset")
                    if "pending_user_pan" in UI.STATE:
                        UI.STATE["pending_user_pan"] = "unset"
                if user_req != "unset":
                    # Straight to the player, NOT through the flow: this is a
                    # calibration of the room, not an event in the session. It
                    # changes where a gesture points, never which gesture runs.
                    player.set_user_pan(user_req)
                    # Echo the value the player KEPT, which is clamped to what
                    # the body can turn to. Otherwise the page reports a seat
                    # that does not exist -- and the number a researcher reads
                    # back off the screen is the one that ends up in the notes.
                    with UI.LOCK:
                        UI.STATE["user_pan"] = (
                            None if player._user_pan is None
                            else round(player._user_pan, 1))
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
            # ---- THE ANTENNA WHILE THE PLANNER IS OUT ------------------------
            #
            # `plan_pending` and not the clip state: the player has already run
            # S4 to its end and moved on, while the request is still in flight.
            # That gap is the only stretch in a session where the robot is doing
            # something the person cannot see, and it is measured in seconds --
            # 6 on a good evening, 14-62 during the 2026-08-08 slowdowns.
            #
            # The light was not frozen before this: the firmware breathes on its
            # own after 500 ms of silence. But that fallback was tuned to be
            # S1_IDLE's envelope, so the wait said `cool` in colour and `idle` in
            # rhythm. Same colour, quicker rhythm -- see ST.PLAN_BREATH.
            if link and getattr(flow, "plan_pending", False):
                _pb, _t = ST.PLAN_BREATH, time.time()
                if _t - last_plan_led >= 1.0 / _pb["hz"]:
                    last_plan_led = _t
                    phase = (_t % _pb["period_s"]) / _pb["period_s"]
                    # raised cosine: no corner at the bottom of the breath
                    lvl = _pb["low"] + (_pb["high"] - _pb["low"]) * \
                        (0.5 - 0.5 * math.cos(2 * math.pi * phase))
                    counts["led"] += 1
                    link.led(int(lvl))

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
                if (snap["state"] == "S5B_TRACK"
                        and last_state in ("S7a", "S7b")):
                    candidate_gate["busy"] = False
                    candidate_gate["winner"] = None
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
                    candidate_gate["busy"] = False
                    candidate_gate["winner"] = None
                    continue
                # THE CONFIRMATION IS `selected_index`, NOT A `confirmed` FLAG.
                #
                # This read `decision.get("confirmed")`, and the group judge's
                # schema is
                #     required: [axes, candidates, selected_index, note, feedback]
                #     additionalProperties: False
                # so there is no top-level "confirmed" and the model is forbidden
                # from inventing one. `.get` returned None every time, so EVERY
                # successful judgement was discarded one line after it arrived --
                # S7 could not fire at all. Only the --offline stub returns a
                # top-level "confirmed", which is why the mechanics looked fine
                # whenever they were tested without the cloud.
                #
                # The question was also already answered upstream: a candidate
                # only reaches this queue when confirm_candidate_group picked a
                # winner (`winner is None` returns early). Re-asking it here with
                # a key that does not exist is how the answer got thrown away.
                if int(decision.get("selected_index", -1)) < 0:
                    print(f"[confirm] rejected: {decision.get('note', '')}")
                    if view is not None and view.executor is not None:
                        view.executor.recool(candidate["entry"],
                                             ST.REJECTED_COOLDOWN_S, backoff=True)
                    candidate_gate["busy"] = False   # or the gate stays shut for
                    candidate_gate["winner"] = None  # the rest of the session
                    continue
                feedback = decision.get("feedback") or decision.get("note") or candidate["entry"].get("label")
                print(f"[FEEDBACK] {feedback}")
                if a.feedback == "robot":
                    ctxd["entry"] = candidate["entry"]
                    ctxd["frame"] = candidate["frame"]
                    ui_events.append("finding")
                else:
                    candidate_gate["busy"] = False
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
            flow.judge_busy = bool(candidate_gate["busy"])
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
                        # SAY WHY NOTHING FIRED, while it is not firing. Every
                        # 8 s of watching with no candidate, name the first
                        # broken link -- no pose / object not detected /
                        # geometry -- so "it did not trigger" is attributable
                        # during the session rather than reconstructed after it
                        # from a log that never recorded the reason.
                        if not fired and now - last_whynot > 8.0:
                            reason = view.why_not()
                            if reason:
                                last_whynot = now
                                print(f"[cv] {reason}")
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
            if fired:
                entries = [dict(e) for e in order_coincident_candidates(fired)]
                if candidate_gate["busy"] or candidate_gate["inflight"]:
                    for entry in entries:
                        publish_judgment(
                            entry, "suppressed",
                            "A previous event group is still being judged or reported.",
                            ctxd.get("plan_generation", 0),
                        )
                    # AND ALL BUT GIVE THEM BACK THEIR COOLDOWN. The gate being
                    # busy is a fact about the previous moment, not about these
                    # cards; they reported nothing, so they owe nothing. Charged
                    # fifteen seconds for the collision, the next real occurrence
                    # is refused as well -- reported 2026-08-12 as cards that sit
                    # in cooldown and never fire again.
                    #
                    # A FEW SECONDS RATHER THAN ZERO, though. This was a full
                    # refund (`unfire`) while firing still required a fresh edge,
                    # which held the card back until the relation broke and
                    # re-formed. With the edge requirement gone a zero cooldown
                    # means it fires again on the very next frame, into the same
                    # gate that is still busy, once per frame until it clears.
                    if view is not None and view.executor is not None:
                        for entry in entries:
                            view.executor.recool(entry, ST.SUPPRESSED_RETRY_S)
                    print(f"[watch] suppressed later group of {len(entries)} card(s): "
                          f"gate busy -- retrying in {ST.SUPPRESSED_RETRY_S:.0f}s")
                else:
                    candidate_gate["busy"] = True
                    labels = [entry.get("label") for entry in entries]
                    print(f"[watch] same-moment candidate group: {labels}")
                    for entry in entries:
                        publish_judgment(
                            entry, "candidate",
                            f"CV gate passed with {len(entries)} same-moment card(s); collecting five frames.",
                            ctxd.get("plan_generation", 0),
                        )
                    # WITH NO DEADLINE, THE JUDGE IS NOT A STEP AT ALL, and
                    # that includes its frame window. `due` is +1.0 s because
                    # the judge wants five frames spanning the onset
                    # (t-1.0 .. t+1.0); the REPORT never needed them. Waiting
                    # for them anyway would leave a second of the judge's
                    # latency in a path that is supposed to be free of it --
                    # and that second is on top of the ~1.25 s the CV gate
                    # already spends on sustain + persist.
                    #
                    # The candidate still goes on to collect its frames and be
                    # judged; it is just no longer what the robot is waiting
                    # for. `fired_early` makes the late verdict record instead
                    # of fire, exactly as on the deadline path.
                    fire_now = judge_deadline <= 0
                    if fire_now:
                        from planning.judge import pick_winner
                        _i = pick_winner(entries)
                        if _i >= 0 and a.feedback == "robot":
                            ctxd["entry"] = entries[_i]
                            ctxd["frame"] = frame.copy() if frame is not None else None
                            ui_events.append("finding")
                            candidate_gate["winner"] = entries[_i].get("label")
                            print(f"[confirm] CV gate -- reporting now: "
                                  f"{candidate_gate['winner']}")
                        elif _i < 0:
                            fire_now = False
                    event_candidates.append({
                        # `due` USED TO BE now + 1.0, because the frame window
                        # straddled the onset and the +1.0 s frame did not exist
                        # yet. That second sat on the critical path in front of
                        # a call that usually takes two, every single time. The
                        # offsets are now entirely in the past
                        # (EVENT_OFFSETS_S), so the evidence is already in the
                        # camera's deque and the request can go out at once.
                        #
                        # Kept as a field rather than removed: it is the seam
                        # where "collect the evidence" and "send it" are still
                        # separable, and a future window that needs to wait
                        # again should move this number rather than reintroduce
                        # a sleep somewhere else.
                        "entries": entries, "onset": now, "due": now,
                        "fired_early": fire_now,
                        "generation": ctxd.get("plan_generation"),
                        # Captured WITH the candidate, alongside its generation.
                        # The judge is asked to prefer the card most relevant to
                        # the request, so it must be the request these frames
                        # were watched under -- not whatever has been asked since.
                        "request": ctxd.get("request", ""),
                        "frame": frame.copy() if frame is not None else None,
                    })

            for candidate in list(event_candidates):
                if candidate["generation"] != ctxd.get("plan_generation"):
                    event_candidates.remove(candidate)
                    candidate_gate["busy"] = False
                    print("[confirm] cancelled stale candidate before API call")
                    continue
                if now < candidate["due"]:
                    continue
                event_candidates.remove(candidate)
                candidate["images"] = event_jpegs(candidate["onset"])
                if len(candidate["images"]) != 5:
                    print("[confirm] skipped: five event frames unavailable")
                    for entry in candidate["entries"]:
                        publish_judgment(entry, "rejected",
                                         "Five event frames were unavailable.",
                                         candidate["generation"])
                    candidate_gate["busy"] = False
                    continue
                # Held so the DEADLINE can find it. The worker thread cannot be
                # cancelled -- and is not: it lands late and its verdict is
                # recorded beside the finding it did not gate.
                # time.time(), NOT monotonic: the deadline below is compared
                # against the loop's `now`, which is wall clock. Mixing the two
                # made every difference astronomically large, so the deadline
                # fired instantly whatever it was set to -- the 0 default looked
                # right and --judge-deadline 10 was a knob that did nothing.
                candidate["sent_at"] = time.time()
                candidate_gate["awaiting"] = candidate
                threading.Thread(target=confirm_candidate_group, args=(candidate,),
                                 daemon=True).start()

            # ---- the judge is taking too long: react anyway ----
            #
            # Waiting buys the pass/fail gate and nothing else. `describe` never
            # reaches the participant -- S7 plays a sound effect, not speech, and
            # the sentence on the feed card is written later by the storyboard's
            # own narration of the strip. So the cost of waiting is paid in the
            # only currency this interaction has: arriving while the moment is
            # still in the room.
            #
            # The card is picked by pick_winner, the SAME rule the judge path
            # uses, because it was never a judgement -- most relation ids wins,
            # ties to CV's order. A second rule written beside the first is how
            # two sessions would disagree about which event they recorded.
            waiting = candidate_gate.get("awaiting")
            if (waiting is not None and not waiting.get("fired_early")
                    and now - waiting["sent_at"] >= judge_deadline):
                candidate_gate["awaiting"] = None
                if waiting["generation"] != ctxd.get("plan_generation"):
                    candidate_gate["busy"] = False
                else:
                    from planning.judge import pick_winner
                    idx = pick_winner(waiting["entries"])
                    if idx < 0:
                        candidate_gate["busy"] = False
                    else:
                        waiting["entry"] = waiting["entries"][idx]
                        waiting["fired_early"] = True
                        candidate_gate["winner"] = waiting["entry"].get("label")
                        print("[confirm] "
                              + ("not waiting for the judge"
                                 if judge_deadline <= 0 else
                                 f"judge past {judge_deadline:.0f}s")
                              + f" -- reporting: {candidate_gate['winner']}")
                        for entry in waiting["entries"]:
                            publish_judgment(
                                entry, "candidate",
                                ("Reported straight off the CV gate. The judge "
                                 "is still running and its verdict will be "
                                 "recorded here when it lands."
                                 if judge_deadline <= 0 else
                                 f"Reported without waiting -- the judge passed "
                                 f"{judge_deadline:.0f}s. Its verdict will be "
                                 f"recorded here when it lands."),
                                waiting["generation"])
                        if a.feedback == "robot":
                            ctxd["entry"] = waiting["entry"]
                            ctxd["frame"] = waiting["frame"]
                            ui_events.append("finding")
                        else:
                            candidate_gate["busy"] = False

            # Open bursts advance whenever there is a frame and an open burst --
            # NOT only while watching. A story that stops collecting because the
            # robot changed state loses its follow-through, which is the half that
            # says what the moment turned into.
            #
            # BUT ONLY WHEN THE HEAD IS STILL, for the same reason perception is
            # gated above, and it took a real strip to see how badly it mattered.
            # The camera is ON THE HEAD, and a story opens at the instant S7
            # starts swinging it. Feeding those frames in did two things:
            #
            #   - the panels recorded the ROBOT TURNING, not the room. A saved
            #     3-shot strip reads: blurred mid-turn, the participant facing
            #     the camera because the robot had turned to her, then a wall.
            #     The narration was written from the middle one -- "a woman
            #     looking towards the camera" -- which describes her reacting to
            #     the robot rather than the thing she asked it to watch.
            #   - it DEFEATED THE KEYFRAME TEST. `scene_diff` asks whether the
            #     picture changed; a head turn changes all of it, so every
            #     interval produced a panel no matter how still the room was.
            #     Hence "3 shots every time, even when I do not move" -- the
            #     shots were counting the robot's own motion.
            #
            # `story.step` is a no-op with no open burst, so gating it costs
            # nothing when nothing is being collected. The burst's own clocks
            # (`ends_at`, `next`) run on wall time and are unaffected: a story
            # still closes on schedule, it just does not photograph the swing.
            if story is not None and frame is not None:
                ctxd["frame"] = frame
            if story is not None and frame is not None and settled:
                # THE POSE TRAVELS WITH THE FRAME. The camera is on the head,
                # so "is this the same shot" is a question about the neck, not
                # about the picture -- and the picture cannot answer it (a room
                # that changed and a head that moved look identical to a pixel
                # difference).
                story.step(frame,
                           view.truth if view else {}, view.viz if view else {},
                           view.statuses if view else [],
                           pose=snap.get("pose_deg"))

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
        # CLOSE THE BOOKS BEFORE ANYTHING ELSE. A story stays open through
        # `linger` for its follow-through, so stopping the run inside that window
        # used to discard the whole finding -- strip, sentence and log line --
        # including ones the judge had already confirmed. First thing in the
        # teardown, because the rest of it prints and closes ports.
        if story is not None:
            try:
                story.flush("run stopped")
            except Exception as exc:
                print(f"[story] flush failed: {str(exc)[:90]}")
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
