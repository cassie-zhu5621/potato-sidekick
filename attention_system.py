"""
attention_system.py — the FULL VLM-first system (relation_table.md architecture, live):

    context (typed) + frame  ->  VLM PLANNER (planner.py)  ->  watch-spec JSON
        ->  RelationEngine (relations.py): per-frame truth vector over rows 1-11
        ->  WatchExecutor (watch_exec.py): all/any/not/then + windows + habituation
        ->  records (frame + truth slice + label) + web UI + relation_log.jsonl

Context comes from --context or a context FILE (default context.txt next to this script);
EDIT THE FILE WHILE RUNNING and the system re-plans on the next frame — that is the
"taste is re-writable at runtime" loop, now operating on plans instead of axis weights.

Run:
    echo "Two of us are assembling a robot arm this afternoon." > context.txt
    python attention_system.py --offline --camera 0 --serve      # keyless dry run
    export ANTHROPIC_API_KEY=sk-...
    python attention_system.py --camera 0 --serve --save         # real planner
    python attention_system.py --serve                           # M5 (rig.py CAM_URL)
"""

from __future__ import annotations
import argparse, json, math, os, queue, subprocess, sys, threading, time
os.environ.setdefault("GLOG_minloglevel", "2")   # quiet MediaPipe/glog INFO+WARNING spam (set 3 for silent)
import cv2
import numpy as np

from perceive import make_detector
from planner import plan, VOCAB, VOCAB_VERSION
from relations import RelationEngine
from watch_exec import WatchExecutor
from judge import judge as run_judge, ReportabilityTaste
from gaze import (draw_text, draw_box, draw_arrow, draw_circle, draw_panel,
                  C_GREEN, C_RED, C_YELLOW, C_ORANGE, C_MAGENTA, C_CYAN, C_WHITE)
from attention_demo import frame_source, publish
from cores3_link import CoreS3Link          # CoreS3 I/O board (screen/sound/touch); optional


def beep():
    """Audible 'noticed' cue (tests are hard to watch while acting). macOS only; silent no-op elsewhere."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def make_strip(shots, height=300):
    """The comic strip: N shots -> one horizontal story image."""
    tiles = []
    for s in shots:
        h, w = s.shape[:2]
        tiles.append(cv2.resize(s, (max(1, int(w * height / h)), height)))
    return np.hstack(tiles)


def shot_trace(truth, viz):
    """One panel's GROUNDED detection summary (what the system actually saw this frame), so the
    story description is narrated from the real detected sequence — not re-captioned off a photo."""
    dets = viz.get("dets", [])
    parts, seen = [], set()
    for relname, h in viz.get("hits", []):              # gazing-at / pointing-at (have a target)
        di = h.get("det") if isinstance(h, dict) else None
        tgt = dets[di].label if isinstance(di, int) and 0 <= di < len(dets) else ""
        key = f"{relname} {tgt}".strip()
        if key not in seen:
            seen.add(key); parts.append(key)
    hit_names = {relname for relname, _ in viz.get("hits", [])}
    for i, on in truth.items():                         # truth-only rows (proxemic, approach, hands-on…)
        if on:
            nm = VOCAB.get(i, str(i)).split("—")[0].strip()
            if nm not in hit_names and nm not in seen:
                seen.add(nm); parts.append(nm)
    return ", ".join(parts) if parts else "quiet"


def read_context(path, fallback):
    try:
        s = open(path).read().strip()
        return s or fallback
    except OSError:
        return fallback


def spec_summary(spec):
    out = []
    for c in spec.get("watch", []) or []:
        bits = []
        if c.get("all"):  bits.append("+".join(map(str, c["all"])))
        if c.get("any"):  bits.append("any(" + ",".join(map(str, c["any"])) + ")")
        if c.get("then"): bits.append("then(" + "→".join(map(str, c["then"])) + ")")
        if c.get("not"):  bits.append("not(" + ",".join(map(str, c["not"])) + ")")
        out.append((" ".join(bits), c.get("label", "")))
    return out


def _rig_raw(rig, pan, tilt):
    """Write a pose to the board WITHOUT move_to's 0.35s settle — fast, non-blocking-ish."""
    from rig import PAN_SIGN, PAN_LIMIT, TILT_LIMIT, TILT_TRIM
    cp = max(-PAN_LIMIT, min(PAN_LIMIT, PAN_SIGN * pan))
    ct = max(-TILT_LIMIT, min(TILT_LIMIT, tilt + TILT_TRIM))
    rig.ser.write(f"{int(round(cp))},{int(round(ct))}\n".encode())
    rig.pan, rig.tilt = pan, tilt


def rig_goto_async(rig, pan, tilt):
    """Non-blocking scan move: fire the target and return — the board eases on its own while
    the main loop keeps grabbing frames (keeps the live view smooth, no per-move freeze)."""
    try:
        _rig_raw(rig, pan, tilt)
    except Exception:
        pass


def look_bid(rig, cs3, beats=4, beat_s=0.42, nod=False):
    """'Come look!' — head bob and buzzer driven from ONE laptop timeline so they're in sync.
    Per beat: command the head DOWN first (servo has more lag), then fire ONE beep, then UP.
    Sound + downbeat land together → rhythmic, no 'sound first' offset."""
    if cs3:
        cs3.look()                                  # screen 'Look!' + antenna pulse (no autonomous sound)
    p = rig.pan if rig is not None else 0
    tl = rig.tilt if rig is not None else 0
    half = beat_s / 2.0
    for _ in range(beats):
        if rig is not None and nod: _rig_raw(rig, p, tl + 12)   # head down (only if nods enabled)
        if cs3: cs3.beep()                                      # one beat tone
        time.sleep(half)
        if rig is not None and nod: _rig_raw(rig, p, tl)        # head up
        time.sleep(half)


def confused_shake(rig):
    """Quick 'huh?' head jitter — the puzzled reaction when you tap its head (= 'that's wrong')."""
    if rig is None:
        return
    p, tl = rig.pan, rig.tilt
    for dp in (11, -11, 7, -7, 0):
        _rig_raw(rig, p + dp, tl)
        time.sleep(0.11)


def mode_dance(rig, cs3, eager):
    """CALM/EAGER tempo transition: a short rhythmic 'dance' (head sway + antenna + soft beats)
    at the NEW tempo, so the patience change is FELT, not just toggled. Eager = quick/snappy;
    calm = slow/swaying. Driven from one laptop timeline so head + sound stay in sync."""
    if cs3:
        cs3.mode(eager)                                  # screen 'eager'/'calm' + antenna tempo
    beat  = 0.20 if eager else 0.52                      # eager = quick; calm = slow, longer sway
    beats = 8    if eager else 6
    amp   = 15   if eager else 10                        # pan sway amplitude (deg)
    p  = rig.pan  if rig is not None else 0
    tl = rig.tilt if rig is not None else 0
    for i in range(beats):
        d = amp if (i % 2 == 0) else -amp                # sway left/right on the beat
        if rig is not None: _rig_raw(rig, p + d, tl)
        if cs3: (cs3.beep() if eager else cs3.beep_low())
        time.sleep(beat)
    if rig is not None: _rig_raw(rig, p, tl)             # settle back to center


def watch_text(spec, ctx):
    """Plain-English line for the CoreS3 screen ('Looking for: …')."""
    labels = [lbl for _, lbl in spec_summary(spec) if lbl]
    if labels:
        return "; ".join(labels)
    return (ctx or "").strip()[:80]


# --- relevance layer (v0): planner-chosen object whitelist + focus tier -------------------
# The planner emits `detect` (what to look for at all) and `focus` (what can trigger a report).
# On closed COCO YOLO the vocab is ignored, so `detect` is applied as a post-detection whitelist,
# with a small synonym map so planner nouns map onto COCO labels.
COCO_SYNONYMS = {
    "desk": {"dining table"}, "table": {"dining table"},
    "monitor": {"tv"}, "screen": {"tv"}, "display": {"tv"}, "television": {"tv"},
    "bag": {"backpack", "handbag", "suitcase"}, "handbag": {"handbag"}, "backpack": {"backpack"},
    "phone": {"cell phone"}, "mobile": {"cell phone"}, "cellphone": {"cell phone"},
}


def _expand(labels):
    """planner nouns -> the set of lowercase labels that satisfy them (forgiving for closed YOLO)."""
    out = set()
    for w in labels or []:
        w = str(w).strip().lower()
        if not w:
            continue
        out.add(w)
        out |= COCO_SYNONYMS.get(w, set())
    return out


class _FilteredDetector:
    """Wraps a detector; drops any box whose label is not in relevance['allow'] (when set).
    `allow` is a live set, so re-planning updates the whitelist with no detector rebuild.
    'person' is always kept — people are the subject of every social relation."""
    def __init__(self, base, relevance):
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "rel", relevance)
        object.__setattr__(self, "_ver", -1)          # last set_vocab version applied

    def detect(self, image):
        rel = self.rel
        # open-vocab detectors (yoloworld / gdino) expose set_vocab -> re-prompt when the plan
        # changes the object set. Closed COCO YOLO has no set_vocab -> the whitelist below does it.
        want = rel.get("want_classes")
        if want and rel.get("classes_ver", 0) != self._ver and hasattr(self.base, "set_vocab"):
            try:
                self.base.set_vocab(want)
                object.__setattr__(self, "_ver", rel["classes_ver"])
                print(f"[relevance] detector re-prompted -> {sorted(want)}")
            except Exception as ex:
                print(f"[relevance] set_vocab failed: {ex}")
        dets = self.base.detect(image)
        allow = rel.get("allow")
        if not allow:
            return dets
        return [d for d in dets if str(d.label).lower() in allow or d.label == "person"]

    def __getattr__(self, k):
        return getattr(object.__getattribute__(self, "base"), k)


def _focus_ok(e, viz, focus):
    """Focus gate at story-open: an object gaze/point entry may only OPEN a story when its target
    is the right object. If the entry names `on`, that specific object is required; otherwise any
    focus object. Person-centric entries (approach/gather/joint/prox/F-form/turn) and truth-only
    object relations (lean/hands — no viz target) pass through. Empty target set -> allow all."""
    on = e.get("on")
    want = _expand([on] if isinstance(on, str) else on) if on else set(focus)
    if not want:
        return True
    ids = set(e.get("all", [])) | set(e.get("any", [])) | set(e.get("then", []))
    if not (ids & {1, 4}):                     # no gaze/point target to verify -> people/lean/hands: allow
        return True
    dets = viz.get("dets", [])
    targets = set()
    for _, h in viz.get("hits", []):
        di = h.get("det") if isinstance(h, dict) else None
        if isinstance(di, int) and 0 <= di < len(dets):
            targets.add(str(dets[di].label).lower())
    return bool(targets & want)


# electric / cyber green — LIVE-ONLY, used for the BlazePose skeleton (BGR of #1EFF5A)
C_SKEL = (90, 255, 30)

# short labels for the bottom relation ribbon (rows 1–11), synced with the web UI vocab
LIVE_ABBR = {1: "gaze", 2: "joint", 3: "eye", 4: "point", 5: "prox", 6: "F-form",
             7: "appr", 8: "lean", 9: "hands", 10: "group", 11: "turn"}


def draw_relation_ribbon(fr, W, H, truth, entries):
    """Bottom-of-frame vocabulary strip: rows 1–11 as 'N abbr', lit yellow-green when
    detected THIS frame; watch-entry groups (≥2 ids) bracketed above with an AND/OR/THEN
    label that lights blue when its condition holds. Detection-only — no watch/cooldown."""
    import cv2
    n = 11
    margin = 34
    step = (W - 2 * margin) / (n - 1)
    xs = {i: int(margin + (i - 1) * step) for i in range(1, n + 1)}
    base_y = H - 18                                    # the label row
    for i in range(1, n + 1):                          # 1) the vocabulary labels
        on = bool(truth.get(i, False))
        col = C_YELLOW if on else (165, 165, 165)
        txt = f"{i} {LIVE_ABBR.get(i, '')}"
        (tw, _), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        draw_text(fr, txt, (xs[i] - tw // 2, base_y), col, 0.5, 2 if on else 1)

    level = [0]                                        # 2) the conjunction brackets, stacked up
    def connect(ids, label, lit):
        ids = [i for i in ids if i in xs]
        if len(ids) < 2:
            return
        y = base_y - 34 - level[0] * 28
        lo = min(ids, key=lambda i: xs[i]); hi = max(ids, key=lambda i: xs[i])
        col = C_CYAN if lit else (110, 110, 110)
        th = 3 if lit else 1
        cv2.line(fr, (xs[lo], y), (xs[hi], y), col, th, cv2.LINE_AA)        # the bar
        for i in ids:                                                       # down-ticks to each
            cv2.line(fr, (xs[i], y), (xs[i], base_y - 14), col, th, cv2.LINE_AA)
        mx = (xs[lo] + xs[hi]) // 2
        (tw, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        draw_text(fr, label, (mx - tw // 2, y - 7), col, 0.6, 2)
        level[0] += 1

    for e in entries:
        on = lambda i: bool(truth.get(i, False))
        if len(e.get("then", [])) >= 2:
            connect(e["then"], "THEN", all(on(i) for i in e["then"]))
        if len(e.get("all", [])) >= 2:
            connect(e["all"], "AND", all(on(i) for i in e["all"]))
        if len(e.get("any", [])) >= 2:
            connect(e["any"], "OR", any(on(i) for i in e["any"]))


def main():
    ap = argparse.ArgumentParser(description="VLM-first attention system (plan -> watch -> record)")
    ap.add_argument("--camera", default=None, help="M5 URL (default rig.py CAM_URL) or webcam index")
    ap.add_argument("--context", default=None, help="context sentence (overrides --context-file)")
    ap.add_argument("--context-file", default="context.txt",
                    help="re-plans when this file changes (default context.txt)")
    ap.add_argument("--grammar", default="free", help="planner grammar (study winner: free)")
    ap.add_argument("--plan-frame", action="store_true",
                    help="(kept for back-compat; situated planning is now the default)")
    ap.add_argument("--text-only-plan", action="store_true",
                    help="plan from the context sentence ONLY (no frame). Default is situated: the "
                         "planner sees the current frame, enumerates the objects present, and tiers "
                         "them by the context.")
    ap.add_argument("--spec-file", default=None,
                    help="JSON watch-spec file: SKIP the planner and execute exactly this "
                         "(deterministic per-item testing; context hot-reload disabled)")
    ap.add_argument("--detector", default="yolo", help="yolo | yoloworld | gdino")
    ap.add_argument("--lean-deg", type=float, default=25.0,
                    help="lean-in threshold: torso tilt off image-vertical (deg) to count as leaning "
                         "(raise to cut seated/angled false positives)")
    ap.add_argument("--vocab", default="person,laptop,monitor,keyboard,mouse,cup,bottle,chair,"
                                       "desk,bag,book,cell phone,potted plant")
    ap.add_argument("--conf", type=float, default=0.3)
    ap.add_argument("--persist", type=int, default=2, help="frames a relation must hold")
    ap.add_argument("--cooldown", type=float, default=60.0, help="habituation per entry (s)")
    ap.add_argument("--tau-gap", type=float, default=3.0,
                    help="THEN-gate: min onset gap (s) for a 'then' to count as an ORDERED event; "
                         "tighter than this falls back to 'and' within the window")
    ap.add_argument("--confirm", action="store_true",
                    help="VLM double-checks the frame when an entry fires (judge confirm)")
    ap.add_argument("--burst-n", type=int, default=10,
                    help="MAX panels per story (event-driven: a panel is added only when the scene "
                         "CHANGES, so short stories stay short and long ones use up to this many)")
    ap.add_argument("--burst-interval", type=float, default=4.0,
                    help="minimum seconds between panels (a panel is taken only if the scene also changed)")
    ap.add_argument("--scene-diff", type=float, default=8.0,
                    help="image-change fallback: mean gray-diff vs the last panel to count as a change")
    ap.add_argument("--linger", type=float, default=6.0,
                    help="keep capturing this many seconds AFTER the triggering relation drops, so the "
                         "follow-through is recorded (avoids 1-shot stories the note can't back up)")
    ap.add_argument("--no-sound", action="store_true", help="disable the audible 'noticed' cue")
    ap.add_argument("--rig", action="store_true",
                    help="pan-tilt mode: SCAN poses while all entries are quiet; on fire -> "
                         "buzzer+nod, STAY and watch the story; resume scanning when bored")
    ap.add_argument("--port", default=None, help="servo serial port (default: rig.py SERIAL_PORT)")
    ap.add_argument("--cores3", default=None,
                    help="CoreS3 I/O board serial port (e.g. /dev/tty.usbmodem101); omit to disable")
    ap.add_argument("--wait", action="store_true",
                    help="start IDLE with no default watch; wait for the first delegate "
                         "(UI) before planning")
    ap.add_argument("--sweep-capture", action="store_true",
                    help="on each full sweep (after a delegate), save an annotated frame every "
                         "--sweep-step degrees (green boxes = detected; yellow = focus tier) plus "
                         "plan.json (seen/detect/focus + watch-spec) to feed/sweeps/<timestamp>/")
    ap.add_argument("--sweep-frames", type=int, default=7,
                    help="how many frames the sweep grabs, evenly spaced across the pan range "
                         "(enough to cover the room; 7 is plenty). Set 0 to use --sweep-step instead")
    ap.add_argument("--sweep-step", type=int, default=15,
                    help="fallback: degrees between sweep frames (only used when --sweep-frames 0)")
    ap.add_argument("--sweep-dwell", type=float, default=0.8,
                    help="seconds to dwell at each sweep-capture pan step (settle + detect + show); "
                         "raise if frames blur or boxes are missing")
    ap.add_argument("--replan-interval", type=float, default=300.0,
                    help="sweep mode: auto re-sweep (VLM re-plan, same context) this often to refresh "
                         "the vocab/watch-spec so NEW objects get picked up")
    ap.add_argument("--idle-replan", type=float, default=30.0,
                    help="sweep mode: if something fired then the scene went quiet this long, re-sweep "
                         "early (a dead room that never fired anything waits for --replan-interval)")
    ap.add_argument("--nod", action="store_true",
                    help="enable head nods / come-look tilt bobs. OFF by default — a heavy head droops "
                         "when the tilt servo dips; keep off until the head is lighter/counterweighted")
    ap.add_argument("--look-secs", type=float, default=6.0, help="rig: dwell per scan pose (legacy mode)")
    ap.add_argument("--watch-end", type=float, default=8.0,
                    help="rig: seconds of quiet before leaving a WATCH pose")
    ap.add_argument("--worth", type=float, default=0.0,
                    help="with --confirm: min reportability worth to record")
    ap.add_argument("--offline", action="store_true", help="fake planner/judge, no API key")
    ap.add_argument("--save", action="store_true", help="records + relation_log.jsonl to feed dir")
    ap.add_argument("--serve", action="store_true", help="web UI (live + feed)")
    ap.add_argument("--web-port", type=int, default=8000)
    ap.add_argument("--feed-dir", default="feed")
    args = ap.parse_args()
    if args.offline:
        os.environ["SECONDATTN_OFFLINE"] = "1"
    if args.camera is None:
        from rig import CAM_URL
        args.camera = CAM_URL
    print(f"[camera] {args.camera}")

    # ---- plan ----
    fallback = "A shared lab space; notice socially meaningful moments."
    ctx = args.context or read_context(args.context_file, fallback)
    ctx_mtime = os.path.getmtime(args.context_file) if (not args.context and
                os.path.exists(args.context_file)) else None

    # live relevance state (updated per plan): allow=closed-YOLO whitelist (synonym-expanded);
    # focus=story-open gate; want_classes=RAW nouns fed to an open-vocab detector's set_vocab;
    # classes_ver bumps so the detector re-prompts lazily on its next frame.
    relevance = {"allow": None, "focus": set(), "want_classes": None, "classes_ver": 0}

    def _raw(labels):
        return {str(w).strip().lower() for w in (labels or []) if str(w).strip()}

    def apply_relevance(spec):
        """Push a plan's detect/focus into the live relevance state.
        allow/focus use the synonym-expanded set (matches whatever labels a detector emits);
        want_classes uses the RAW planner nouns (the open-vocab prompt — e.g. 'robot arm')."""
        if not spec:
            relevance["allow"], relevance["focus"] = None, set()
            return
        relevance["allow"] = _expand(spec.get("detect")) or None
        relevance["focus"] = _expand(spec.get("focus"))
        # ALWAYS prompt for 'person': on open-vocab YOLO-World the model only finds what it is
        # prompted for, and people drive every social relation, so pin them in unconditionally.
        want = _raw(spec.get("detect")) | _raw(spec.get("focus")) | {"person"}
        relevance["want_classes"] = want
        relevance["classes_ver"] += 1                    # detector re-prompts on next frame
        if relevance["allow"]:
            print(f"[relevance] detect={sorted(relevance['allow'])}  focus={sorted(relevance['focus'])}")

    def make_plan(context, jpeg=None):
        # SITUATED planning by default: give the planner the current frame so it enumerates the
        # objects actually present and tiers them by the context (--text-only-plan to disable).
        jp = None if args.text_only_plan else jpeg
        r = plan(context, jpeg=jp, grammar=args.grammar)
        if r["spec"] is None:                            # transient parse/truncation -> retry once
            print(f"[plan] failed ({r['violations']}); retrying once…")
            r = plan(context, jpeg=jp, grammar=args.grammar)
        if r["spec"] is None:                            # still bad -> DON'T crash; keep current plan
            print(f"[plan] FAILED again: {r['violations']}\n       raw head: {r['raw'][:200]} — keeping current plan")
            return None
        if r["violations"]:
            print(f"[plan] WARNING violations: {r['violations']} (executing anyway)")
        print(f"[plan] context: {context}")
        for expr, label in spec_summary(r["spec"]):
            print(f"[plan]   watch {expr}   ({label})")
        print(f"[plan]   why: {r['spec'].get('why','')}")
        if r["spec"].get("missing"):
            print(f"[plan]   MISSING (vocab {VOCAB_VERSION} gap): {r['spec']['missing']}")
        apply_relevance(r["spec"])                # v0: object whitelist + focus tier
        if cs3:                                   # CoreS3: armed + show the SHORT brief (not the matrix)
            if args.no_sound: cs3.mute()          # re-assert mute before the ARMED chirp
            cs3.armed(); cs3.prompt((context or "").strip()[:80])
        return r["spec"], WatchExecutor(r["spec"], persist=args.persist, cooldown=args.cooldown,
                                        tau_gap=args.tau_gap)

    taste = ReportabilityTaste()                 # only used by --confirm (back-end judge)
    UI = None
    if args.serve:                               # the VLM-FIRST surface: context box, not taste box
        import attention_ui
        UI = attention_ui.serve(args)

    def push_plan():
        if UI is not None:
            s = spec or {}
            with UI.LOCK:
                UI.STATE["context"] = ctx
                UI.STATE["why"] = s.get("why", "")
                UI.STATE["entries"] = spec_summary(spec) if spec else []
                UI.STATE["seen"] = list(s.get("seen") or [])       # VLM's full frame enumeration
                UI.STATE["detect"] = list(s.get("detect") or [])   # kept (context tier)
                UI.STATE["focus"] = list(s.get("focus") or [])     # can trigger (focus tier)

    # --- CoreS3 I/O board (screen+sound+touch), separate USB serial; optional ---
    # STOP is handled HERE in the serial-reader thread; everything else is queued for the loop.
    stop_evt = threading.Event()
    rec_state = {"on": False}                 # frozen: voice PTT removed; loop guards read it as never-recording
    in_q = queue.Queue()

    def _on_cores3(s):
        if s.startswith("IN TAP STOP"):
            stop_evt.set()
        else:
            in_q.put(s)                      # EAGER / CALM / BODYTAP -> main loop

    cs3 = None
    if args.cores3:
        try:
            cs3 = CoreS3Link(args.cores3, on_input=_on_cores3)
            print(f"[cores3] linked on {args.cores3}")
            if args.no_sound:
                # opening the port resets the board (it reboots to volume 100), so a single early
                # VOL 0 can be dropped mid-boot. Re-send a few times over the first ~2.5s.
                def _ensure_mute():
                    for _ in range(8):
                        time.sleep(0.35)
                        try: cs3.mute()
                        except Exception: pass
                threading.Thread(target=_ensure_mute, daemon=True).start()
                print("[cores3] muting (--no-sound)")
        except Exception as e:
            print(f"[cores3] link failed ({e}); continuing without it")

    scan = []                                     # filled when --rig (used by survey() too)
    rig = None
    if args.rig:                                  # motion chassis: same brain, scanning body
        from rig import GimbalRig, ServoOnlyRig, SERIAL_PORT
        import rig as _rigmod; _rigmod.SETTLE_S = 0.12   # less dead-time per move (USB cam: safe; smoother live, snappier nod)
        if str(args.camera).isdigit():            # USB webcam on the head: servos only,
            rig = ServoOnlyRig(port=args.port or SERIAL_PORT)   # frames via VideoCapture
            frames = frame_source(str(args.camera))
        else:                                     # M5: rig owns the stream (fresh-frame)
            rig = GimbalRig(cam_url=str(args.camera), port=args.port or SERIAL_PORT)

            def _rig_frames():
                while True:
                    yield rig.get_frame()
            frames = _rig_frames()
        scan = [(p, 0) for p in (-50, -25, 0, 25, 50)]   # PAN-ONLY scan; tilt reserved for interaction (nods)
        pose_i, mstate = 0, "SCAN"
        pose_until, last_active = 0.0, time.time()
        print(f"[rig] live — {len(scan)} scan poses; fire -> beep+nod+WATCH; "
              f"resume after {args.watch_end:.0f}s quiet")
    else:
        frames = frame_source(str(args.camera))
    first = next(frames)                          # camera live BEFORE planning; with
    if args.spec_file:                            # --plan-frame the planner sees the scene
        spec = json.load(open(args.spec_file))
        executor = WatchExecutor(spec, persist=args.persist, cooldown=args.cooldown,
                                 tau_gap=args.tau_gap)
        apply_relevance(spec)                     # spec-file may carry detect/focus too
        ctx, ctx_mtime = f"[spec-file] {args.spec_file}", None
        print("[plan] bypassed — executing spec from file:")
        for expr, label in spec_summary(spec):
            print(f"[plan]   watch {expr}   ({label})")
    elif args.wait and not args.context:
        spec, executor, ctx, ctx_mtime = None, None, "", None   # idle until first delegate
        apply_relevance(None)                     # no whitelist yet -> detect everything until a delegate
        print("[plan] waiting for first delegate (UI)…")
        if cs3: cs3.step("REST")
    else:
        _r0 = make_plan(ctx, cv2.imencode(".jpg", first)[1].tobytes())
        spec, executor = _r0 if _r0 else (None, None)
    push_plan()
    engine = RelationEngine(_FilteredDetector(
                                make_detector(args.detector,
                                              [v.strip() for v in args.vocab.split(",")],
                                              conf=args.conf),
                                relevance),
                            lean_deg=args.lean_deg)
    if args.save:
        os.makedirs(args.feed_dir, exist_ok=True)
        rel_log = open(os.path.join(args.feed_dir, "relation_log.jsonl"), "a")
    banner = None
    bursts = []                                   # open story bursts (entries mid-narrative)

    def _draw_dets(vis, dets):
        """Box every detection, colored by tier: focus=yellow (thick), context=green, other=grey."""
        focus = relevance.get("focus") or set()
        allow = relevance.get("allow")
        for d in dets:
            lab = str(d.label).lower()
            if lab in focus:
                col, th = C_YELLOW, 4
            elif (not allow) or lab in allow or lab == "person":
                col, th = C_GREEN, 2
            else:
                col, th = (150, 150, 150), 1
            x1, y1, x2, y2 = map(int, d.box)
            cv2.rectangle(vis, (x1, y1), (x2, y2), col, th, cv2.LINE_AA)
            draw_text(vis, f"{d.label} {float(getattr(d, 'conf', 0)):.2f}",
                      (x1, max(12, y1 - 6)), col, 0.55, 2)
        return vis

    def sweep_and_plan(context):
        """SWEEP-FIRST planning (the --sweep-capture flow). The VLM does ALL the perception here;
        no CV detector runs until AFTER the head settles.
          1) FAST raw sweep — grab one frame per pan angle (quick, no boxes);
          2) tile the frames into a GRID contact-sheet (keeps per-view resolution) and make ONE VLM
             call: it tiers the whole room, emits the watch-spec, AND boxes every object it sees;
          3) map each VLM box back to its angle frame and draw by tier (focus=yellow, context=green)
             — the labeled dataset for your analysis;
          4) move to the angle with the most focus boxes and STAY — from here CV takes over.
        Returns (spec, executor)."""
        nonlocal mstate, last_active
        from rig import PAN_LIMIT
        dwell = max(0.15, float(args.sweep_dwell))
        lo, hi = -int(PAN_LIMIT), int(PAN_LIMIT)
        if args.sweep_frames and args.sweep_frames >= 2:                # target a FRAME COUNT (evenly spaced, ends included)
            n = int(args.sweep_frames)
            poses = sorted({int(round(lo + (hi - lo) * k / (n - 1))) for k in range(n)})
        else:                                                          # or fall back to a fixed degree step
            poses = list(range(lo, hi + 1, max(5, int(args.sweep_step))))
        # 1) fast raw sweep
        grabbed = []
        for pan in poses:
            try:
                rig.move_to(pan, 0)
            except Exception:
                pass
            time.sleep(dwell)
            fr = None
            for _ in range(3):                            # flush stale buffered frames -> fresh angle
                try:
                    fr = next(frames)
                except StopIteration:
                    fr = None; break
            if fr is None:
                continue
            grabbed.append((pan, fr.copy()))
            if UI is not None:
                with UI.LOCK:
                    UI.STATE["jpg"] = cv2.imencode(".jpg", fr)[1].tobytes()
        if not grabbed:
            return make_plan(context)                     # no frames -> normal single-frame plan
        # 2) GRID contact-sheet (NOT a wide strip) -> preserves per-view resolution so the VLM can
        #    box accurately. ONE call returns the watch-spec + tiers + per-object boxes.
        N = len(grabbed)
        cols = max(1, int(math.ceil(math.sqrt(N)))); rows = int(math.ceil(N / cols))
        CW, CH = 480, 360
        grid = np.zeros((rows * CH, cols * CW, 3), dtype=np.uint8)
        cells = []                                        # per tile: pan, original frame, grid origin
        for i, (pan, fr) in enumerate(grabbed):
            r, c = divmod(i, cols)
            grid[r * CH:r * CH + CH, c * CW:c * CW + CW] = cv2.resize(fr, (CW, CH))
            cells.append({"pan": pan, "fr": fr, "x0": c * CW, "y0": r * CH})
        GW, GH = cols * CW, rows * CH
        res = make_plan(context, cv2.imencode(".jpg", grid)[1].tobytes())
        if res is None:                                   # planner failed -> keep previous plan, don't crash
            return None
        spec_, exec_ = res
        # 3) map each VLM box (normalized over the grid) back to its angle frame; draw by tier
        ts = time.strftime("%Y%m%d_%H%M%S")
        out = os.path.join(args.feed_dir, "sweeps", ts); os.makedirs(out, exist_ok=True)
        per = {i: [] for i in range(N)}                   # frame idx -> [(label, tier, [x0,y0,x1,y1] px)]
        for b in ((spec_ or {}).get("boxes") or []):
            try:
                x0, y0, x1, y1 = [float(v) for v in list(b.get("box", []))[:4]]
            except Exception:
                continue
            gx0, gy0, gx1, gy1 = x0 * GW, y0 * GH, x1 * GW, y1 * GH
            c, r = int(((gx0 + gx1) / 2) // CW), int(((gy0 + gy1) / 2) // CH)   # cell holding the center
            i = r * cols + c
            if not (0 <= i < N):
                continue
            cell = cells[i]; fh, fw = cell["fr"].shape[:2]
            lx0 = max(0.0, (gx0 - cell["x0"]) * fw / CW); ly0 = max(0.0, (gy0 - cell["y0"]) * fh / CH)
            lx1 = min(float(fw), (gx1 - cell["x0"]) * fw / CW); ly1 = min(float(fh), (gy1 - cell["y0"]) * fh / CH)
            if lx1 - lx0 >= 2 and ly1 - ly0 >= 2:
                per[i].append((str(b.get("label", "?")), str(b.get("tier", "context")), [lx0, ly0, lx1, ly1]))

        def _draw_vlm(vis, boxes):
            for lab, tier, (x0, y0, x1, y1) in boxes:                  # focus = RED, context = GREEN
                col, th = (C_RED, 4) if tier == "focus" else (C_GREEN, 2)
                cv2.rectangle(vis, (int(x0), int(y0)), (int(x1), int(y1)), col, th, cv2.LINE_AA)
                draw_text(vis, lab, (int(x0), max(14, int(y0) - 6)), col, 0.6, 2)
            return vis

        # the grid itself, with the VLM boxes + labels drawn over it -> panorama.jpg
        gridvis = grid.copy()
        for i in range(N):
            cell = cells[i]; fh, fw = cell["fr"].shape[:2]
            for lab, tier, (x0, y0, x1, y1) in per[i]:
                col = C_RED if tier == "focus" else C_GREEN
                gx0 = int(cell["x0"] + x0 * CW / fw); gy0 = int(cell["y0"] + y0 * CH / fh)
                gx1 = int(cell["x0"] + x1 * CW / fw); gy1 = int(cell["y0"] + y1 * CH / fh)
                cv2.rectangle(gridvis, (gx0, gy0), (gx1, gy1), col, 2, cv2.LINE_AA)
                draw_text(gridvis, lab, (gx0, max(12, gy0 - 5)), col, 0.5, 2)
        cv2.imwrite(os.path.join(out, "panorama.jpg"), gridvis)
        best = (-1, 0); shots = []
        for i, (pan, fr) in enumerate(grabbed):
            vis = _draw_vlm(fr.copy(), per[i])
            cv2.imwrite(os.path.join(out, f"pan_{pan:+04d}.jpg"), vis)
            if UI is not None:
                with UI.LOCK:
                    UI.STATE["jpg"] = cv2.imencode(".jpg", vis)[1].tobytes()
            sc = sum(3 if tier == "focus" else 1 for _, tier, _ in per[i])   # focus weighs 3x
            if sc > best[0]:
                best = (sc, pan)
            print(f"[sweep] pan {pan:+d}°: {len(per[i])} VLM boxes (score {sc})")
            shots.append({"pan": pan, "file": f"pan_{pan:+04d}.jpg",
                          "dets": [{"label": lab, "tier": tier, "box": [round(v, 1) for v in bx]}
                                   for lab, tier, bx in per[i]]})
        # coverage: for each planned object, in how many frames did the VLM box it? 0 = phantom.
        cover = {}
        for lbl in ((spec_ or {}).get("detect") or []):
            ll = str(lbl).lower()
            cover[lbl] = sum(1 for sh in shots for d in sh["dets"] if str(d["label"]).lower() == ll)
        never = [o for o, n in cover.items() if n == 0]
        if never:
            print(f"[sweep] planned but VLM boxed in NO angle: {never}")
        meta = {"time": ts, "context": context, "grid": f"{cols}x{rows}", "n_frames": len(grabbed),
                "poses": [p for p, _ in grabbed],
                "seen": (spec_ or {}).get("seen"), "detect": (spec_ or {}).get("detect"),
                "focus": (spec_ or {}).get("focus"), "coverage": cover, "watch_spec": spec_,
                "shots": shots, "panorama": "panorama.jpg",
                "richest_pan": best[1], "richest_score": best[0]}
        with open(os.path.join(out, "plan.json"), "w") as f:
            json.dump(meta, f, indent=2)
        # 4) orient to the richest view and STAY — CV (YOLO-World + mediapipe) takes over from here
        try:
            rig.move_to(best[1], 0)
        except Exception:
            pass
        mstate, last_active = "WATCH", time.time()
        print(f"[sweep] {len(shots)} frames + grid -> {out}  ·  richest pan {best[1]:+d}° "
              f"(score {best[0]}) — staying")
        return spec_, exec_

    def survey():
        """Non-sweep 'look around' gesture (used when --sweep-capture is off). Needs --rig."""
        if rig is None:
            return
        for pn in (-50, -25, 0, 25, 50, 0):
            try:
                rig.move_to(pn, 0)
            except Exception:
                pass

    def delegate(context, jpeg):
        """A delegate -> a plan. With --sweep-capture + rig: sweep-first (panorama plan + dataset +
        orient). Otherwise: plan from the single frame, then a look-around gesture."""
        if args.sweep_capture and rig is not None:
            return sweep_and_plan(context)
        res = make_plan(context, jpeg)
        if res is None:
            return None
        survey()
        return res

    def _finalize(b):
        """Narrate (VLM) + publish a completed story — runs in a BACKGROUND thread so the
        live loop never blocks on the network."""
        n = len(b["shots"])
        strip = make_strip(b["shots"])
        story = " → ".join(dict.fromkeys(b["traces"]))
        if args.offline:
            note, worth = f"{b['label']}: {story}", b["worth"]
        else:
            try:
                r = run_judge(cv2.imencode(".jpg", strip)[1].tobytes(), None, taste, story=story)
                note, worth = r["note"], r["worth"]
            except Exception as e:
                print(f"[judge] error: {e}")
                note, worth = f"{b['label']}: {story}", b["worth"]
        note = f"{note} ({n}-shot story)"
        print(f"[MOMENT] {b['label']} :: {note}")
        fid = time.strftime("%Y%m%d_%H%M%S_") + f"{int(time.time()*1000)%1000:03d}"
        rec = {"time": time.strftime("%H:%M:%S"),
               "worth": worth if worth is not None else "—",
               "why": "watch-spec", "note": note, "thumb": f"thumb_{fid}.jpg",
               "frame": f"frame_{fid}.jpg", "label": b["label"], "shots": n,
               "story": story, "truth": b["truth"]}
        publish(strip, rec, args, UI)

    def _report_found(label, fr, truth):
        """P2 (find-and-share): publish a lightweight 'the focus object appeared' moment. No judge."""
        fid = time.strftime("%Y%m%d_%H%M%S_") + f"{int(time.time()*1000)%1000:03d}"
        rec = {"time": time.strftime("%H:%M:%S"), "worth": 0.9, "why": "focus object appeared",
               "note": f"'{label}' appeared — the thing you asked to watch",
               "thumb": f"thumb_{fid}.jpg", "frame": f"frame_{fid}.jpg",
               "label": f"found:{label}", "shots": 1, "story": f"{label} appeared",
               "truth": {k: int(v) for k, v in truth.items()}}
        publish(make_strip([fr]), rec, args, UI)
        print(f"[P2] found focus object: {label}")

    import itertools
    cur_cd = args.cooldown                         # live cooldown (eager/calm tunes it)
    ACK_SEC = 8.0                                  # after LOOK!, how long to wait for you to look
    awaiting_ack = False                           # currently bidding "come look" + waiting for ack
    ack_deadline = 0.0
    focus_seen, focus_found_at = {}, {}            # P2 onset tracking + per-object habituation
    OBJ_REAPPEAR = 4.0                             # object must be gone this long to count as a NEW appearance
    last_plan = time.time()                        # sweep-mode auto re-plan timers
    last_event = 0.0                               # last time a story/found fired (0 = none since plan)
    if executor is not None:
        survey()                                   # initial survey if we planned at startup
    for fr in itertools.chain([first], frames):
        t = time.time()
        H, W = fr.shape[:2]

        # ---- CoreS3 queued inputs (EAGER/CALM/BODYTAP); STOP/PTT are handled in the reader thread ----
        while not in_q.empty():
            msg = in_q.get_nowait()
            if msg.startswith("IN TAP EAGER"):
                cur_cd = max(5.0, cur_cd * 0.5)
                if spec is not None: executor = WatchExecutor(spec, persist=args.persist, cooldown=cur_cd, tau_gap=args.tau_gap)
                mode_dance(rig, cs3, True)                          # eager: quick tempo dance
                if cs3 and spec is not None: cs3.prompt((ctx or "").strip()[:80])
                print(f"[mode] EAGER — cooldown {cur_cd:.0f}s")
            elif msg.startswith("IN TAP CALM"):
                cur_cd = cur_cd * 2.0
                if spec is not None: executor = WatchExecutor(spec, persist=args.persist, cooldown=cur_cd, tau_gap=args.tau_gap)
                mode_dance(rig, cs3, False)                         # calm: slow swaying tempo
                if cs3 and spec is not None: cs3.prompt((ctx or "").strip()[:80])
                print(f"[mode] CALM — cooldown {cur_cd:.0f}s")
            elif msg.startswith("IN BODYTAP"):
                # head-tap = "not this, now" → puzzled, then RE-SEARCH: full sweep like a fresh
                # delegate and re-lock. The SAME context can capture more than one kind of moment,
                # so we KEEP what it already caught: any open story is finalized + published (not
                # scrapped) — the tap only means "stop following THIS one and look again".
                if spec is not None:
                    print("[cores3] head-tap → confused, re-searching (keeping what it caught)")
                    if cs3: cs3.step("CONFUSED")                    # 'huh?' + wobble chirp + amber blink
                    confused_shake(rig)
                    time.sleep(0.5)
                    for b in bursts:                               # publish-in-place, don't discard
                        threading.Thread(target=_finalize, args=(b,), daemon=True).start()
                    bursts.clear(); awaiting_ack = False           # handed off → stop following them
                    survey()                                       # full sweep, same as a new brief
                    executor = WatchExecutor(spec, persist=args.persist, cooldown=cur_cd, tau_gap=args.tau_gap)   # re-lock
                    if cs3: cs3.prompt((ctx or "").strip()[:80])   # back to the brief → watching
                else:
                    print("[cores3] head-tap ignored (nothing being watched yet)")
        if stop_evt.is_set():
            break

        # hot re-plan: from the web UI context box…
        if UI is not None and not args.spec_file:
            with UI.LOCK:
                pending = UI.STATE.pop("pending_context", None)
                UI.STATE["pending_context"] = None
            if pending:
                ctx = pending
                _res = delegate(ctx, cv2.imencode(".jpg", fr)[1].tobytes())
                if _res:
                    spec, executor = _res; push_plan()
                    last_plan = t; last_event = 0.0
                    banner = (t + 4, f"re-planned: {ctx[:60]}", C_CYAN)
                else:
                    banner = (t + 4, "plan failed — kept previous", C_RED)
        # webui gallery yes/no → CoreS3 body backchannel (attention_ui sets pending_judge)
        if cs3 and UI is not None:
            with UI.LOCK:
                pj = UI.STATE.pop("pending_judge", None)
            if pj == "keep":   cs3.keep()
            elif pj == "not":  cs3.not_this()
        # …or when context.txt changes
        if ctx_mtime is not None:
            try:
                m = os.path.getmtime(args.context_file)
                if m != ctx_mtime:
                    ctx_mtime = m
                    ctx = read_context(args.context_file, fallback)
                    _res = delegate(ctx, cv2.imencode(".jpg", fr)[1].tobytes())
                    if _res:
                        spec, executor = _res; push_plan()
                        last_plan = t; last_event = 0.0
                        banner = (t + 4, f"re-planned: {ctx[:60]}", C_CYAN)
                    else:
                        banner = (t + 4, "plan failed — kept previous", C_RED)
            except OSError:
                pass

        truth, viz = engine.step(fr, t)
        if executor is not None:
            fired, statuses = executor.step(truth, t)
        else:
            fired, statuses = [], []              # idle: planned nothing yet
        if rec_state["on"] or awaiting_ack:
            fired = []                            # delegating, or mid-bid waiting for you: don't open new catches
        cur_entries = executor.entries if executor is not None else []

        # ---- LOOK! acknowledgement: did you look? (eye-contact #3) → resume; else time out ----
        if awaiting_ack:
            if bool(truth.get(3)):                # you looked at it = joint attention closed
                awaiting_ack = False
                if rig is not None and args.nod: rig.nod()     # "good — you saw it" (off: heavy head)
                if cs3: cs3.prompt((ctx or "").strip()[:80])   # back to the brief → resume watching
                print("[bid] acknowledged (you looked) — resume")
            elif t > ack_deadline:
                awaiting_ack = False
                if cs3: cs3.prompt((ctx or "").strip()[:80])   # no response → resume quietly (never nag)
                print("[bid] no response — resume")
        if args.save and executor is not None:        # only log once a delegate exists (aligns to experiment start)
            rel_log.write(json.dumps({"t": round(t, 2),
                                      "truth": {k: int(v) for k, v in truth.items()}}) + "\n")

        for e in fired:                                   # ---- a watched moment opens a STORY ----
            if not _focus_ok(e, viz, relevance["focus"]):  # focus gate: gaze/point must be at a focus object
                print(f"[gate] {e['label']} suppressed (no focus object involved)")
                continue
            label = e["label"]
            rec_ok, note, worth = True, label, None
            if args.confirm:
                ids = e["all"] + e["any"] + e["then"]
                claim = label + " — i.e. " + "; ".join(VOCAB[i].split("—")[1].strip()
                                                       for i in ids if i in VOCAB)
                r = run_judge(cv2.imencode(".jpg", fr)[1].tobytes(), None, taste, confirm=claim)
                rec_ok = r["confirmed"] and r["worth"] >= args.worth
                note, worth = r["note"], r["worth"]
                if not r["confirmed"]:
                    print(f"[VETO ] {label} :: {note}")
                    banner = (t + 3, f"VLM veto: {label}", C_RED)
            if rec_ok:                                    # burst: keep shooting while it unfolds
                # ---- FOUND: quiet "I noticed it" ----
                if cs3:
                    cs3.found()                           # 'Found!' + single note
                if rig is not None:
                    if args.nod: rig.nod()                # TILT dip (off by default: heavy head droops)
                    mstate, last_active = "WATCH", t      # stop scanning: stay with the story
                elif not args.no_sound:
                    beep()
                    time.sleep(0.4)                       # (no-rig) tiny gap so Found! ≠ Look!
                # ---- LOOK!: come-look BID (beats + screen; head bob only if --nod) ----
                look_bid(rig, cs3, nod=args.nod)
                awaiting_ack = True; ack_deadline = t + ACK_SEC   # now wait for you to look, then resume
                last_event = t                                    # something happened (feeds idle re-plan)
                print(f"[STORY ] {label} — burst opened")
                banner = (t + 3, f"watching: {label}", C_CYAN)
                bursts.append({"label": label, "idx": executor.entries.index(e),
                               "shots": [fr.copy()], "traces": [shot_trace(truth, viz)],
                               "last_truth": dict(truth),
                               "last_gray": cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY),
                               "next": t + args.burst_interval,
                               "ends_at": t + args.linger,      # capture the follow-through, not just the trigger
                               "note": note, "worth": worth,
                               "truth": {k: int(v) for k, v in truth.items()}})

        # ---- P2: a focus OBJECT appears (find-and-share) — report on ONSET, then habituate ----
        present = {str(d.label).lower() for d in viz.get("dets", [])} & (relevance.get("focus") or set())
        present.discard("person")                       # people arrivals go through relations, not P2
        if executor is not None and not rec_state["on"] and not awaiting_ack:
            for lab in present:
                gap = t - focus_seen.get(lab, -1e9)     # long gap = it just (re)appeared
                cooling = t - focus_found_at.get(lab, -1e9) < cur_cd     # per-object habituation
                if gap > OBJ_REAPPEAR and not cooling:
                    focus_found_at[lab] = t
                    last_event = t                          # something happened (feeds idle re-plan)
                    if cs3: cs3.found()
                    if rig is not None and args.nod: rig.nod()
                    banner = (t + 4, f"found: {lab}", C_GREEN)
                    threading.Thread(target=_report_found, args=(lab, fr.copy(), dict(truth)),
                                     daemon=True).start()
        for lab in present:
            focus_seen[lab] = t                         # keep onset tracking current (always)

        # ---- advance open bursts; on completion publish the comic strip, then 'bored' ----
        for b in list(bursts):
            st_ = statuses[b["idx"]]
            # EVENT-DRIVEN panel: add one only when the scene CHANGED (truth-vector differs, or the
            # image moved enough) AND the min interval has passed — long stories get more panels,
            # short ones stay short, no near-duplicate frames.
            if st_.satisfied:
                b["ends_at"] = t + args.linger          # keep the burst open a bit past the relation
            # keep capturing panels while the burst is open — INCLUDING the linger window after the
            # relation drops — so the follow-through ("...then moved to the group") is on record too,
            # not just the triggering instant.
            if t >= b["next"] and len(b["shots"]) < args.burst_n:
                g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
                changed = dict(truth) != b["last_truth"]
                if not changed and b["last_gray"].shape == g.shape:
                    changed = float(cv2.absdiff(g, b["last_gray"]).mean()) > args.scene_diff
                if changed:
                    b["shots"].append(fr.copy()); b["traces"].append(shot_trace(truth, viz))
                    b["last_truth"] = dict(truth); b["last_gray"] = g
                b["next"] = t + args.burst_interval
            if len(b["shots"]) >= args.burst_n or t > b.get("ends_at", t):
                bursts.remove(b)
                banner = (t + 5, f"saving: {b['label']}", C_GREEN)
                # NARRATE (VLM) + publish OFF the main thread → live never freezes on the network
                threading.Thread(target=_finalize, args=(b,), daemon=True).start()

        # ---- motion policy (rig mode) ----
        if rig is not None and args.sweep_capture:
            # SWEEP mode: stay LOCKED on the richest angle the sweep chose — no blind pose scanning.
            # Change what it watches only by RE-PLANNING (VLM re-sweep, same context), during a lull:
            #   periodic  — every --replan-interval seconds; OR
            #   idle      — something happened after the last plan, then --idle-replan s of quiet
            #               (a dead room that never fired anything falls back to the periodic timer).
            quiet = (not bursts) and (not awaiting_ack) and (not rec_state["on"])
            due_periodic = (t - last_plan) > args.replan_interval
            due_idle = (last_event > last_plan) and (t - last_event) > args.idle_replan
            if executor is not None and quiet and (due_periodic or due_idle):
                why = "periodic" if due_periodic else "idle"
                print(f"[replan] {why} refresh — re-sweeping (same context)")
                _res = delegate(ctx, cv2.imencode(".jpg", fr)[1].tobytes())
                if _res:
                    spec, executor = _res; push_plan()
                    banner = (t + 4, f"re-swept ({why})", C_CYAN)
                last_plan = t; last_event = t
        elif rig is not None:
            # legacy (no --sweep-capture): WATCH while active, else resume fixed-pose SCAN
            if bursts or any(s.satisfied for s in statuses):
                last_active, mstate = t, "WATCH"
            if mstate == "WATCH" and not bursts and t - last_active > args.watch_end:
                mstate, pose_until = "SCAN", 0.0
                print("[rig] bored & quiet -> resume scan")
            if mstate == "SCAN" and t >= pose_until and not rec_state["on"]:
                pose_i += 1                                # (hold still while delegating/recording)
                pn, tl = scan[pose_i % len(scan)]
                rig_goto_async(rig, pn, tl)               # NON-blocking → live stays smooth
                pose_until = time.time() + args.look_secs

        # ---- overlay ----
        for d in viz["dets"]:                                       # object slots — yellow-green
            x1, y1, x2, y2 = map(int, d.box)
            cv2.rectangle(fr, (x1, y1), (x2, y2), C_YELLOW, 3, cv2.LINE_AA)
            draw_text(fr, d.label, (x1, y1 - 6), C_YELLOW, 0.6, 2)
        from gaze import draw_pose_skeleton              # mediapipe BlazePose skeleton (THICK)
        for p in viz["people"]:
            if getattr(p, "raw", None):
                draw_pose_skeleton(fr, p.raw, C_SKEL, thick=9)
        # (no separate person box / p# label — the object detector's box already frames the person)
        for a in viz.get("arms", []):                                # pointing — yellow-green
            draw_arrow(fr, a.origin, a.point_at(0.4 * math.hypot(W, H)), C_YELLOW, 12)
        for r0 in viz["rays"]:                                       # gaze rays — blue (logic)
            draw_arrow(fr, r0.origin, r0.point_at(0.5 * math.hypot(W, H)), C_CYAN, 12)
        for c in viz.get("joint", []):                              # joint-attention moment — red
            draw_circle(fr, c["point"], 26, C_MAGENTA, 10)
        # bottom vocabulary ribbon (replaces the old truth-vector line + the top-left panel,
        # which duplicated the web UI's THE PLAN); detection-only highlight + conjunction brackets
        draw_relation_ribbon(fr, W, H, truth, cur_entries)
        if rig is not None:                            # live-only motion state (not on the right panel)
            draw_text(fr, f"{mstate} pan {rig.pan:.0f} tilt {rig.tilt:.0f}", (16, 30), C_GREEN, 0.6, 2)
        if banner and t < banner[0]:
            draw_text(fr, str(banner[1])[:60], (16, 36), banner[2], 0.85, 2)
        if UI is not None:
            with UI.LOCK:
                UI.STATE["status"] = UI.build_status(statuses, cur_entries, truth)
            UI.STATE["jpg"] = cv2.imencode(".jpg", fr)[1].tobytes()
        cv2.imshow("attention system (VLM-first)", fr)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
    cv2.destroyAllWindows(); engine.close()
    if rig is not None:
        rig.close()
    if cs3:
        cs3.step("STOP"); cs3.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[exit] interrupted — force quit")
        os._exit(0)        # guarantee termination even if an audio/serial stream is open
