# Auto-generates the S8 ERROR loop (3-DOF + LED). Run inside S8_ERROR.blend after
# repair_rig.py + add_nod_joint.py. OVERWRITES all keys.
# Design rationale: ../../../robot_motion/S8_DESIGN.md (local, not in this repo).
#
# A slow, decaying sway with the head down and nowhere to land. Loops until a
# human intervenes; exit is STOP only.
#
# v2 (2026-08-03). One structural change and one method catch-up.
#
# 1. THE DROOP IS NOW HELD, NOT PERFORMED.
#
#    v1 sank to the droop and RECOVERED TO LEVEL by the last frame, so the loop
#    would be seamless. Measured out of the exported clip: the gaze ran
#    0 -> -13 -> 0 every pass, i.e. THE ROBOT PICKED ITS HEAD BACK UP FIFTEEN
#    TIMES A MINUTE. A machine that re-inflates every four seconds is not stuck,
#    and S8 can sit here for minutes -- that would have been dozens of
#    dejection-and-recovery cycles.
#
#    The seam was bought by undoing the posture, which is the one thing this
#    state's meaning cannot afford. A held pose is seamless for free: tilt and
#    nod are now CONSTANT at the droop for the entire loop, and pan is the only
#    channel that moves.
#
# 2. THE COLLAPSE IS A SPEED, NOT A BEAT IN THIS CLIP.
#
#    S8 is entered from anywhere -- an unusable transcript in S2 (+60/-28/+53),
#    a failed plan in S4, a dead camera in S5 -- so unlike S6 and S7 there is no
#    predecessor pose to inherit and no authored distance to travel. That makes
#    the entry a genuine transition rather than unauthored travel that
#    contradicts the clip.
#
#    But it is still the most expressive thing S8 does, so its TIMING is chosen:
#    pose.COLLAPSE_DPS, applied by clip_player when S8_ERROR is entered. Same
#    mechanism and same argument as the re-aim into S5a (pose.REAIM_DPS): an
#    eased curve authored for one distance cannot be stretched to another, and
#    the distance here is unknown until runtime -- so author a speed, which
#    travels any distance correctly.
#
#    A TRANSITION INTO A STATE SHOULD NOT BE FASTER THAN THE STATE ITSELF MOVES,
#    or the arrival contradicts the thing it arrives at. COLLAPSE_DPS is set to
#    S8's own swing peak, so the robot deflates at the speed it then sways at.
#
# WHY THIS IS NOT A CONFLICT WITH S6. The grammar note says "horizontal shake =
# negation, S6 only", and S8's horizontal excursion is in fact LARGER than S6's
# (15 deg vs 7). They are separated by SPEED, not amplitude: S6 peaks at
# ~106 deg/s and reads as a shaken head saying no; S8 peaks at ~44 and reads as a
# slow, searching sway with nowhere to land. Same axis, same amplitude order,
# opposite meaning -- decided by which stroke is quick. (The same rule that makes
# S7b a summons rather than a nod.)
#
# That separation is this design's load-bearing claim, so it is now GUARDED
# against S6's real numbers instead of a hardcoded threshold: v1 checked
# `peak > 80` using a PEAK_FACTOR of 1.6, a figure that matches no easing curve.
#
# The pose design is the hand-animated original, measured out of the v1 export:
# pan 0 -> -15 -> +10 -> -5 -> 0 over 120 frames, decaying by roughly two thirds
# each pass and SLOWING as it decays -- which is what makes it read as running
# out of ideas rather than oscillating mechanically.
#
# SIGN CONVENTION: BLENDER positive nod = head UP; on the bus a higher unit is
# DOWN. INVERT in robot/calibration.py reconciles them. Author against the render.

import bpy
import math

# ---- the damped swing (measured off the hand-animated original) ----
SWING_DEG = 15.0       # first excursion, degrees
DECAY = 0.67           # each pass keeps this fraction. Measured 15 -> 10 -> 5.
SWING_FRAMES = [19, 35, 20, 45]   # frames per pass, also measured: the swing
                                  # SLOWS as it decays. Running out of ideas.

# ---- the held pose ----
DROOP_TILT = -5.0      # neck sunk. HELD for the whole loop now, not performed.
DROOP_NOD = -8.0       # head down -- "at a loss". Gaze sits at -13, which is
                       # clearly distinct from S1_IDLE's sleep pose (gaze -50)
                       # and from S7's aimed crane (gaze -10 with a deep lean).
                       # NEGATIVE IS DOWN in Blender for nod.

# ---- LED: accented on every swing extreme, decaying with the motion ----
#
# v3 (2026-08-05). THE ENVELOPE CARRIED THE BUG v2 REMOVED FROM THE MOTION.
#
# Read the note at the top again: v1's droop "RECOVERED TO LEVEL by the last
# frame, so the loop would be seamless... the robot picked its head back up
# fifteen times a minute." The pose was fixed by holding it. The LIGHT was not.
# It still ran LED_LO -> LED_HI -> LED_LO every pass and closed on LED_LO to be
# loop-safe, so the antenna re-inflated every four seconds exactly as the neck
# used to. A pulse that re-asserts on a fixed period is an ALARM RHYTHM: it is
# the light saying "still here, still here", which is the one thing a state
# meaning "I have run out of ideas" must not say.
#
# AND IT WAS THE BRIGHTEST THING IN THE LIBRARY AFTER S7. Measured out of the
# exports: S8 peaked at 143 while S5B_TRACK -- the robot working normally --
# peaks at 96. Being stuck outshone being useful.
#
# The beats stay: tying them to the swing extremes is right, and the argument
# below (the light is the same effort as the movement) is the reason. What
# changes is the CEILING. The whole envelope now sits under S5b's, so the light
# still fades with each swing but never climbs back to a level that competes
# with working. Embers, not a beacon.
LED_LO = 0.4           # 13/255. Was 0.9 (29), then 0.6 (19).
LED_HI = 1.6           # 51/255. WAS 4.5 = 143, i.e. 1.5x S5B_TRACK's peak of 96.
                       #
                       # 2.2 (70) was the first cut and it was not enough: S1's
                       # breath runs 10..80, so at 19..70 the two states covered
                       # almost the same band and S8 only looked dimmer at the
                       # top of a breath. "Dimmer than idle" has to hold at every
                       # instant, not on average -- a participant sees one moment,
                       # not a distribution.
                       #
                       # At 13..51 the whole band sits under S1's 80 peak and its
                       # own peak is below S1's mid-breath. S8 is now unambiguously
                       # the faintest thing the robot does, which is what "the
                       # light going out of it" has to mean in numbers.
                       # Far under S7's 255 and the firmware's 150 flash threshold:
                       # a problem being reported, not an invitation.
LED_LEAD_F = 4         # dark just before each beat, so it punches
LED_TAIL_F = 5
# Colour is NOT set from this file any more -- the antenna hue comes from
# states.py (`spent`) via EVT HUE. This value only tints the Blender preview, so
# it is kept in sync by hand: see robot_motion/LED_COLOR_DESIGN.md. Amber was
# the old ALARM colour and is wrong for the same reason the pulse was.
LED_AMBER = (0.70, 0.75, 1.00, 1.0)   # = SPENT, for the render only

# ---- the S6 separation, guarded ----
S6_SHAKE_HZ = 2.4      # = generate_s6_finetune.py SHAKE_HZ
S6_SHAKE_DEG = 7.0     # = generate_s6_finetune.py SHAKE_DEG
S6_PEAK = 2.0 * math.pi * S6_SHAKE_HZ * S6_SHAKE_DEG   # sine, exact
MIN_SEPARATION = 2.0   # S8 must be at least this many times slower than S6

EASE_MODE = "minjerk"  # Flash & Hogan 1985 -- see generate_s2_listen.py. v1 set
                       # sparse keys and let Blender's DEFAULT BEZIER fill the
                       # gaps, so the exported curves were not authored at all.
FPS = 30
SAMPLE_F = 1

# ---- guard rails, read from the live calibration ----
import os
import sys

REACH_PATH = ""


def _find_up(rel, starts, levels=8):
    """Walk up, and look one step down into each level's subdirectories -- the
    .blend files live in the local design folder and the generators in the repo,
    which makes them siblings. See generate_s2_listen.py."""
    for s in starts:
        if not s:
            continue
        d = os.path.abspath(s)
        for _ in range(levels):
            p = os.path.join(d, rel)
            if os.path.exists(p):
                return p
            try:
                subs = sorted(os.listdir(d))
            except OSError:
                subs = []
            for name in subs:
                if name.startswith("."):
                    continue
                p = os.path.join(d, name, rel)
                if os.path.exists(p):
                    return p
            up = os.path.dirname(d)
            if up == d:
                break
            d = up
    return None


_starts = [os.path.dirname(bpy.data.filepath), os.getcwd()]
_rp = REACH_PATH or (_find_up(os.path.join("motion", "blender", "reach.py"), _starts)
                     or _find_up("reach.py", _starts))
if not _rp or not os.path.exists(_rp):
    raise RuntimeError(
        "Cannot find motion/blender/reach.py.\n"
        "  bpy.data.filepath = " + repr(bpy.data.filepath) + "\n"
        "  cwd               = " + os.getcwd() + "\n"
        "Save the .blend inside the repo, or set REACH_PATH above.")

reach = type(sys)("reach")
reach.__file__ = _rp
with open(_rp) as _fh:
    exec(compile(_fh.read(), _rp, "exec"), reach.__dict__)
print("[s8] " + reach.summary())

# --- the swing schedule: alternating, decaying, slowing ---
_amp, _sign = SWING_DEG, -1.0        # the original goes left first
SWING = [(1, 0.0)]
EXTREMES = []                        # (frame, effort relative to the first)
_f = 1
for _i, _dur in enumerate(SWING_FRAMES):
    _f += _dur
    _last = _i == len(SWING_FRAMES) - 1
    SWING.append((_f, 0.0 if _last else _sign * _amp))
    if not _last:
        EXTREMES.append((_f, _amp / SWING_DEG))
        _amp *= DECAY
        _sign = -_sign
END_F = _f

for _j, _v, _w in (("tilt", DROOP_TILT, "held droop, neck"),
                   ("nod", DROOP_NOD, "held droop, head"),
                   ("pan", SWING_DEG, "swing +"), ("pan", -SWING_DEG, "swing -")):
    reach.check(_j, _v, _w)
for (_f0, _v0), (_f1, _v1) in zip(SWING, SWING[1:]):
    reach.check_floor("pan", _v1 - _v0, f"swing leg at f{_f0}")

# Every leg of the swing, not just the first: the swing decays but it also slows,
# and it is not obvious by inspection which leg ends up fastest.
_FAC = math.pi / 2.0 if EASE_MODE == "cosine" else 1.875
_peak = max(abs(_v1 - _v0) / (( _f1 - _f0) / float(FPS)) * _FAC
            for (_f0, _v0), (_f1, _v1) in zip(SWING, SWING[1:]))
if _peak * MIN_SEPARATION > S6_PEAK:
    raise RuntimeError(
        f"S8 peaks at {_peak:.0f} deg/s against S6's {S6_PEAK:.0f} -- only "
        f"{S6_PEAK / _peak:.1f}x apart, under the {MIN_SEPARATION:.0f}x this "
        f"design needs. S6 and S8 share an axis and an amplitude order and are "
        f"told apart ONLY by speed, so 'I am lost' is turning into 'no'. Slow "
        f"the swing or shrink it.")

pan = bpy.data.objects["pan_pivot"]
tilt = bpy.data.objects["tilt_pivot"]
nod = bpy.data.objects.get("nod_pivot")
if not nod:
    raise RuntimeError("Run add_nod_joint.py first.")

for obj in (pan, tilt, nod):
    if obj.animation_data and obj.animation_data.action:
        obj.animation_data_clear()


def key(obj, axis, frame, deg):
    idx = {"x": 0, "z": 2}[axis]
    obj.rotation_euler[idx] = math.radians(deg)
    obj.keyframe_insert(data_path="rotation_euler", index=idx, frame=frame)


led_strength = None
led_color = None
mat = bpy.data.materials.get("led_mat")
if mat and mat.use_nodes:
    if mat.node_tree.animation_data:
        mat.node_tree.animation_data_clear()
    for n in mat.node_tree.nodes:
        if n.type == 'EMISSION':
            led_strength = n.inputs['Strength']
            led_color = n.inputs['Color']
            break
if led_color is not None:
    led_color.default_value = LED_AMBER
    mat.diffuse_color = LED_AMBER


def key_led(frame, value):
    if led_strength is not None:
        led_strength.default_value = value
        led_strength.keyframe_insert(data_path="default_value", frame=frame)


def ease(t):
    t = max(0.0, min(1.0, t))
    if EASE_MODE == "cosine":
        return 0.5 * (1.0 - math.cos(math.pi * t))
    return t * t * t * (10.0 + t * (-15.0 + 6.0 * t))


def track(points, f):
    """Value at frame `f` on a waypoint list, eased between consecutive points."""
    if f <= points[0][0]:
        return points[0][1]
    for (f0, v0), (f1, v1) in zip(points, points[1:]):
        if f <= f1:
            if f1 == f0:
                return v1
            return v0 + (v1 - v0) * ease((f - f0) / float(f1 - f0))
    return points[-1][1]


# LED: a beat at each extreme, fading exactly as the swing fades. Tied to the
# extremes rather than free-running, so the light is visibly the SAME EFFORT as
# the movement -- a free-running blink here would read as a separate indicator
# lamp bolted on, which is the opposite of the intent.
LED = [(1, LED_LO)]
for _fr, _rel in EXTREMES:
    LED += [(max(2, _fr - LED_LEAD_F), LED_LO),
            (_fr, LED_LO + (LED_HI - LED_LO) * _rel),
            (min(END_F - 1, _fr + LED_TAIL_F), LED_LO)]
LED.append((END_F, LED_LO))          # loop-safe: same as frame 1

for f in range(1, END_F + 1, SAMPLE_F):
    key(pan, "z", f, track(SWING, f))
    key(tilt, "x", f, DROOP_TILT)    # HELD. The loop is seamless because
    key(nod, "x", f, DROOP_NOD)      # nothing here returns to level.
    key_led(f, track(LED, f))

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = END_F

# states.py fires 'lost'. It belongs to the FIRST swing extreme -- where the LED
# also peaks -- not to frame 1, which is now a held pose and not a beat.
SFX_AT = (EXTREMES[0][0] - 1) / float(END_F - 1)
msg = (f"S8 error v2: droop {DROOP_TILT:+.0f}/{DROOP_NOD:+.0f} HELD (gaze "
       f"{DROOP_TILT + DROOP_NOD:+.0f}), damped sway {SWING_DEG:.0f} deg x{DECAY} "
       f"over {END_F}f ({END_F / FPS:.2f}s loop), peak {_peak:.0f} deg/s = "
       f"{S6_PEAK / _peak:.1f}x slower than S6")
print(msg)
print(f"[s8] set states.py S8_ERROR sfx_at = {SFX_AT:.2f}")


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text="Droop is HELD -- v1 recovered to level every pass,")
    self.layout.label(text="i.e. picked its head up 15 times a minute.")
    self.layout.label(text="Entry collapse = pose.COLLAPSE_DPS, not a beat here.")
    self.layout.label(text=f"SET states.py S8_ERROR sfx_at = {SFX_AT:.2f}")
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(draw, title="S8 error v2", icon='INFO')
