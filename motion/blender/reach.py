# What this build can actually reach, in AUTHORING DEGREES, derived from the
# live calibration rather than remembered.
#
# WHY THIS FILE EXISTS
#
# Every generator needs to know whether an authored angle is reachable, and the
# answer changes whenever the robot is re-calibrated or a horn is re-mounted.
# The reach numbers used to be pasted into each generator by hand, and by the
# time anyone looked they were wrong in both directions at once: too wide on
# tilt (so the guard passed angles that clip) and too narrow on nod (so it would
# have rejected a legal pose). A guard that both false-passes and false-alarms is
# worse than none, because it is trusted.
#
# So: one source of truth. calibration.py is written by jog.py against the actual
# machine; this module reads it and converts.
#
# A clipped clip does not fail loudly. The servo takes the command, stops at the
# guard rail, and the authored settle or overshoot simply is not there -- which
# looks like a design that did not work rather than a number that did not fit.
# That is the failure this is here to prevent.

import os

UNITS_PER_DEG = 1.0 / 0.293      # SCS0009: 1 unit = 0.293 deg


def find_up(rel, starts, levels=8):
    """Walk up from each start looking for `rel`, and at every level also look
    one step DOWN into each immediate subdirectory.

    The descent is not paranoia, it is the normal case here: the .blend files
    live in the local design folder (robot_motion/model/vN/) and the generators
    live in the repo (notice-sidekick-runkit/motion/blender/). Those are
    SIBLINGS, so a pure upward walk passes the common ancestor without ever
    seeing the target -- the ancestor holds the repo as a subfolder, not as its
    own contents."""
    for s in starts:
        if not s:
            continue
        d = os.path.abspath(s)
        for _ in range(levels):
            cand = os.path.join(d, rel)
            if os.path.exists(cand):
                return cand
            try:
                subs = sorted(os.listdir(d))
            except OSError:
                subs = []
            for name in subs:
                if name.startswith("."):
                    continue
                cand = os.path.join(d, name, rel)
                if os.path.exists(cand):
                    return cand
            up = os.path.dirname(d)
            if up == d:
                break
            d = up
    return None


def _load_calibration():
    """Find robot/calibration.py and read it. Blender text-blocks have no
    reliable __file__, so search from every plausible root."""
    roots = []
    try:
        import bpy
        if bpy.data.filepath:
            roots.append(os.path.dirname(bpy.data.filepath))
    except Exception:
        pass
    try:
        roots.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    roots.append(os.getcwd())

    cand = find_up(os.path.join("robot", "calibration.py"), roots)
    if cand:
        ns = {}
        with open(cand) as fh:
            exec(compile(fh.read(), cand, "exec"), ns)
        return ns, cand
    raise RuntimeError(
        "reach.py could not find robot/calibration.py. Save the .blend inside "
        "the repo (motion/src/) so the search can walk up to it, or run Blender "
        "with the repo root as the working directory. Do NOT hard-code the "
        "reach numbers back in -- that is the bug this replaced.")


_CAL, CAL_PATH = _load_calibration()
CENTRE = _CAL["CENTRE"]
LIMITS = _CAL["LIMITS"]
INVERT = _CAL["INVERT"]
FLOORS = _CAL["FLOORS"]


def _reach(joint):
    """Guard rails in units -> reachable span in authoring degrees.

    INVERT decides which way positive degrees run, so it decides which guard
    rail bounds the positive side. Getting this backwards is how a value can
    look safe on one joint and clip on another with the same numbers."""
    lo_u, hi_u = LIMITS[joint]
    c = CENTRE[joint]
    down = (c - lo_u) / UNITS_PER_DEG        # degrees available toward lo
    up = (hi_u - c) / UNITS_PER_DEG          # degrees available toward hi
    return (-up, down) if INVERT[joint] else (-down, up)


REACH = {j: _reach(j) for j in ("pan", "tilt", "nod")}
FLOOR_DEG = {j: FLOORS[j] / UNITS_PER_DEG for j in FLOORS}


def check(joint, deg, what=""):
    """Raise if an authored angle cannot be reached. Call it on every extreme
    the clip visits -- including things like user_pan + overshoot, which is
    exactly the combination that clipped when only the base value was checked."""
    lo, hi = REACH[joint]
    if not (lo <= deg <= hi):
        raise RuntimeError(
            f"{joint} {deg:+.1f} deg{' (' + what + ')' if what else ''} is "
            f"outside this build's reach ({lo:+.1f}..{hi:+.1f} deg, from "
            f"{CAL_PATH}). Re-author it, or re-calibrate and re-run -- do not "
            f"let LIMITS clamp it silently.")
    return deg


def check_floor(joint, deg_excursion, what=""):
    """Raise if an authored excursion is smaller than the joint's measured
    amplitude floor. Below the floor the servo acknowledges the command and does
    not move, which is invisible in Blender and invisible in the CSV."""
    f = FLOOR_DEG[joint]
    if abs(deg_excursion) < f:
        raise RuntimeError(
            f"{joint} excursion {abs(deg_excursion):.2f} deg"
            f"{' (' + what + ')' if what else ''} is below its measured "
            f"amplitude floor of {f:.2f} deg ({FLOORS[joint]} units). It will "
            f"be commanded, acknowledged, and never moved.")
    return deg_excursion


def summary():
    lines = [f"reach from {CAL_PATH}"]
    for j in ("pan", "tilt", "nod"):
        lo, hi = REACH[j]
        lines.append(f"  {j:5} {lo:+7.1f} .. {hi:+7.1f} deg   "
                     f"floor {FLOOR_DEG[j]:.2f} deg   invert={INVERT[j]}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())
