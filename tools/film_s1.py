#!/usr/bin/env python3
"""
S1 legibility study -- master-take filming driver.

One continuous camera recording per distance; this script drives the robot
through all 8 survey states with generous stillness around each, and beeps
from the laptop so the take can be cut automatically afterwards.

SETUP (once): all three distance marks sit on ONE line -- the authored seat
bearing (pan -30). `python3 tools/film_s1.py --align` turns the head to that
bearing so you can tape the line along its gaze. Clips then play EXACTLY as
the live loop, no filming-only retargeting anywhere.

SCREEN: blanked for the whole take (`EVT UI black` -- screen text captions the
state, which the study forbids). REFLASH robot/firmware/cores3_sidekick once
before filming day; if any text is visible on the CoreS3 during a take, the
board still runs the old firmware. Antenna LED + sounds are unaffected.

BEFORE FILMING: if any clip was re-authored, re-export it into motion/clips/
FIRST -- this driver plays whatever is in that folder, and a stale CSV films
the old design. Programmatic LED that lives OUTSIDE the clips is replicated
here from the same constants live uses: the planning-hold breath
(ST.PLAN_BREATH) runs in S4's stimulus tail, exactly as noticebot_loop
streams it while plan_pending -- retune the constant and filming follows.

FILMING-DAY COMMAND (once per distance, with the camera already rolling):

    export NOTICEBOT_PORT=/dev/cu.usbmodemXXXXX
    cd notice-sidekick-runkit
    python3 tools/film_s1.py --distance 3m --cores3 auto

Then cut the take:

    python3 tools/crop_s1.py TAKE_3m.mov film_log_3m_*.json -o clips_3m

Marker scheme (audio, laptop speaker -- stimuli are exported without audio):
    triple beep  1568 Hz  = slate, once at run start (sync anchor)
    double beep  1175 Hz  = clip START
    single beep   784 Hz  = clip END
Every beep is also timestamped in the JSON log; the crop script only needs to
find the slate in the take's audio, everything else is offsets.

Per shot: eased goto to the clip's first pose (NOT a designed behavior, never
inside a crop) -> settle 2.5 s -> START beep -> 1.2 s stillness -> play the
clip on its authored clock (LED envelope AND sound effects streamed to the
CoreS3) -> 1.2 s stillness -> END beep. Long LED-only loops (S1, S5B) play a
slice covering >= 2 breath cycles; loops with designed sound (S7b, S8) play
whole passes so the sfx rhythm stays as shipped (S8 cries once per 4 passes --
a 2-pass stimulus carries exactly its first cry, as live).

SOUND IS PART OF THE STIMULUS (decision 2026-08-08): the camera mic records
the CoreS3's sound effects from the audience position -- loudness scales with
distance naturally, same principle as no-zoom. The marker beeps are trimmed
out by crop_s1.py's default trim window, so keep the laptop NEAR THE CAMERA
(mic must hear the beeps) at moderate volume.

Zero human involvement: do not walk in, do not touch the robot. If a pass has
a glitch, rerun with --only.
"""
import argparse
import json
import math
import os
import struct
import subprocess
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from robot.scs import open_bus                                    # noqa: E402
from robot.pose import JOINTS, IDS, centre_units, resolve         # noqa: E402
from robot.clip_player import (ClipPlayer, DEFAULT_CLIPS,         # noqa: E402
                               shift_pan_centre)
from robot import states as ST                                    # noqa: E402

STILL_INNER = 1.2    # stillness inside the crop, each end (spec: >= 1 s)
SETTLE_PRE = 2.5     # settle after the goto, before the START beep
GAP_POST = 0.8       # breather after the END beep
LED_HOLD_TICK = 0.25  # re-send LED during holds (firmware falls back after 500 ms)

# EVERYTHING PLAYS EXACTLY AS THE LIVE LOOP -- no filming-only retargeting.
# The CAMERA moves to the robot instead: all three distance marks sit on the
# AUTHORED SEAT BEARING (pan -30, ST.USER_PAN_AUTHORED). Then, as in live:
# S2/S3 as authored address that bearing (= the lens); S6 is translated to the
# seat by the same shift_pan_centre the live loop uses; S7 points at its
# authored finding (-25), NOT the person -- as designed; and the rest pose sits
# ~30 deg off-camera, which is exactly D5's "start off-axis so turns read".
# Use --align to have the robot face the seat bearing while you tape the line.
SHOTS = [
    ("S1_IDLE",     ["S1_IDLE"]),
    ("S2_LISTEN",   ["S2_LISTEN"]),
    ("S3_ACK",      ["S3_ACK"]),
    ("S4_PLAN",     ["S4_PLAN"]),
    ("S5_TRACK",    ["S5A_SETTLE", "S5B_TRACK"]),
    ("S6_FINETUNE", ["S6_FINETUNE"]),
    ("S7_SUMMON",   ["S7a", "S7b"]),
    ("S8_ERROR",    ["S8_ERROR"]),
]
# Long LED-only loops (no sfx) are TRUNCATED to >= 2 breath cycles; loops with
# designed sound play WHOLE passes so the sfx schedule stays as shipped.
SLICE = {"S1_IDLE": 12.0, "S5B_TRACK": 11.0}   # cycles: 5.0 s / 3.6 s
PASSES = {"S7b": 2, "S8_ERROR": 2}             # 2x6.53 s / 2x3.97 s
# Sequences film as designed: S7 = found -> beckon; S5 = SETTLE -> hold,
# because live NEVER enters S5B cold -- both entry paths (S4 armed, S6
# corrected) arrive through the settle crane, and the crane is tracking's
# motion signature (without it the stimulus is a still body + breath, one
# rhythm away from idle). Each sequence logs a sub_<state> marker, so the
# crop can also produce the bare S7b / S5B variant if ever wanted.

# D4 taxonomy: each state is filmed ONLY at its own distance. --distance picks
# the right subset automatically; override with --only (e.g. --only all).
DISTANCE_SHOTS = {
    "1m": ["S2_LISTEN", "S3_ACK", "S6_FINETUNE"],   # interaction states
    "3m": ["S1_IDLE", "S4_PLAN", "S5_TRACK", "S8_ERROR"],  # autonomous
    "6m": ["S7_SUMMON"],                             # summons envelope boundary
}

TONES = {"slate": (1568.0, 3), "start": (1175.0, 2), "end": (784.0, 1)}
BEEP_DIR = "/tmp/film_s1_beeps"


def make_beeps():
    os.makedirs(BEEP_DIR, exist_ok=True)
    rate, tone_s, gap_s = 44100, 0.12, 0.10
    for name, (freq, n) in TONES.items():
        path = os.path.join(BEEP_DIR, f"{name}.wav")
        if os.path.exists(path):
            continue
        frames = b""
        for k in range(n):
            for i in range(int(rate * tone_s)):
                # 10 ms fade in/out so the tone has no click transient
                env = min(1.0, i / (rate * 0.01),
                          (int(rate * tone_s) - i) / (rate * 0.01))
                frames += struct.pack(
                    "<h", int(28000 * env * math.sin(2 * math.pi * freq * i / rate)))
            if k < n - 1:
                frames += b"\x00\x00" * int(rate * gap_s)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(frames)


def beep(name):
    t = time.perf_counter()
    subprocess.Popen(["afplay", os.path.join(BEEP_DIR, f"{name}.wav")],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return t


def slice_frames(frames, secs):
    if secs is None:
        return frames
    return [f for f in frames if f["t"] <= secs + 1e-9]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--distance", required=True,
                    help="tag for filenames/log: 1m | 3m | 6m (or anything)")
    ap.add_argument("--cores3", nargs="?", const="auto",
                    help="stream the authored LED envelope (auto-detect port). "
                         "S1 and S5B carry their state in the LED ONLY -- "
                         "filming without it is filming a dead robot.")
    ap.add_argument("--user-pan", type=float, default=None,
                    help="the seat bearing in deg (default: the authored seat, "
                         f"{ST.USER_PAN_AUTHORED:+.0f}). The camera stands on "
                         "this bearing; only S6 is translated to it -- exactly "
                         "what the live loop does.")
    ap.add_argument("--align", action="store_true",
                    help="setup helper: turn the head to the seat bearing and "
                         "hold, so you can tape the camera line, then exit.")
    ap.add_argument("--repeat", type=int, default=2,
                    help="passes over the whole shot list (default 2; keep the "
                         "technically cleanest pass per state)")
    ap.add_argument("--only", default=None,
                    help="comma-separated shot ids (retakes), or 'all'. Default: "
                         "the subset D4 assigns to --distance (1m/3m/6m); any "
                         "other distance tag runs all shots.")
    ap.add_argument("--clips", default=DEFAULT_CLIPS)
    a = ap.parse_args()

    if a.only == "all":
        wanted = None
    elif a.only:
        wanted = a.only.split(",")
    else:
        wanted = DISTANCE_SHOTS.get(a.distance)  # None for unknown tags = all
        if wanted:
            print(f"--distance {a.distance} -> shots {wanted} "
                  f"(D4 taxonomy; use --only all to override)")
    shots = SHOTS if wanted is None else [s for s in SHOTS if s[0] in wanted]
    if not shots:
        sys.exit(f"no shots matched; shot ids: {[s[0] for s in SHOTS]}")

    seat = a.user_pan if a.user_pan is not None else ST.USER_PAN_AUTHORED

    make_beeps()
    try:
        bus, port = open_bus()
    except IOError as e:
        sys.exit(str(e))
    missing = [n for n, sid in IDS.items() if not bus.ping(sid)[1]]
    if missing:
        sys.exit(f"no reply from {missing} on {port}")

    if a.align:
        units = centre_units()
        units["pan"] = resolve("pan", max(0, min(1023,
                               round((seat + 150.0) / 300.0 * 1023))))[0]
        for sid in IDS.values():
            bus.torque(sid, True)
        for j in JOINTS:
            bus.write_pos(IDS[j], units[j], time_ms=800, speed=0)
        print(f"head is facing the seat bearing ({seat:+.0f} deg). Tape the "
              "camera line along its gaze; put the 1/3/6 m marks ON this line.")
        input("Enter to recentre and exit... ")
        for j in JOINTS:
            bus.write_pos(IDS[j], centre_units()[j], time_ms=800, speed=0)
        time.sleep(1.0)
        bus.close()
        return

    link, on_led, on_sfx = None, None, None
    if a.cores3:
        from session.cores3_link import CoreS3Link, find_cores3
        c3 = a.cores3 if a.cores3 != "auto" else find_cores3(exclude=(port,))
        if not c3:
            sys.exit("no CoreS3 found -- the LED and the sound are part of the "
                     "stimulus; pass --cores3 /dev/cu.usbmodemXXXX")
        link = CoreS3Link(c3, on_input=lambda s: None)
        on_led = link.led
        on_sfx = link.sfx
        # SCREEN BLACK FOR THE WHOLE TAKE (Cassie, 2026-08-08): the screen's
        # words ("recording", "noticed", HOLD/STOP/OK...) caption the state --
        # a printed answer key on the stimulus. Antenna hue/level and sfx are
        # separate channels and stay fully authored. Requires the firmware
        # with the `black` screen -- if you can read ANY text on the CoreS3
        # after this line, REFLASH robot/firmware/cores3_sidekick first.
        link.ui("black")
    else:
        print("!! NO --cores3: S1_IDLE and S5B_TRACK will show a DEAD robot "
              "(their only signal is the LED) and NO designed sounds will "
              "play. Only proceed for a motion-only test take.")

    p = ClipPlayer(bus, a.clips, verbose=False, on_led=on_led, on_sfx=on_sfx)

    def hold_still(secs, led_val):
        end = time.perf_counter() + secs
        while time.perf_counter() < end:
            if on_led is not None and led_val is not None:
                on_led(led_val)
            time.sleep(min(LED_HOLD_TICK, max(0.01, end - time.perf_counter())))

    def hold_breath(secs, pb):
        """The planning-hold breath, EXACTLY as noticebot_loop streams it while
        plan_pending: raised cosine between pb['low'] and pb['high'] at
        pb['period_s'], resent at pb['hz']. In live, this takes over the moment
        S4's clip ends -- so the S4 stimulus tail must breathe, not freeze."""
        end = time.perf_counter() + secs
        while time.perf_counter() < end:
            if on_led is not None:
                phase = (time.perf_counter() % pb["period_s"]) / pb["period_s"]
                lvl = pb["low"] + (pb["high"] - pb["low"]) * \
                    (0.5 - 0.5 * math.cos(2 * math.pi * phase))
                on_led(int(lvl))
            time.sleep(1.0 / pb["hz"])

    events = []

    def log(shot, pas, event):
        events.append(dict(shot=shot, pass_=pas, event=event,
                           t_mono=time.perf_counter(), t_wall=time.time()))

    est = sum(sum((SLICE.get(st) or
                   p.clips[ST.STATES[st]["clip"]][-1]["t"] * PASSES.get(st, 1))
                  for st in states) + SETTLE_PRE + 2 * STILL_INNER + GAP_POST + 1.5
              for _, states in shots) * a.repeat
    print(f"\nTake plan: {len(shots)} shots x {a.repeat} pass(es), "
          f"~{est/60:.1f} min. Distance tag: {a.distance}")
    print(f"Camera on the SEAT BEARING line ({seat:+.0f} deg -- --align shows "
          "it), at this distance's tape mark. Exposure/WB LOCKED, focus locked, "
          "framing per reference photo, room empty, phone silenced, laptop "
          "NEAR THE CAMERA, volume moderate (beeps = sync track).")
    input("\n>> START the camera recording, then press Enter for the slate... ")

    t0 = beep("slate")
    events.append(dict(shot="_slate", pass_=0, event="slate",
                       t_mono=t0, t_wall=time.time()))
    time.sleep(1.5)

    try:
        for pas in range(1, a.repeat + 1):
            for shot_id, states in shots:
                spec0 = ST.STATES[states[0]]
                if link:
                    link.hue(spec0["hue"])   # antenna colour IS stimulus; screen stays black
                print(f"[{a.distance}] pass {pas}  {shot_id}")
                # prepare frames per state -- EXACTLY the live loop's routing:
                # USER_FACING (S6) translated to the seat via the same
                # shift_pan_centre live uses; everything else as authored.
                # (Live's S7 shift-to-current-pan is the identity here, since
                # the pre-roll goto puts the head at S7's own first pose.)
                prepared = []
                for stname in states:
                    spec = ST.STATES[stname]
                    fr = p.clips[spec["clip"]]
                    if stname in ST.USER_FACING:
                        fr = shift_pan_centre(fr, seat, verbose=True)
                    fr = slice_frames(fr, SLICE.get(stname))
                    prepared.append((stname, fr))
                first_pose = {j: prepared[0][1][0][j] for j in JOINTS}
                p._goto(first_pose, label=f"pre {shot_id}")
                hold_still(SETTLE_PRE, prepared[0][1][0]["led"])
                beep("start")
                log(shot_id, pas, "start")
                hold_still(STILL_INNER, prepared[0][1][0]["led"])
                last_led = None
                for k, (stname, fr) in enumerate(prepared):
                    spec = ST.STATES[stname]
                    if k > 0:
                        log(shot_id, pas, f"sub_{stname}")
                        nxt_pose = {j: fr[0][j] for j in JOINTS}
                        p._goto(nxt_pose, label=f"   -> {stname}")
                    if link:
                        link.hue(spec["hue"])
                    # Sound, as shipped: shutter rides the clip's own LED flash
                    # edge; the entry sound fires at its authored fraction of
                    # the clip, on the FIRST pass only (S8's every-4th re-arm
                    # never lands inside a 2-pass stimulus -- one cry, as live).
                    p._flash_sfx = spec.get("sfx_flash")
                    p._in_flash = False
                    sfx, at_frac = spec.get("sfx"), spec.get("sfx_at", 0.0)
                    for n in range(PASSES.get(stname, 1)):
                        if sfx and on_sfx and n == 0:
                            if at_frac <= 0.0:
                                on_sfx(sfx)
                                p._pending_sfx = None
                            else:
                                p._pending_sfx = sfx
                                p._pending_at = at_frac * fr[-1]["t"]
                        else:
                            p._pending_sfx = None
                        p._play_once(fr)
                    last_led = fr[-1]["led"]
                # S4's tail is the planning hold: in live, ST.PLAN_BREATH takes
                # the antenna the moment the clip ends (plan_pending). Everything
                # else holds its clip's final LED value.
                if states[-1] == "S4_PLAN":
                    hold_breath(STILL_INNER, ST.PLAN_BREATH)
                    beep("end")
                    log(shot_id, pas, "end")
                    hold_breath(GAP_POST, ST.PLAN_BREATH)
                else:
                    hold_still(STILL_INNER, last_led)
                    beep("end")
                    log(shot_id, pas, "end")
                    hold_still(GAP_POST, last_led)
    except KeyboardInterrupt:
        print("\ninterrupted -- log written for what completed")
    finally:
        p._goto(centre_units(), label="home")
        for sid in IDS.values():
            try:
                bus.torque(sid, False)
            except Exception:
                pass
        if link:
            link.rest()
            time.sleep(0.3)
            link.close()
        bus.close()

    out = f"film_log_{a.distance}_{time.strftime('%m%d_%H%M%S')}.json"
    with open(out, "w") as fh:
        json.dump(dict(distance=a.distance, user_pan=a.user_pan,
                       repeat=a.repeat, clips_dir=a.clips,
                       still_inner=STILL_INNER,
                       tones={k: v for k, v in TONES.items()},
                       led_cycles={"S1_IDLE": 5.0, "S5B_TRACK": 3.6,
                                   "S7b": 6.53, "S8_ERROR": 3.97},
                       events=events), fh, indent=1)
    print(f"\nlog -> {out}")
    print("STOP the camera recording. Next:")
    print(f"  python3 tools/crop_s1.py <TAKE.mov> {out} -o clips_{a.distance}")


if __name__ == "__main__":
    main()
