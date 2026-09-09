#!/usr/bin/env python3
"""attract.py — the stand runs itself while nobody is at it.

FOR THE BREAK. A stand with a motionless robot on it reads as broken, and a
robot that only moves when somebody works the tablet is invisible from across
the hall. This walks the whole eight-state cycle on a loop -- motion, screen,
light and sound together -- so the table keeps saying what the project is while
the researcher is away from it.

IT IS NOT THE SYSTEM. No camera, no VLM, no watch-spec, nothing recorded: it
plays the authored clips in order, on a timer. That distinction matters at a
research stand, so the CoreS3 says DEMO LOOP on entry and the terminal says it
too. If somebody asks whether it is really watching right now, the answer is no
-- start the real loop and show them.

    python3 -m robot.tools.attract                       # auto-detect the board
    python3 -m robot.tools.attract --cores3 /dev/cu.usbmodem11201
    python3 -m robot.tools.attract --once                # one pass, then stop
    python3 -m robot.tools.attract --gap 1.5             # slower between beats

Ctrl-C leaves the joints torqued off, so the head does not fight anyone who
picks the robot up to look at it.

RUN IT INSTEAD OF THE LOOP, never beside it. Both open the same two serial
ports, and the second one to start gets a device-busy error at best and a
half-written frame at worst.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from robot import states as ST
from robot.clip_player import ClipPlayer
from robot.pose import IDS
from robot.scs import open_bus

# The order is the interaction, not the state table's declaration order. It is
# the sequence a visitor would have produced: woken, told, acknowledged, looked
# around, settled, watching, corrected, watching again, called, back to rest.
# Error is included once at the end because it is part of the grammar and a
# stand should show the whole vocabulary, but it is last so the loop does not
# spend its most visible seconds looking broken.
#
# Seconds are HOLDS AFTER the clip's own length, not the clip length. Loop
# clips (S1, S5B, S8) have no end of their own, so their number is the whole
# time they get.
def _clip_seconds(clips_dir):
    """How long each clip runs, read from the CSVs the player will feed.

    Measured rather than declared: the state table says which clip a state
    plays, not how long it is, and a hand-kept table of durations goes stale the
    first time anything is re-exported from Blender.
    """
    import csv as _csv
    out = {}
    for name, spec in ST.STATES.items():
        if spec.get("loop"):
            continue                      # no end of its own; `hold` is all it gets
        path = os.path.join(clips_dir, f"{spec['clip']}.csv")
        try:
            with open(path) as fh:
                rows = list(_csv.DictReader(fh))
            out[name] = (int(rows[-1]["t_ms"]) - int(rows[0]["t_ms"])) / 1000.0
        except Exception:
            out[name] = 0.0
    return out


CLIP_S = {}


BEATS = [
    ("S1_IDLE",     6.0,  "asleep"),
    ("S2_LISTEN",   1.2,  "woken -- someone spoke to it"),
    ("S3_ACK",      0.8,  "got it"),
    ("S4_PLAN",     1.0,  "looking around the room"),
    ("S5A_SETTLE",  0.8,  "arriving at what it chose"),
    ("S5B_TRACK",   7.0,  "watching"),
    ("S6_FINETUNE", 1.0,  "not that one"),
    ("S5A_SETTLE",  0.6,  "arriving somewhere else"),
    ("S5B_TRACK",   5.0,  "watching again"),
    ("S7a",         0.6,  "it found something"),
    ("S7b",         6.0,  "calling you over"),
    ("S3_ACK",      1.0,  "seen -- acknowledged"),
    ("S8_ERROR",    5.0,  "and this is what stuck looks like"),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cores3", default="auto",
                    help="serial port of the CoreS3, or 'auto', or 'none'")
    ap.add_argument("--port", default=None, help="servo bus port")
    ap.add_argument("--baud", type=int, default=None)
    ap.add_argument("--clips", default="motion/clips")
    ap.add_argument("--once", action="store_true", help="one pass, then stop")
    ap.add_argument("--gap", type=float, default=1.0,
                    help="multiplier on every hold; 1.5 = a slower loop")
    a = ap.parse_args(argv)

    global CLIP_S
    CLIP_S = _clip_seconds(a.clips)

    bus, port = open_bus(a.port, a.baud)

    link = None
    if a.cores3 and a.cores3 != "none":
        from session.cores3_link import CoreS3Link, find_cores3
        target = a.cores3
        if target == "auto":
            target = find_cores3(exclude=(port,))
            if not target:
                print("[attract] no CoreS3 found -- motion only")
        elif port and os.path.realpath(target) == os.path.realpath(port):
            sys.exit(f"--cores3 {target} is the SERVO bus. Both are named "
                     f"usbmodem-<id>; leave it on 'auto' to detect by PING.")
        if target:
            link = CoreS3Link(target, on_input=lambda l: None)

    def led(level):
        link and link.led(level)

    def sfx(name):
        link and link.sfx(name)

    player = ClipPlayer(bus, a.clips, on_led=led, on_sfx=sfx)
    # WITHOUT THIS NOTHING MOVES. `request()` only sets what the player SHOULD be
    # doing; the clip is fed to the bus by a worker thread that start() spawns.
    # Leaving it out gave a stand where the M5 screen changed on cue and the head
    # never moved and no sound played, which looks exactly like a servo-power or
    # a wiring fault and is neither.
    player.start()

    if link:
        # SAY WHAT THIS IS. A stand that performs the whole cycle with nobody
        # touching it invites the reading that it is responding to the room, and
        # at a research stand that reading has to be corrected before it is
        # made, not after somebody has already told a colleague about it.
        link.event("NAME", "DEMO LOOP")
        link.event("UI", "idle")

    print("[attract] DEMO LOOP -- authored clips on a timer.")
    print("[attract] NOT the live system: no camera, no VLM, nothing recorded.")
    print("[attract] Ctrl-C to stop; the joints are released on the way out.\n")

    passes = 0
    try:
        while True:
            for state, hold, why in BEATS:
                spec = ST.STATES[state]
                print(f"  {state:<12} {why}")
                if link:
                    link.event("UI", spec.get("screen") or "idle")
                    hue = spec.get("hue")
                    if hue:
                        link.event("HUE", str(hue).upper())
                player.request(state)
                # WAIT FOR THE CLIP, THEN HOLD. `hold` is time on top of the
                # clip's own length, so a beat is never cut off mid-gesture --
                # sleeping for `hold` alone would interrupt S2's 1.7 s turn
                # after 1.2 s and the motion would read as a twitch.
                #
                # Loop clips (S1, S5B, S8) never finish, so they get `hold` flat.
                spec_len = CLIP_S.get(state, 0.0)
                time.sleep(max(0.05, (spec_len + hold) * a.gap))
            passes += 1
            if a.once:
                break
            print(f"  -- pass {passes} --")
    except KeyboardInterrupt:
        print("\n[attract] stopping")
    finally:
        # RELEASE THE JOINTS. Somebody will pick the robot up to look
        # underneath it, and a head fighting their hand is both alarming and a
        # good way to strip a horn.
        try:
            player.stop()          # relaxes by default
        except Exception:
            pass
        for sid in IDS.values():
            try:
                bus.torque(sid, False)
            except Exception:
                pass
        if link:
            try:
                link.event("UI", "idle")
                link.event("REST", "")
            except Exception:
                pass
        print("[attract] joints released")
    return 0


if __name__ == "__main__":
    sys.exit(main())
