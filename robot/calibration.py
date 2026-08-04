"""Written by jog.py. Safe to hand-edit -- this is config, not a
build artifact (unlike the clip CSVs, which get overwritten by the
next Blender export).

LIMITS are a guard rail, not a record of how far the joint can go:
set them wider than the motion needs and well short of anything that
can jam.
"""
UNCALIBRATED = set()

CENTRE = {
    "pan": 361,
    "tilt": 478,
    "nod": 652,
}
LIMITS = {
    "pan": (123, 591),
    "tilt": (382, 637),
    "nod": (414, 855),
}
OFFSET = {
    "pan": -151,
    "tilt": -34,
    "nod": 140,
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

# Carried over from deadband_probe -- not measured by jog.py.
FLOORS = {
    "pan": 6,
    "tilt": 7,
    "nod": 12,
}
