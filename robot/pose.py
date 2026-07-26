"""
The single place that turns a clip's servo unit into a unit this build can be
commanded to, and the single place that decides how long a move may take.

Everything that drives the servos imports from here. The mapping used to live
inside play_on_hardware.py; once a second player needed it, a copy would have
been the start of two subtly different robots -- an INVERT flipped in one file
and not the other produces motion that is wrong in a way no error message
mentions.
"""
from robot import calibration as cal

JOINTS = ("pan", "tilt", "nod")
IDS = {"pan": 1, "tilt": 2, "nod": 3}
CENTER = 512                    # the clips' neutral, in raw exported units
UNITS_PER_DEG = 1023 / 300.0

# Speed the hardware may be ASKED for outside of a clip: transitions, pre-rolls,
# centring. Inside a clip the authored timing rules and is not second-guessed --
# the ease curves are the design. This ceiling is for the moves between clips,
# which nobody authored.
SAFE_DPS = 120.0
MIN_MOVE_MS = 220               # floor, so tiny moves still read as movement


def resolve(name, raw_unit):
    """Clip unit -> commanded unit. Order matters: mirror, then trim, then clamp.

    Returns (unit, was_clamped). Callers should treat was_clamped as a bug in the
    clip rather than something to route around: a clamped frame means the motion
    silently lost its shape.
    """
    u = int(round(float(raw_unit)))
    if cal.INVERT[name]:
        u = 1023 - u
    u += cal.OFFSET[name]
    lo, hi = cal.LIMITS[name]
    c = max(lo, min(hi, u))
    return c, (c != u)


def unit_to_deg(name, commanded_unit):
    """Commanded unit -> Blender degrees. The exact inverse of resolve().

    Written as the inverse rather than tracked alongside, because anything that
    remembers the angle it asked for will eventually disagree with the angle the
    joint is at -- after a clamp, after a re-aim, after a clip ends early. The
    sweep labels its captured frames with this, and a frame filed under the wrong
    angle is worse than a frame not captured at all.
    """
    u = int(round(float(commanded_unit))) - cal.OFFSET[name]
    if cal.INVERT[name]:
        u = 1023 - u
    return (u - CENTER) / UNITS_PER_DEG


def centre_units():
    """Where the clips' neutral pose lands on this build."""
    return {n: resolve(n, CENTER)[0] for n in JOINTS}


def move_ms(from_units, to_units):
    """How long a move between two poses should take, at SAFE_DPS.

    Distance-scaled, not constant. A fixed duration is the trap here: 200 ms is
    right between neighbouring poses and becomes 600 deg/s across the library
    (S2 ends at pan +60, S4 opens at -60 -- 120 deg apart).
    """
    far = max(abs(to_units[n] - from_units[n]) for n in JOINTS)
    ms = far / UNITS_PER_DEG / SAFE_DPS * 1000.0
    return int(max(MIN_MOVE_MS, ms)), far
