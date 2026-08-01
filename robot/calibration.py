"""Written by jog.py. Safe to hand-edit -- this is config, not a
build artifact (unlike the clip CSVs, which get overwritten by the
next Blender export).

LIMITS are a guard rail, not a record of how far the joint can go:
set them wider than the motion needs and well short of anything that
can jam.
"""
UNCALIBRATED = set()

CENTRE = {
    "pan": 251,
    "tilt": 492,
    "nod": 686,
}
LIMITS = {
    "pan": (80, 251),
    "tilt": (401, 621),
    "nod": (470, 750),
}
OFFSET = {
    "pan": -261,
    "tilt": -20,
    "nod": 174,
}
# INVERT is carried over, never measured here: which way a jog
# key moves a joint does NOT tell you whether a clip plays
# mirrored. Only playing one and comparing with the render does.
# After re-mounting a horn, treat every entry below as stale.
INVERT = {
    "pan": True,
    "tilt": True,
    "nod": True,
}
