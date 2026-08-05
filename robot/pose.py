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
from robot import IDS  # single source of truth: robot/__init__.py
CENTER = 512                    # the clips' neutral, in raw exported units
UNITS_PER_DEG = 1023 / 300.0

# Speed the hardware may be ASKED for outside of a clip: transitions, pre-rolls,
# centring. Inside a clip the authored timing rules and is not second-guessed --
# the ease curves are the design. This ceiling is for the moves between clips,
# which nobody authored.
SAFE_DPS = 120.0
MIN_MOVE_MS = 220               # floor, so tiny moves still read as movement

# ONE transition is not a transition: the turn to a direction the person has just
# pointed out (S6 -> S5a). There is no clip around it, so that travel IS the whole
# event -- the robot's answer to "look over there" -- and a move that carries a
# result may not be left at a default. It runs at S4's own travel speed, so the
# body moves at the SAME rate whether it chose the direction or was told it.
#
# That is the point: a single travel speed keeps travel from becoming a MORPHEME.
# If it varied, every turn in the library would mean something and would have to
# be defended. Fixed, it means nothing, which is exactly what lets the authored
# beats -- the crane, the shake, the droop -- carry all of it.
REAIM_DPS = 75.0                # = generate_s4_sweep.STATION_SPEED

# And one more, for the same reason and the opposite feeling: the collapse into
# S8. S8 is entered from anywhere -- an unusable transcript in S2, a failed plan
# in S4, a dead camera in S5 -- so there is no predecessor pose to inherit and no
# authored distance, which is exactly the case a clip cannot hold and a speed can.
#
# A TRANSITION INTO A STATE SHOULD NOT BE FASTER THAN THE STATE ITSELF MOVES, or
# the arrival contradicts the thing it arrives at. Sprinting into a posture that
# means "I have run out of ideas" would undo it before it is held. So this is
# S8's own swing peak: the robot deflates at the speed it then sways at.
COLLAPSE_DPS = 47.0             # = generate_s8_error.py's swing peak, measured


def reach_deg(name):
    """(min, max) Blender degrees this joint can actually hold, in one place.

    Derived from the live LIMITS/OFFSET/INVERT rather than written down, because
    a written-down range survives a re-calibration and a derived one cannot. Pan
    is roughly -68..+70 on the current mount: THE BODY DOES NOT TURN BEHIND
    ITSELF. Anything that takes an angle from a person -- a seat preset, a
    re-aim, the S7 user lock -- has to ask this before believing the number, or
    it will store a bearing the robot can never adopt and then compute from it.
    """
    lo_u, hi_u = cal.LIMITS[name]
    edges = []
    for u in (lo_u, hi_u):
        v = u - cal.OFFSET[name]
        if cal.INVERT[name]:
            v = 1023 - v
        edges.append((v - CENTER) / UNITS_PER_DEG)
    return (min(edges), max(edges))


def clamp_deg(name, deg):
    """-> (reachable_deg, was_clamped). The single place a request becomes a fact."""
    lo, hi = reach_deg(name)
    d = float(deg)
    c = max(lo, min(hi, d))
    return c, abs(c - d) > 0.05


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


def move_ms(from_units, to_units, dps=None):
    """How long a move between two poses should take, at `dps` (default SAFE_DPS).

    Distance-scaled, not constant. A fixed duration is the trap here: 200 ms is
    right between neighbouring poses and becomes 600 deg/s across the library
    (S2 ends at pan +60, S4 opens at -60 -- 120 deg apart).

    A SPEED rather than a duration is also what makes the re-aim authorable at
    all. The angle the person points at is arbitrary and only known at runtime,
    so it cannot live in a clip; and an eased curve authored for one distance
    cannot be stretched to another without rewriting the thing it carries (which
    is why set_pan_deg refuses to retarget S4). A speed travels any distance
    correctly, so the design decision survives not knowing the number.
    """
    far = max(abs(to_units[n] - from_units[n]) for n in JOINTS)
    ms = far / UNITS_PER_DEG / float(dps or SAFE_DPS) * 1000.0
    return int(max(MIN_MOVE_MS, ms)), far
