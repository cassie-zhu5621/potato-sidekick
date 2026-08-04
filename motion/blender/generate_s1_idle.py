# Auto-generates the S1 IDLE loop (3-DOF + LED). Run inside S1_IDLE.blend after
# repair_rig.py + add_nod_joint.py. OVERWRITES all keys.
# Design rationale: ../../../robot_motion/S1_DESIGN.md (local, not in this repo).
# Comments here are operational -- what a value must not break. The
# argument for the design, the literature, and the alternatives that were
# rejected live in that document, so they can be revised without touching
# code and cited from the paper in place.
#
# v4 -- A SLEEPING POSE, HELD. The body is still; the LED breathes; every so
# often the sleeper shifts.
#
# THE MEASUREMENT THAT DECIDES THIS CLIP. There are two floors on this hardware,
# not one, and every earlier version of S1 fell foul of the second:
#
#   amplitude floor   >= 7 units (tilt) / 12 (nod)   or the joint does not move
#   SMOOTHNESS floor  >= 8.8 deg/s                    or it moves in visible steps
#
# The second comes from quantisation: 1 unit = 0.293 deg, so at 30 fps the
# command only advances a whole unit per frame above 8.8 deg/s. Slower than that
# and the joint waits, accumulates error, breaks past its dead band, and jumps.
# v3's 3 deg breath peaked at 2.77 deg/s -- a third of a unit per frame -- which
# is exactly why it read as stuttering rather than breathing.
#
# And the ambient salience budget caps velocity at about 5.4 deg/s (0.5 deg/s at
# the eye at 1.2 m). 5.4 < 8.8, so:
#
#   ON THIS HARDWARE, MOTION SLOW ENOUGH TO BE AMBIENT CANNOT BE SMOOTH.
#
# Continuous breathing on a joint is therefore not available at any amplitude.
# The opposite shape -- RARE AND FAST, an occasional shift -- clears both floors
# and was built and then REJECTED. Average salience was the wrong statistic to
# judge it by: a discrete movement in an otherwise still field is conspicuous by
# construction, because onset-then-offset against a static background is what an
# event IS, and events get interpreted.
#
# And in THIS system that interpretation is forced. Every other state's motion is
# a signal; the grammar says so. A user who has learned that the robot moving
# means something will look for the meaning of a sleep-shift and not find one.
# It also breaks the project's own movement <=> result rule (S7_DESIGN sec 2),
# which requires every movement to be the signature of a real detection. A shift
# with no referent is precisely what that rule forbids.
#
# So S1 has NO joint motion at all. That is not a shortfall; it is the rule
# extended to its limit -- no result, no movement.
#
# Why stillness does not read as "broken" here: because the POSE says asleep, and
# sleeping things are supposed to be still. Song et al. (2009) found a motionless
# robot reads as switched off -- but that was a robot standing NEUTRAL. The frame
# changes what stillness means, which is the same argument (Bucci et al., CHI
# 2017) that motivated dropping the upright breath in the first place.

import bpy
import math

# ---- the sleeping pose: the whole signal ----
# Two joints flexing forward, the head flexing MORE than the neck.
#   * one rigid rotation keeps the neck-head silhouette a straight diagonal,
#     which reads as a mechanism powering down
#   * adding head flexion breaks it into a curve -- a line of action (Williams;
#     Thomas & Johnston), which is what a resting body has
#   * the head falling FURTHER than the neck is how weight is communicated
# Static postures alone carry emotion attribution reliably, and lowered postures
# are among the best-recognised (Coulson 2004, J. Nonverbal Behavior 28(2)).
DROOP_TILT = -8.0      # neck forward. NEGATIVE is forward on this rig (S4 uses
                       # LEAN_TILT -12 for "craning forward"). Deliberately
                       # shallow: on this short-necked form a deep neck droop
                       # starts to read as the object TOPPLING rather than
                       # resting, and the shape wanted here is the head tucking
                       # IN, not the body slumping over.
                       #
                       # Enough forward set remains that the head folds toward
                       # the body rather than hanging in front of it -- at 0 the
                       # silhouette would be a vertical column with a hinge on
                       # top, which reads as "looking down at the desk" and
                       # collides with S5.
                       #
                       # Holding torque goes as sin(droop): -8 is about 3% of the
                       # SCS0009's stall, half of what -12 cost. S1 holds this for
                       # hours.
DROOP_NOD = -42.0      # head a further 42 deg DOWN relative to the neck -- chin
                       # folded into the body. Blender positive nod = head UP, so
                       # down is negative. Absolute head pitch: about -50 deg.
                       #
                       # Re-calibrated 2026-08-01: nod now runs (474,814) about
                       # centre 644 = +/-49.9 deg, nearly double the old +/-26.4.
                       # -42 leaves 27 units (7.9 deg) to the hardware rail and
                       # 3 deg to the Blender constraint (set_nod_limit.py, +/-45).
                       #
                       # The only ceiling that still matters is MECHANICAL -- the
                       # chin meeting the neck or shell -- and nobody has measured
                       # it. Push this number in Blender until the head visibly
                       # interpenetrates, back off 2-3 deg, and THAT is the value.
                       # Record it: it is the shell geometry setting the limit of
                       # the postural vocabulary, which is a finding, not a
                       # setting. If it lands well below -42, the answer is a
                       # cut-away under the chin on the next shell, not a smaller
                       # pose.
                       #
                       # To go past -45 also raise LIMIT_DEG in set_nod_limit.py.
                       # Keep it under the calibrated 49.9 so Blender stays the
                       # conservative one.
                       #
                       # A clamped pose is silent: nothing errors, the robot just
                       # stops holding the pose that was designed. check_clips
                       # reports it -- read that line before believing a run.

# ---- the occasional shift: BUILT, THEN REJECTED ----
# Kept as code because the rejection is a design finding, not a dead end, and
# because it is the only motion shape this hardware can render smoothly at all.
# Set SHIFT_DEG > 0 to see it. Leave it at 0.
SHIFT_DEG = 0.0        # 8.0 was the tested value: 27 units, peak 27 deg/s, both
                       # floors cleared. Rejected on meaning, not on mechanics --
                       # see the header. In a system where movement is the signal
                       # vocabulary, an unreferenced movement is a signal that
                       # fails to parse.
SHIFT_S = 0.6          # duration. Peak works out near 26 deg/s -- THREE TIMES
                       # the smoothness floor, so this move is genuinely smooth
                       # where a slow one cannot be.
SHIFT_AT_S = 23.0      # when in the loop it happens. Anywhere with room before
                       # the seam; 23 s is off-centre so it does not feel metrical.
SHIFT_NOD_FRAC = 0.45  # the head follows the neck, but only partly and LATE --
                       # drag/follow-through. A head that tracked the neck exactly
                       # would make the two joints one rigid piece again.
SHIFT_NOD_LAG_S = 0.15

# ---- LED: the only thing that runs continuously ----
BREATH_S = 5.0         # 12 breaths/min, the slow end of adult resting
                       # respiration (12-20/min).
BREATH_RISE = 0.34     # resting expiration is PASSIVE and longer than
                       # inspiration, roughly 1:2. A symmetric envelope is the one
                       # thing a resting body does not do.
LED_LO = 0.30          # never 0 -- off reads as powered down, not asleep. PWM is
                       # linear and perceived brightness goes as roughly L^0.43,
                       # so 26..80 of 255 was only a 1.6x perceived swing while
                       # 10..80 is 2.5x for the same peak.
                       # FIRMWARE MUST MATCH: FB_LO = 0.30/8 = 0.0375,
                       # FB_SPAN = (2.5-0.30)/8 = 0.275.
LED_HI = 2.5           # vs 8.0 for S7's accents. Idle must not draw the eye.
LED_WARM = (1.00, 0.62, 0.22, 1.0)
                       # Warm and dim for three independent reasons (LED_PALETTE):
                       # low brightness and saturation give low arousal (Valdez &
                       # Mehrabian 1994); short-wavelength light is specifically
                       # ALERTING via melanopsin, the last thing a sleeping state
                       # should be; and warm suits a domestic object at rest.

N_BREATHS = 8          # loop length = N_BREATHS * BREATH_S. Must be a whole
                       # number of breaths or the LED ticks at the seam.
SAMPLE_F = 3
FPS = 30

VIEW_GAIN = 1.0        # DEBUG ONLY. Scales the SHIFT, never the pose. MUST be
                       # 1.0 before export_clip.py.
# -----------------------------------------------------------

BREATH_F = int(round(BREATH_S * FPS))
LOOP_F = BREATH_F * N_BREATHS
SHIFT_F = int(round(SHIFT_S * FPS))
SHIFT_AT_F = int(round(SHIFT_AT_S * FPS))
SHIFT_NOD_LAG_F = int(round(SHIFT_NOD_LAG_S * FPS))

if SHIFT_DEG and SHIFT_AT_F + SHIFT_F + SHIFT_NOD_LAG_F >= LOOP_F:
    raise RuntimeError("the shift runs past the loop seam -- move SHIFT_AT_S earlier")

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
    led_color.default_value = LED_WARM
    mat.diffuse_color = LED_WARM


def key_led(frame, value):
    if led_strength is not None:
        led_strength.default_value = value
        led_strength.keyframe_insert(data_path="default_value", frame=frame)


def breath(i):
    """0 -> 1 -> 0 over one breath, rising for BREATH_RISE of it. Zero slope at
    both ends and at the crest, so the seam has no discontinuity."""
    p = (i % BREATH_F) / float(BREATH_F)
    if p < BREATH_RISE:
        return 0.5 * (1.0 - math.cos(math.pi * p / BREATH_RISE))
    return 0.5 * (1.0 + math.cos(math.pi * (p - BREATH_RISE) / (1.0 - BREATH_RISE)))


def shift(i, lag_f=0):
    """0 -> 1 -> 0 over SHIFT_F frames, once per loop. Raised cosine, so it eases
    out of stillness and back into it rather than starting with a jerk."""
    if not SHIFT_DEG:
        return 0.0
    k = i - SHIFT_AT_F - lag_f
    if k < 0 or k >= SHIFT_F:
        return 0.0
    return 0.5 * (1.0 - math.cos(2.0 * math.pi * k / float(SHIFT_F)))


def pose_at(i):
    s_t = shift(i)
    s_n = shift(i, SHIFT_NOD_LAG_F)
    # The shift LIFTS the neck (toward upright) and the head follows partly.
    return (DROOP_TILT + VIEW_GAIN * SHIFT_DEG * s_t,
            DROOP_NOD + VIEW_GAIN * SHIFT_DEG * SHIFT_NOD_FRAC * s_n)


# Key the shift densely and the still stretches sparsely: nothing is happening
# for most of the loop, and a keyframe every 3 frames of a constant value is just
# a bigger CSV.
frames = set(range(1, LOOP_F + 1, SAMPLE_F)) | {1, LOOP_F}
if SHIFT_DEG:
    frames |= set(range(SHIFT_AT_F, SHIFT_AT_F + SHIFT_F + SHIFT_NOD_LAG_F + 2))

for f in sorted(frames):
    # Frame LOOP_F carries frame 1's values, not frame LOOP_F-1's. The clip is
    # played on repeat, so the last frame IS the first frame; computing it from
    # its own index leaves a fraction of a unit at the seam, which becomes a
    # visible tick once per loop. (It did: the LED came out 0.300 vs 0.301.)
    i = 0 if f == LOOP_F else f - 1
    t_deg, n_deg = pose_at(i)
    key(pan, "z", f, 0.0)
    key(tilt, "x", f, t_deg)
    key(nod, "x", f, n_deg)
    key_led(f, LED_LO + (LED_HI - LED_LO) * breath(i))

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = LOOP_F

peak_shift = (2.0 * SHIFT_DEG / SHIFT_S) if SHIFT_DEG else 0.0
msg = (f"S1 idle v4: pose neck {DROOP_TILT:+.0f} / head {DROOP_NOD:+.0f} "
       f"(absolute {DROOP_TILT + DROOP_NOD:+.0f}), HELD; "
       f"LED {LED_LO}-{LED_HI} breathing at {BREATH_S:.1f}s; "
       + (f"shift {SHIFT_DEG:.0f} deg in {SHIFT_S:.1f}s at {SHIFT_AT_S:.0f}s "
          f"(peak {peak_shift:.0f} deg/s); " if SHIFT_DEG else "no shift (still); ")
       + f"{LOOP_F}f ({LOOP_F / FPS:.0f}s)")
print(msg + ("" if VIEW_GAIN == 1.0 else f"  *** VIEW_GAIN {VIEW_GAIN}x -- DO NOT EXPORT ***"))


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text="The POSE is the signal. The LED is the only thing")
    self.layout.label(text="that runs continuously. Set SHIFT_DEG = 0 to compare")
    self.layout.label(text="the pure still version.")
    if VIEW_GAIN != 1.0:
        self.layout.label(text="*** VIEW_GAIN %gx -- set to 1.0 before export ***"
                          % VIEW_GAIN, icon='ERROR')
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(
    draw, title="S1 idle v4" + ("  [DEBUG GAIN]" if VIEW_GAIN != 1.0 else ""),
    icon='ERROR' if VIEW_GAIN != 1.0 else 'INFO')
