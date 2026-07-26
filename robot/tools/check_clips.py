#!/usr/bin/env python3
"""
Validate exported clip CSVs against this build's calibration, before any of them
touch the servos.

  python3 check_clips.py ../../motion/clips
  python3 check_clips.py ../../motion/clips --ceiling 150

Checks per clip:
  * columns, frame count, duration, frame rate consistency
  * peak speed per axis against the authoring ceiling
  * every commanded pose against LIMITS -- reports what WOULD be clamped
  * loop clips: first frame must equal last frame on all three axes

And across clips:
  * handover poses match, so state transitions do not jump
    (S4 -> S5, S7a -> S7b)

Clamping is the quiet failure: playback does not error, the joint just stops
short and the motion loses its shape. It is much cheaper to find here.
"""
import argparse, csv, os, sys

# Run me directly: the repo root is two levels up from robot/tools/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from robot import calibration as cal

AXES = ("pan", "tilt", "nod")
UNITS_PER_DEG = 1023 / 300.0
LOOP_CLIPS = {"S1_IDLE", "S5_TRACK", "S8_ERROR"}
# clip -> clip: the first must end where the second begins
HANDOVERS = [("S1_IDLE", "S2_LISTEN"), ("S2_LISTEN", "S3_ACK"),
             ("S3_ACK", "S4_PLAN"), ("S4_PLAN", "S5_TRACK"),
             ("S5_TRACK", "S6_FINETUNE"), ("S6_FINETUNE", "S5_TRACK"),
             ("S5_TRACK", "S7a"), ("S7a", "S7b")]

# Not every pair needs to match: the state machine eases between states. What
# matters is how FAR it has to ease, because a fixed transition time turns a
# large gap into a fast move. TRANSITION_MS is what the state machine intends
# to use; SAFE_DPS is what the hardware should be asked to do.
TRANSITION_MS = 200.0
SAFE_DPS = 120.0


def resolve(name, unit):
    u = int(round(float(unit)))
    if cal.INVERT[name]:
        u = 1023 - u
    u += cal.OFFSET[name]
    lo, hi = cal.LIMITS[name]
    return u, max(lo, min(hi, u))


def load(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError("empty")
    missing = [c for a in AXES for c in (f"{a}_deg", f"{a}_unit")
               if c not in rows[0]]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    return rows


def check(path, ceiling):
    name = os.path.basename(path)[:-4]
    problems = []
    rows = load(path)
    t = [float(r["t_ms"]) for r in rows]
    dur = t[-1] / 1000.0
    fps = (len(rows) - 1) / dur if dur else 0

    print(f"\n{name}  {len(rows)} frames  {dur:.2f}s  ~{fps:.0f}fps"
          + ("  [LOOP]" if name in LOOP_CLIPS else ""))

    for a in AXES:
        degs = [float(r[f"{a}_deg"]) for r in rows]
        peak = 0.0
        for (t0, d0), (t1, d1) in zip(zip(t, degs), zip(t[1:], degs[1:])):
            dt = (t1 - t0) / 1000.0
            if dt > 0:
                peak = max(peak, abs(d1 - d0) / dt)

        units = [resolve(a, r[f"{a}_unit"]) for r in rows]
        raw = [u for u, _ in units]
        clamped = sum(1 for u, c in units if u != c)
        lo, hi = cal.LIMITS[a]
        margin = min(min(raw) - lo, hi - max(raw))

        flag = ""
        if clamped:
            flag = f"  <-- {clamped} frames CLAMPED"
            problems.append(f"{a} clamped on {clamped} frames")
        elif peak > ceiling:
            flag = f"  <-- over {ceiling:.0f} deg/s"
            problems.append(f"{a} peaks at {peak:.0f} deg/s")

        print(f"  {a:<5} {min(degs):+7.1f}..{max(degs):+7.1f} deg   "
              f"units {min(raw):>4}..{max(raw):<4} (limits {lo}-{hi}, "
              f"margin {margin:>4})  peak {peak:>5.0f} deg/s{flag}")

    if name in LOOP_CLIPS:
        for a in AXES:
            a0 = float(rows[0][f"{a}_deg"])
            a1 = float(rows[-1][f"{a}_deg"])
            if abs(a0 - a1) > 0.5:
                print(f"  !! loop seam: {a} starts {a0:+.1f} ends {a1:+.1f}")
                problems.append(f"{a} loop seam {a0:+.1f} -> {a1:+.1f}")
    return name, rows, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--extra", nargs="*", default=[],
                    help="extra clip files from outside the folder, e.g. the "
                         "older 2-DOF ../../motion/clips/S1_IDLE.csv")
    ap.add_argument("--ceiling", type=float, default=200.0,
                    help="peak deg/s to flag. 200 is the authoring limit at 6V; "
                         "on batteries something lower is honest.")
    a = ap.parse_args()

    paths = [os.path.join(a.folder, f) for f in sorted(os.listdir(a.folder))
             if f.endswith(".csv")] + list(a.extra)
    if not paths:
        sys.exit(f"no CSVs in {a.folder}")

    print(f"calibration: INVERT={cal.INVERT}")
    print(f"             OFFSET={cal.OFFSET}")

    clips, all_problems = {}, {}
    for p in paths:
        try:
            name, rows, probs = check(p, a.ceiling)
            clips[name] = rows
            if probs:
                all_problems[name] = probs
        except Exception as e:
            print(f"\n{p}: FAILED -- {e}")
            all_problems[p] = [str(e)]

    # Which handovers are DESIGNED CONTINUATIONS (states.py `then`) versus merely
    # possible. A gap is not a defect either way -- the player bridges it with a
    # distance-scaled move. What matters is how long that bridge takes when it
    # sits inside the designed cycle, because there it is an unauthored pause in
    # the middle of something that was authored.
    try:
        from robot import states as ST
        from robot import pose
        CONTINUATIONS = {(k, v["then"]) for k, v in ST.STATES.items() if v["then"]}
    except Exception:
        CONTINUATIONS, pose = set(), None

    print("\n--- handovers (bridge = unauthored move the player must insert) ---")
    for src, dst in HANDOVERS:
        if src not in clips or dst not in clips:
            print(f"  {src} -> {dst}: skipped (not both present)")
            continue
        deltas, units_end, units_start = [], {}, {}
        for ax in AXES:
            e = float(clips[src][-1][f"{ax}_deg"])
            s = float(clips[dst][0][f"{ax}_deg"])
            units_end[ax] = resolve(ax, clips[src][-1][f"{ax}_unit"])[1]
            units_start[ax] = resolve(ax, clips[dst][0][f"{ax}_unit"])[1]
            if abs(e - s) > 0.5:
                deltas.append(f"{ax} {e:+.0f}->{s:+.0f}")
        chained = (src, dst) in CONTINUATIONS
        tag = "chained" if chained else "       "
        if not deltas:
            print(f"  {tag} {src:<11} -> {dst:<11} seamless")
            continue
        ms = far = None
        if pose:
            ms, far = pose.move_ms(units_end, units_start)
        print(f"  {tag} {src:<11} -> {dst:<11} bridge "
              f"{ms}ms ({far} units)   " + ", ".join(deltas))
        if chained and ms and ms > 400:
            all_problems.setdefault(src, []).append(
                f"{ms}ms unauthored bridge into {dst} (inside the designed cycle)")

    print("\n--- transition budget ---")
    print(f"  worst gap between the end of any clip and the start of any other,")
    print(f"  i.e. what a state change may have to cover:")
    worst = (0.0, None, None, None)
    for src in clips:
        for dst in clips:
            if src == dst:
                continue
            for ax in AXES:
                d = abs(float(clips[src][-1][f"{ax}_deg"])
                        - float(clips[dst][0][f"{ax}_deg"]))
                if d > worst[0]:
                    worst = (d, src, dst, ax)
    d, src, dst, ax = worst
    need = d / (TRANSITION_MS / 1000.0)
    print(f"    {src} -> {dst}: {ax} moves {d:.0f} deg")
    print(f"    in {TRANSITION_MS:.0f} ms that is {need:.0f} deg/s"
          + (f"  <-- over the {SAFE_DPS:.0f} deg/s you can safely ask for"
             if need > SAFE_DPS else "  ok"))
    if need > SAFE_DPS:
        print(f"    at {SAFE_DPS:.0f} deg/s it needs {d / SAFE_DPS * 1000:.0f} ms.")
        print(f"    => transition time must SCALE WITH DISTANCE. A constant "
              f"{TRANSITION_MS:.0f} ms")
        print(f"       is fine between neighbouring poses and a lurch across the "
              f"library.")

    print("\n--- summary ---")
    if not all_problems:
        print("  all clips clean")
    else:
        for k, v in all_problems.items():
            print(f"  {k}: " + "; ".join(v))
    return 1 if all_problems else 0


if __name__ == "__main__":
    sys.exit(main())
