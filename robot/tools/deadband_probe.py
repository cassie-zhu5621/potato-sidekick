#!/usr/bin/env python3
"""
Measure the smallest motion each joint can actually produce.

The whole micro-motion design depends on one number nobody has measured: how
small a commanded step this servo will honour. Two things stand between a
command and a movement --

  * quantisation: 1 unit = 0.293 deg, so nothing finer exists
  * the dead band: SCSCL ignores an error smaller than a few units, by design,
    so the horn does not buzz while holding a pose

-- and neither is in any datasheet number we have. S1's nod noise is +-2 units
peak, which may be entirely inside the dead band: commanded, acknowledged, and
never moved. That looks exactly like "the clip has no nod channel".

  python3 -m robot.tools.deadband_probe            # all three joints
  python3 -m robot.tools.deadband_probe nod        # one
  python3 -m robot.tools.deadband_probe nod --span 20
  python3 -m robot.tools.deadband_probe nod --sweep   # CONTINUOUS floor

Two floors, and they are not the same number:

  step  (default)  the smallest step FROM REST that moves the joint. This is
                   static friction plus the dead band, and it is the worst case.
  sweep (--sweep)  the smallest amplitude the joint TRACKS while already moving.
                   Kinetic friction is lower than static, so this is usually the
                   smaller number -- and it is the one that governs a breath or a
                   noise layer, which never stop. Use the step floor for anything
                   that starts from a hold: an accent, a peck, a flick.

Reads PRESENT_POSITION back, so it reports what the joint DID, not what it was
told. Moves each joint a few units around its calibrated centre and nothing more.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from robot.scs import open_bus
from robot import IDS, ORDER
from robot import calibration as cal

UNITS_PER_DEG = 1023 / 300.0
SETTLE_S = 0.45          # long enough for a small move to finish and settle
N_READS = 5              # median, so one bad sample cannot invent a movement


def read_median(bus, sid):
    bus.flush_input()
    vals = sorted(v for v in (bus.read_pos(sid) for _ in range(N_READS))
                  if v is not None)
    return vals[len(vals) // 2] if vals else None


def probe(bus, joint, span):
    sid = IDS[joint]
    centre = cal.CENTRE.get(joint, 512)
    lo, hi = cal.LIMITS.get(joint, (centre - 100, centre + 100))
    print(f"\n=== {joint} (id {sid})  centre {centre}  limits ({lo},{hi}) ===")

    bus.write_pos(sid, centre, time_ms=400, speed=0)
    bus.torque(sid, True)
    time.sleep(0.8)
    base = read_median(bus, sid)
    if base is None:
        print("  no reply -- skipping")
        return
    print(f"  resting at {base} (commanded {centre})")
    print(f"  {'cmd step':>9}{'reached':>9}{'moved':>7}{'deg':>8}   verdict")

    smallest = None
    for step in (1, 2, 3, 4, 6, 8, 12, 16):
        if step > span:
            break
        if not (lo <= centre + step <= hi and lo <= centre - step <= hi):
            print(f"  {step:>9}   outside LIMITS -- stopping")
            break
        # Go out and come back, and measure the OUTWARD position. A joint that
        # only moves when pushed further than the dead band will show a moved
        # count of 0 here while still tracking large steps perfectly.
        bus.write_pos(sid, centre + step, time_ms=300, speed=0)
        time.sleep(SETTLE_S)
        out = read_median(bus, sid)
        bus.write_pos(sid, centre, time_ms=300, speed=0)
        time.sleep(SETTLE_S)
        moved = abs(out - base) if out is not None else 0
        verdict = ""
        if moved <= 1:
            verdict = "no movement -- inside the dead band"
        elif smallest is None:
            smallest = step
            verdict = "<<< SMALLEST STEP THAT MOVES"
        print(f"  {step:>9}{out if out is not None else '--':>9}{moved:>7}"
              f"{step / UNITS_PER_DEG:>8.2f}   {verdict}")

    print()
    if smallest is None:
        print(f"  {joint}: nothing up to {span} units moved it. Check power "
              f"(4.3 V back-feed reads fine and cannot move a load) and torque.")
    else:
        d = smallest / UNITS_PER_DEG
        print(f"  {joint}: floor = {smallest} units = {d:.2f} deg.")
        print(f"    Any authored motion smaller than this is invisible on hardware")
        print(f"    however good it looks in Blender. For a noise or breath layer,")
        print(f"    author at >= {2 * smallest} units ({2 * d:.2f} deg) peak-to-peak so the")
        print(f"    joint crosses the floor in BOTH directions.")


def sweep(bus, joint, span):
    """Smallest amplitude the joint TRACKS during continuous motion.

    Peak-to-peak alone is the wrong metric and it lied on the first run: a joint
    that stick-slips -- holds, then breaks away in a jump -- shows a healthy p-p
    while producing motion that looks nothing like the commanded curve. tilt
    "tracked" 3 units and failed 4, which is not physically possible and was the
    tell. So this correlates the measured trajectory against the commanded one,
    at the best of several lags (the servo always trails), and requires BOTH a
    good correlation and enough range.
    """
    import math
    sid = IDS[joint]
    centre = cal.CENTRE.get(joint, 512)
    lo, hi = cal.LIMITS.get(joint, (centre - 100, centre + 100))
    print(f"\n=== {joint} (id {sid}) CONTINUOUS -- 4 s sinusoid per amplitude ===")
    bus.write_pos(sid, centre, time_ms=400, speed=0)
    bus.torque(sid, True)
    time.sleep(0.8)
    print(f"  {'amp':>5}{'deg':>7}{'cmd p-p':>9}{'meas p-p':>10}{'corr':>7}   verdict")

    def corr(a, b):
        n = min(len(a), len(b))
        if n < 8:
            return 0.0
        a, b = a[:n], b[:n]
        ma, mb = sum(a) / n, sum(b) / n
        ca, cb = [x - ma for x in a], [x - mb for x in b]
        den = math.sqrt(sum(x * x for x in ca) * sum(x * x for x in cb))
        return (sum(ca[i] * cb[i] for i in range(n)) / den) if den > 1e-9 else 0.0

    smallest = None
    PERIOD_S, DUR_S, HZ = 4.0, 4.0, 25.0
    for amp in (2, 3, 4, 6, 8, 12, 16):
        if amp > span:
            break
        if not (lo <= centre + amp <= hi and lo <= centre - amp <= hi):
            print(f"  {amp:>5}   outside LIMITS -- stopping")
            break
        cmd, meas = [], []
        t0 = time.time()
        while time.time() - t0 < DUR_S:
            ph = (time.time() - t0) / PERIOD_S
            c = centre + amp * math.sin(2 * math.pi * ph)
            bus.write_pos(sid, int(round(c)), time_ms=0, speed=0)
            v = bus.read_pos(sid)
            if v is not None:
                cmd.append(c)
                meas.append(float(v))
            time.sleep(1.0 / HZ)
        bus.write_pos(sid, centre, time_ms=300, speed=0)
        time.sleep(0.3)

        # Best correlation over a few samples of lag -- the servo always trails,
        # and a fixed lag of zero penalises a joint that is tracking perfectly.
        best = max((corr(cmd[:len(cmd) - k] if k else cmd, meas[k:]),)
                   for k in range(0, 7))[0] if len(meas) > 12 else 0.0
        pp = (max(meas) - min(meas)) if meas else 0
        ok = best >= 0.85 and pp >= 0.5 * (2 * amp)
        if ok and smallest is None:
            smallest = amp
        note = ("<<< SMALLEST TRACKED" if (ok and smallest == amp)
                else "" if ok
                else ("jumpy -- range without fidelity" if pp >= 0.5 * (2 * amp)
                      else "not tracking"))
        print(f"  {amp:>5}{amp / UNITS_PER_DEG:>7.2f}{2 * amp:>9}{pp:>10.0f}"
              f"{best:>7.2f}   {note}")

    print()
    if smallest is None:
        print(f"  {joint}: nothing up to {span} units tracked faithfully.")
    else:
        d = smallest / UNITS_PER_DEG
        print(f"  {joint}: continuous floor = {smallest} units = {d:.2f} deg amplitude")
        print(f"    ({2 * smallest} units / {2 * d:.2f} deg peak-to-peak).")
        print(f"    corr >= 0.85 means the joint is following the CURVE, not just")
        print(f"    covering the range. Below this, motion still happens -- it is")
        print(f"    just not the motion that was authored.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("joint", nargs="?", choices=ORDER,
                    help="one joint, else all three")
    ap.add_argument("--span", type=int, default=16,
                    help="largest step to try, in units (default 16 = 4.7 deg)")
    ap.add_argument("--sweep", action="store_true",
                    help="measure the CONTINUOUS floor instead of the step floor")
    a = ap.parse_args()

    try:
        bus, port = open_bus()
    except IOError as e:
        sys.exit(str(e))
    print(f"port {port}")

    joints = [a.joint] if a.joint else ORDER
    try:
        for j in joints:
            (sweep if a.sweep else probe)(bus, j, a.span)
    finally:
        for sid in IDS.values():
            try:
                bus.torque(sid, False)
            except Exception:
                pass
        bus.close()
        print("\ntorque off, port closed.")


if __name__ == "__main__":
    main()
