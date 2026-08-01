#!/usr/bin/env python3
"""
Play a Blender-exported motion clip on the real 3-DOF NoticeBot prototype.

Hardware: 3x Feetech SCS0009 (pan=1, tilt=2, nod=3) on one TTL bus behind an
FE-URT2-C001. Servo bus needs its own 5-6V supply on the blue screw terminal --
USB does not power it.

  python3 -m pip install feetech-servo-sdk pyserial
  python3 check_bus.py                      # find port + baud first

Usage:
  python3 play_on_hardware.py --center
  python3 play_on_hardware.py ../../motion/clips/S1_IDLE.csv
  python3 play_on_hardware.py ../../motion/clips/S1_IDLE.csv --rate 0.5    # half speed, DIAGNOSIS ONLY
  python3 play_on_hardware.py ../../motion/clips/S1_IDLE.csv --dry-run     # no hardware, just check the CSV

CSV columns: t_ms, pan_deg, tilt_deg, [nod_deg,] pan_unit, tilt_unit, [nod_unit]
The current export/*.csv are 2-DOF (no nod) -- nod is held at centre and you
get a warning. Re-export from Blender with the nod channel to fix that.

Tuning rule: if motion looks too fast or jittery, RE-AUTHOR IN BLENDER and
re-export. Do not hand-edit the CSV -- the CSV is a build artifact, and hand
edits get silently destroyed on the next export. --rate exists only to tell
"too fast" apart from "bad data"; it is not an authoring tool.
"""
import argparse, csv, os, sys, time

# Run me directly: the repo root is two levels up from robot/tools/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from robot.scs import Bus, open_bus

# Port comes from the environment, same as check_bus.py and jog.py -- one place
# to set it, and no stale hardcoded device to drift out of date:
#   export NOTICEBOT_PORT=/dev/cu.usbmodemXXXX
PORT = os.environ.get("NOTICEBOT_PORT")
BAUD = int(os.environ.get("NOTICEBOT_BAUD", 1_000_000))

from robot import IDS  # single source of truth: robot/__init__.py
CENTER = 512

# Fallback values, used only if calibration.py is missing. Deliberately narrow --
# if you ever see these in play, calibration has not been done yet.
LIMITS = {"pan": (412, 612), "tilt": (412, 612), "nod": (412, 612)}
INVERT = {"pan": False, "tilt": False, "nod": False}   # flip if a joint runs backwards
OFFSET = {"pan": 0, "tilt": 0, "nod": 0}               # mechanical-zero trim, units

_UNCALIBRATED = set(IDS) if False else None
try:
    # written by jog.py; hand-editable
    from robot import calibration as _cal
    LIMITS, OFFSET, INVERT = _cal.LIMITS, _cal.OFFSET, _cal.INVERT   # noqa: F811
    _UNCALIBRATED = set(getattr(_cal, "UNCALIBRATED", set()))
except ImportError:
    _UNCALIBRATED = None

CENTER_MOVE_MS = 800      # slow, deliberate move when centring
PREROLL_DPS = 120.0       # speed used to reach a clip's opening pose
UNITS_PER_DEG = 1023 / 300.0


def resolve(name, unit):
    """CSV unit -> actual servo unit, applying invert, then trim, then clamp."""
    u = int(round(float(unit)))
    if INVERT[name]:
        u = 1023 - u
    u += OFFSET[name]
    lo, hi = LIMITS[name]
    return max(lo, min(hi, u)), (u < lo or u > hi)


def load_clip(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit(f"{path}: empty")
    has_nod = "nod_unit" in rows[0]
    frames = []
    for r in rows:
        f = {"t": float(r["t_ms"]) / 1000.0,
             "pan": float(r["pan_unit"]),
             "tilt": float(r["tilt_unit"]),
             "nod": float(r["nod_unit"]) if has_nod else CENTER}
        frames.append(f)
    return frames, has_nod


def report_clip(path, frames, has_nod):
    dur = frames[-1]["t"]
    fps = (len(frames) - 1) / dur if dur > 0 else 0
    print(f"{os.path.basename(path)}: {len(frames)} frames, {dur:.2f}s, ~{fps:.0f} fps")
    if not has_nod:
        print("  ! no nod_unit column -- 2-DOF clip, nod held at centre.")
        print("    Re-export from Blender with the nod channel for the real thing.")
    # peak per-axis speed, to catch clips that will slam on hardware
    UNITS_PER_DEG = 1023 / 300.0
    for name in IDS:
        peak = 0.0
        for a, b in zip(frames, frames[1:]):
            dt = b["t"] - a["t"]
            if dt > 0:
                peak = max(peak, abs(b[name] - a[name]) / dt)
        print(f"  {name:<4} peak {peak / UNITS_PER_DEG:6.0f} deg/s", end="")
        if peak / UNITS_PER_DEG > 200:
            print("   <-- over the 200 deg/s authoring ceiling", end="")
        print()
    # anything the clamp would eat
    hit = {n for f in frames for n in IDS if resolve(n, f[n])[1]}
    if hit:
        print(f"  ! clamped by LIMITS: {sorted(hit)} -- the clip asks for poses your")
        print("    limits forbid. Widen LIMITS or re-author, don't just let it clip.")


def go_center(bus):
    print(f"centring to {CENTER} over {CENTER_MOVE_MS}ms ...")
    # Torque first. jog.py and --relax both leave it off, so without this the
    # servos accept and acknowledge every command and simply never move --
    # which looks identical to a dead script.
    for sid in IDS.values():
        bus.torque(sid, True)
    time.sleep(0.05)

    targets = {}
    for name, sid in IDS.items():
        u, _ = resolve(name, CENTER)
        targets[name] = u
        bus.write_pos(sid, u, time_ms=CENTER_MOVE_MS, speed=0)
    time.sleep(CENTER_MOVE_MS / 1000.0 + 0.3)
    bus.flush_input()

    stuck = []
    for name, sid in IDS.items():
        p = bus.read_pos(sid)
        print(f"  {name:<4} id={sid}  pos={p}/{targets[name]}  {bus.read_voltage(sid)}V")
        if p is None or abs(p - targets[name]) > 8:
            stuck.append(name)
    if stuck:
        sys.exit(f"{stuck} did not reach centre -- refusing to play a clip.\n"
                 f"Playing into a joint that cannot move means the rest of the "
                 f"clip runs open-loop against a jam.")


def play(bus, frames, rate=1.0, loop=False):
    """Stream waypoints on the clip's own clock.

    Timing is the whole point: the expressive character lives in the ease curves,
    so we hold an absolute schedule rather than sleeping a fixed step. If a frame
    is already late we SKIP it instead of sending late -- sending late would
    accumulate lag and stretch the clip.
    """
    late = 0.0
    skipped = 0
    t0 = time.perf_counter()
    while True:
        for f in frames:
            target = t0 + f["t"] / rate
            now = time.perf_counter()
            if now > target + 0.020:      # >20ms behind: drop this waypoint
                skipped += 1
                continue
            while now < target:
                if target - now > 0.002:
                    time.sleep(target - now - 0.001)
                now = time.perf_counter()
            late = max(late, now - target)
            for name, sid in IDS.items():
                u, _ = resolve(name, f[name])
                bus.write_pos_fast(sid, u, time_ms=0, speed=0)
        if not loop:
            break
        t0 = time.perf_counter()
    bus.flush_input()   # writeTxOnly leaves status packets in the RX buffer
    print(f"done. max lag {late*1000:.1f}ms, {skipped} waypoints dropped")
    if late > 0.030 or skipped:
        print("  ! timing slipped. Lower the export fps in Blender rather than")
        print("    letting the bus decide -- a clip that can't be streamed on time")
        print("    is not the clip you designed.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip", nargs="?")
    ap.add_argument("--port", default=PORT)
    ap.add_argument("--baud", type=int, default=BAUD)
    ap.add_argument("--center", action="store_true", help="centre all joints and hold")
    ap.add_argument("--loop", action="store_true", help="repeat (S1/S5/S8 are loop clips)")
    ap.add_argument("--rate", type=float, default=1.0,
                    help="playback rate for DIAGNOSIS only (0.5 = half speed). "
                         "Fix real timing in Blender, not here.")
    ap.add_argument("--dry-run", action="store_true", help="inspect the CSV, touch no hardware")
    ap.add_argument("--relax", action="store_true", help="torque off and exit")
    a = ap.parse_args()

    if a.dry_run:
        if not a.clip:
            sys.exit("--dry-run needs a clip")
        frames, has_nod = load_clip(a.clip)
        report_clip(a.clip, frames, has_nod)
        return

    if not (a.clip or a.center or a.relax):
        sys.exit("give a clip, or --center / --relax / --dry-run")

    if _UNCALIBRATED is None:
        sys.exit("no calibration.py found -- run jog.py and press p first.\n"
                 "Playing a clip on guessed limits can drive a joint into a stop.")
    if _UNCALIBRATED and not a.relax:
        sys.exit(f"these joints are not calibrated yet: {sorted(_UNCALIBRATED)}\n"
                 f"An uncalibrated axis has no real guard rail. Measure it in "
                 f"jog.py (centre with c, ends with [ and ]), press p, then retry.")

    frames = has_nod = None
    if a.clip:
        frames, has_nod = load_clip(a.clip)
        report_clip(a.clip, frames, has_nod)

    try:
        bus, a.port = open_bus(a.port, a.baud)   # probes; the name is not stable
    except IOError as e:
        sys.exit(str(e))
    try:
        missing = [n for n, sid in IDS.items() if not bus.ping(sid)[1]]
        if missing:
            # opening the port succeeding proves nothing -- any serial device
            # opens fine, it just never answers
            sys.exit(f"no reply from {missing} on {a.port} @ {a.baud}.\n"
                     f"If check_bus.py sees all three, this is the WRONG PORT: "
                     f"you have more than one usbmodem device.")

        if a.relax:
            for sid in IDS.values():
                bus.torque(sid, False)
            print("torque off.")
            return

        go_center(bus)
        if a.center:
            return

        time.sleep(0.3)
        # Ease into the clip's first pose before starting the clock, so frame 1
        # isn't a jump from centre. The duration SCALES WITH DISTANCE: clips do
        # not all start near neutral (S4 opens at -60 deg of pan), and a fixed
        # 400 ms would turn the longest of those into a 150 deg/s lurch before
        # the clip has even begun.
        targets = {n: resolve(n, frames[0][n])[0] for n in IDS}
        far = max(abs(targets[n] - resolve(n, CENTER)[0]) for n in IDS)
        pre_ms = int(max(400, far / UNITS_PER_DEG / PREROLL_DPS * 1000))
        if far > 40:
            print(f"  moving to the opening pose: {far} units "
                  f"({far / UNITS_PER_DEG:.0f} deg) over {pre_ms} ms")
        for name, sid in IDS.items():
            bus.write_pos(sid, targets[name], time_ms=pre_ms, speed=0)
        time.sleep(pre_ms / 1000.0 + 0.3)
        bus.flush_input()

        play(bus, frames, rate=a.rate, loop=a.loop)
    except KeyboardInterrupt:
        print("\ninterrupted -- centring")
        try:
            go_center(bus)
        except Exception:
            pass
    finally:
        bus.close()


if __name__ == "__main__":
    main()
