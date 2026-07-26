"""Written by jog.py. Safe to hand-edit -- this is config, not a
build artifact (unlike the clip CSVs, which get overwritten by the
next Blender export).

LIMITS are a guard rail, not a record of how far the joint can go:
set them wider than the motion needs and well short of anything that
can jam.
"""
UNCALIBRATED = set()

CENTRE = {
    "pan": 508,
    "tilt": 218,
    "nod": 560,
}
LIMITS = {
    "pan": (268, 768),
    # tilt has NO mechanical stop -- it runs to the servo's electrical end
    # (unit 58 was reachable), so these come from what the clips need plus
    # margin rather than from where it stops.
    # With INVERT=True the clips land on 149..299 (not 136..286), so the tight
    # side is now the TOP. Upper rail opened 318 -> 348 to keep ~14 deg there.
    "tilt": (118, 348),
    "nod": (470, 720),
}
OFFSET = {
    "pan": -4,
    "tilt": -294,
    "nod": 48,
}
INVERT = {
    # confirmed against renders/statemachine_full_demo.mp4 (S2_LISTEN): the
    # shape of the motion matched, only the direction was mirrored.
    "pan": True,
    # S3_ACK played as a head-LIFT instead of a head-dip -- mirrored too.
    "tilt": True,
    # Higher servo unit tips the head DOWN (measured by jogging), but in the
    # Blender rig positive nod renders as head UP (checked in the viewport).
    # Opposite frames, so nod inverts like the other two.
    # PROVISIONAL: set from the render, not yet seen on hardware, because no
    # clip has carried a nod channel yet. Confirm with the first 3-DOF clip --
    # if the head dips where the render lifts, this is why.
    "nod": True,
}
