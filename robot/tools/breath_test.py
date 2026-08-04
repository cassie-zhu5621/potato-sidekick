#!/usr/bin/env python3
"""
Find the smallest tilt breath that actually reads as breathing on THIS build.

Holds S5B_TRACK's pose and breathes the neck at a series of amplitudes, smallest
first, announcing each one. Watch the robot and note where it stops looking like
a twitch and starts looking alive.

Why measure instead of guess: servo resolution is 1 unit = 0.293 deg and the
SCS0009 deadband is 1-2 units, so a "subtle" 0.5 deg breath is 1.7 units and may
not move at all. Round-tripping that question through Blender costs ten minutes
per guess; here it costs one run.

  export NOTICEBOT_PORT=/dev/cu.usbmodemXXXX
  python3 breath_test.py
  python3 breath_test.py --amps 1 2 3 5 8 12 --period 3.6 --cycles 3

Put the answer into MICRO_BREATH_DEG in blender/generate_s5_track.py
(degrees = units * 0.293).
"""
import argparse, math, os, sys, time

# Run me directly: the repo root is two levels up from robot/tools/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from robot.scs import Bus, open_bus
from robot import calibration as cal

from robot import IDS  # single source of truth: robot/__init__.py
UNITS_PER_DEG = 1023 / 300.0
POSE = {"pan": 25.0, "tilt": -12.0, "nod": 0.0}     # = S5B_TRACK / end of S4
RATE_HZ = 30
ARRIVE_TOL = 8      # units. Normal settling error under load is a few units;
                    # 8 (2.3 deg) is loose enough not to cry wolf, tight enough
                    # to catch a joint that never moved at all.


def to_unit(deg):
    return max(0, min(1023, round((deg + 150.0) / 300.0 * 1023.0)))


def resolve(name, deg):
    u = to_unit(deg)
    if cal.INVERT[name]:
        u = 1023 - u
    u += cal.OFFSET[name]
    lo, hi = cal.LIMITS[name]
    return max(lo, min(hi, u))


def goto(bus, targets, ms, label):
    """Enable torque, move, then CHECK IT ARRIVED.

    Sending a goal position is not the same as moving. jog.py and
    play_on_hardware.py both release torque when they exit, so the very next
    script finds the servos listening but not driving: commands are accepted,
    acknowledged, and silently ignored. Reading the position back afterwards is
    the only thing that tells the two cases apart.
    """
    for sid in IDS.values():
        bus.torque(sid, True)
    time.sleep(0.05)
    for n, sid in IDS.items():
        bus.write_pos(sid, targets[n], time_ms=ms, speed=0)
    time.sleep(ms / 1000.0 + 0.4)
    bus.flush_input()

    bad = []
    parts = []
    for n, sid in IDS.items():
        p = bus.read_pos(sid)
        parts.append(f"{n}={p}/{targets[n]}")
        if p is None or abs(p - targets[n]) > ARRIVE_TOL:
            bad.append(n)
    print(f"{label}: " + "  ".join(parts))
    if bad:
        print(f"  !! {bad} did not reach the target.")
        print("     torque refused, joint jammed, or the load exceeds what")
        print("     SCS0009 can hold at this voltage. Check before continuing.")
    return not bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--amps", type=int, nargs="+", default=[12],
                    help="amplitudes to try, in SERVO UNITS (1 unit = 0.293 deg). "
                         "1-8 was invisible on this short neck; 12 reads.")
    ap.add_argument("--mode", choices=("breath", "shift", "both"), default="both",
                    help="breath = continuous cosine; shift = occasional small "
                         "move and return; both = compare them back to back")
    ap.add_argument("--period", type=float, default=3.6, help="breath period, s")
    ap.add_argument("--cycles", type=int, default=3,
                    help="breath cycles, or number of shifts, per amplitude")
    ap.add_argument("--shift-ms", type=int, default=3600,
                    help="duration of ONE full out-and-back neck breath, ms. "
                         "Short reads as a flinch; this wants to be slow.")
    ap.add_argument("--gap", type=float, default=7.2,
                    help="stillness between breaths, s")
    ap.add_argument("--drive", choices=("stream", "servo"), default="stream",
                    help="stream = we send an eased cosine at 30 Hz; "
                         "servo = hand the arc to the servo's own interpolator")
    ap.add_argument("--split", action="store_true",
                    help="share the breath between tilt and nod so their steps "
                         "interleave -- smoother without moving further or "
                         "faster. Requires --drive stream. nod moves ~half the "
                         "amplitude, so it no longer sits exactly at origin.")
    ap.add_argument("--loop", action="store_true",
                    help="repeat each amplitude until Ctrl-C, so you can watch "
                         "it for a while instead of judging from 3 reps")
    ap.add_argument("--no-home", action="store_true",
                    help="skip the move to the calibrated centres first")
    ap.add_argument("--port", default=os.environ.get("NOTICEBOT_PORT"))
    ap.add_argument("--baud", type=int,
                    default=int(os.environ.get("NOTICEBOT_BAUD", 1_000_000)))
    a = ap.parse_args()
    try:
        bus, a.port = open_bus(a.port, a.baud)   # probes; the name is not stable
    except IOError as e:
        sys.exit(str(e))
    try:
        missing = [n for n, sid in IDS.items() if not bus.ping(sid)[1]]
        if missing:
            sys.exit(f"no reply from {missing} on {a.port}")

        if not a.no_home:
            goto(bus, {n: cal.CENTRE[n] for n in IDS}, 1000, "home (calibrated centres)")

        base = {n: resolve(n, d) for n, d in POSE.items()}
        goto(bus, base, 900, "S5 pose")

        def target(amp, frac=1.0):
            d = round(amp * frac)
            return base["tilt"] + d if cal.INVERT["tilt"] else base["tilt"] - d

        def split_targets(amp, frac):
            """Share the breath between tilt and nod so their integer steps
            interleave. Each joint rounds at a different point, so the head tip
            moves in ~2x as many, smaller increments -- more perceived
            smoothness at the same visible amplitude and the same duration.
            The servo's 0.293 deg resolution is the wall; this is the one way
            around it that does not involve moving further or faster."""
            total = amp * frac
            dt = round(total / 2.0)
            dn = round(total) - dt
            t = base["tilt"] + dt if cal.INVERT["tilt"] else base["tilt"] - dt
            n = base["nod"] + dn if cal.INVERT["nod"] else base["nod"] - dn
            return t, n

        if a.mode in ("breath", "both"):
            print("\n=== CONTINUOUS BREATH ===")
            for amp in a.amps:
                deg = amp * (300.0 / 1023.0)
                print(f"\n>>> {amp} units = {deg:.2f} deg, period {a.period}s "
                      f"-- watch the neck")
                t0 = time.perf_counter()
                while (t := time.perf_counter() - t0) < a.period * a.cycles:
                    phase = (t % a.period) / a.period
                    frac = 0.5 * (1.0 - math.cos(2.0 * math.pi * phase))
                    bus.write_pos_fast(IDS["tilt"], target(amp, frac),
                                       time_ms=0, speed=0)
                    time.sleep(1.0 / RATE_HZ)
                bus.write_pos_fast(IDS["tilt"], base["tilt"], time_ms=0, speed=0)
                bus.flush_input()
                time.sleep(0.8)

        if a.mode in ("shift", "both"):
            print("\n=== INTERMITTENT SHIFT ===")
            print("Eyes detect motion ONSETS far better than slow drift, so a")
            print("still robot that stirs occasionally reads as more alive than")
            print("one oscillating below threshold -- with less total movement.")
            def one_breath(amp):
                dur = a.shift_ms / 1000.0
                if a.drive == "servo":
                    # Hand the arc to the servo's own interpolator. It runs far
                    # faster than we can stream and drives the motor with
                    # continuous torque, so it can look smoother than externally
                    # stepping even though it lands on the same integer units.
                    bus.write_pos(IDS["tilt"], target(amp),
                                  time_ms=int(dur * 1000 / 2), speed=0)
                    time.sleep(dur / 2)
                    bus.write_pos(IDS["tilt"], base["tilt"],
                                  time_ms=int(dur * 1000 / 2), speed=0)
                    time.sleep(dur / 2)
                else:
                    t0 = time.perf_counter()
                    while (t := time.perf_counter() - t0) < dur:
                        frac = 0.5 * (1.0 - math.cos(2.0 * math.pi * (t / dur)))
                        if a.split:
                            ut, un = split_targets(amp, frac)
                            bus.write_pos_fast(IDS["tilt"], ut, time_ms=0, speed=0)
                            bus.write_pos_fast(IDS["nod"], un, time_ms=0, speed=0)
                        else:
                            bus.write_pos_fast(IDS["tilt"], target(amp, frac),
                                               time_ms=0, speed=0)
                        time.sleep(1.0 / RATE_HZ)
                    bus.write_pos_fast(IDS["tilt"], base["tilt"], time_ms=0, speed=0)
                    if a.split:
                        bus.write_pos_fast(IDS["nod"], base["nod"], time_ms=0, speed=0)
                bus.flush_input()

            for amp in a.amps:
                deg = amp * (300.0 / 1023.0)
                peak = math.pi * deg / (a.shift_ms / 1000.0)
                step_ms = (a.shift_ms / 2) / max(1, amp)
                print(f"\n>>> {amp} units = {deg:.2f} deg over {a.shift_ms}ms, "
                      f"drive={a.drive} (peak {peak:.1f} deg/s)")
                print(f"    resolution: one 0.293 deg step every {step_ms:.0f} ms"
                      + ("  <-- visible stepping" if step_ms > 120 else ""))
                if a.loop:
                    print("    looping, Ctrl-C to move on")
                    while True:
                        one_breath(amp)
                        time.sleep(a.gap)
                else:
                    for _ in range(a.cycles):
                        one_breath(amp)
                        time.sleep(a.gap)

        print("\ndone.")
        print("  continuous breath  -> MICRO_BREATH_DEG in generate_s5_track.py")
        print("  intermittent shift -> tell me the amplitude and gap and I'll")
        print("                        rewrite S5 around it instead")
        print("  degrees = units * 0.293")
    except KeyboardInterrupt:
        print("\ninterrupted")
        try:
            bus.write_pos(IDS["tilt"], resolve("tilt", POSE["tilt"]),
                          time_ms=500, speed=0)
            time.sleep(0.6)
        except Exception:
            pass
    finally:
        bus.flush_input()
        bus.close()


if __name__ == "__main__":
    main()
