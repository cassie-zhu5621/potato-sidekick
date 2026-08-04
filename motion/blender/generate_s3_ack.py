# Auto-generates S3 ACKNOWLEDGE (3-DOF + LED). Run inside S3_ACK.blend after
# repair_rig.py + add_nod_joint.py. OVERWRITES all keys.
# Design rationale: ../../../robot_motion/S3_DESIGN.md (local, not in this repo).
#
# S3 says the request landed, and hands over to the work. Entered only once a
# transcript EXISTS, so nothing here waits -- the nod and the words arrive
# together.
#
# TWO CYCLES, NOT ONE, and the reason is data rather than taste:
#
#   Hadar, Steiner & Clifford Rose (1985) instrumented five conversationalists
#   and found head-movement kinematics to be function-specific. The feature that
#   marks 'yes'/'no' is CYCLICITY: symmetrical cyclic movement signals assent,
#   while LINEAR movements -- one way and back -- do turn-claiming and
#   entrainment instead. A single down-and-return is linear.
#
#   Kimura & Jokinen et al. (2025), 9,223 nods / 16,843 cycles, found that the
#   magnitude of the FIRST cycle rises with the total number of cycles
#   ("anticipatory rising"). So magnitude and length are not free parameters:
#   a large opening dip is, in human data, the opening of a REPEATED nod.
#
# The old version of this file opened with a 22 deg dip and stopped -- a large
# first cycle with nothing after it. It was also, by then, not a nod at all:
# NOD_DIP was stored as an ABSOLUTE target, so when S2's chin-up deepened from
# +10 to +47 the sequence 47 -> 25 -> 4 -> 0 became a monotonic slump with no
# recovery, and nothing complained because every value was individually legal.
#
# HENCE THE ONE STRUCTURAL RULE HERE: the nod is authored as a MAGNITUDE
# RELATIVE to the pose it inherits, never as absolute targets. Move S2 and this
# clip moves with it instead of quietly deforming.

import bpy
import math

# ---- the pose it inherits (must match generate_s2_listen.py's closing pose) ----
USER_PAN = 60.0    # SHARED with generate_s2_listen.py -- see the long note there.
                   # Must be identical in s2/s3/s7_beckon/s7_found: it is the one
                   # direction 'the person' lies in.
LEAN = -28.0       # S2 end: neck leaning toward the user
CHIN = 53.0        # S2 end: chin up, gaze on the face

# ---- the pose it hands to ----
# S4 opens with the neck vertical and the head level and sweeps the room. Its pan
# is a scan edge rather than a social direction, so pan is left to the transition.
END_TILT = 0.0
END_NOD = 0.0

# ---- the nod ----
# Magnitudes, measured DOWN from CHIN. Never absolute targets.
CYCLE1_MAG = 18.0      # the stroke. Large-range, because S3 is a lexical
                       # response ("got it"), not a continuer -- continuer
                       # backchannels carry a smaller range of movement.
CYCLE_RATIO = 0.545    # NOT a taste value. Kimura et al.'s selected model is a
                       # Gamma GLMM with a LOG link, so its coefficients are
                       # multiplicative:
                       #     declination     b = -0.098 -> exp(b) = 0.907
                       #     final lowering  c = -0.509 -> exp(c) = 0.601
                       # For a 2-cycle nod the second cycle is both one position
                       # later AND the final one, so it takes both:
                       #     0.907 * 0.601 = 0.545
                       # This ratio is what makes two dips read as ONE NOD rather
                       # than as two events. Two equal dips are two strokes; a
                       # second at 55% is the same gesture declining, which is
                       # the shape the corpus actually contains.
CYCLE2_MAG = CYCLE1_MAG * CYCLE_RATIO      # 9.8 deg -- comfortably above nod's
                                           # 3.52 deg amplitude floor, so the
                                           # second cycle is a real movement and
                                           # not a commanded non-event.
CYCLE_S = 0.40         # per cycle: 2.5 Hz. Bounded on three sides.
                       #   below ~1.5 Hz nodding falls into a different
                       #     behavioural category, and S3 is a response;
                       #   the fast 2.6-6.5 Hz listener band is ENTRAINMENT to
                       #     the speaker's syllables, and by S3 the person has
                       #     stopped talking -- there is nothing to entrain to;
                       #   and at 18 deg a 0.30 s cycle peaks at 225 deg/s,
                       #     over the 200 ceiling. 0.40 s gives 169.
                       # The tempo the design wants and the tempo the build
                       # allows agree here, which is not always true.

# ---- the straighten ----
# A SEPARATE BEAT, not a descending baseline under the nod. Letting the baseline
# fall through the cycles would make each cycle asymmetric, and symmetry is half
# of what Hadar et al. identify as the affirmative signature. The nod oscillates
# about a HELD pose; the descent comes after.
#
# Two beats, two claims: the nod is the affirmation, the straighten is the
# undertaking. "Yes", then "on it". The nod happens with the neck still leaned in
# at -22, i.e. still inside the listening posture -- the acknowledgement belongs
# to the exchange, not to the work.
STRAIGHTEN_AT_S = 0.65     # starts 0.15 s before the second cycle ends, per the
STRAIGHTEN_S = 0.60        # library rule: each beat begins before the previous
                           # one finishes, so the state stays one act while
                           # remaining readable as two.

# ---- boundaries ----
HOLD_IN_S = 0.20
HOLD_OUT_S = 0.20

# ---- LED ----
# ONE swell, on the FIRST cycle only. The light marks the accent, and under
# declination the accent IS cycle 1. Swelling on both would flatten the very
# structure the body is expressing.
#
# It also keeps the two near states apart on the light channel:
#   S2 = monotone climb to a new held level  -> a state being ENTERED
#   S3 = swell and return                    -> an event that has PASSED
# Level change versus transient.
LED_BASE = 3.0
LED_SWELL = 6.0

EASE_MODE = "minjerk"      # "minjerk" | "cosine" -- see generate_s2_listen.py
FPS = 30
SAMPLE_F = 2
# -----------------------------------------------------------

HOLD_IN_F = int(round(HOLD_IN_S * FPS))
HOLD_OUT_F = int(round(HOLD_OUT_S * FPS))
CYCLE_F = int(round(CYCLE_S * FPS))
STRAIGHTEN_AT_F = int(round(STRAIGHTEN_AT_S * FPS))
STRAIGHTEN_F = int(round(STRAIGHTEN_S * FPS))
MOVE_F = max(2 * CYCLE_F, STRAIGHTEN_AT_F + STRAIGHTEN_F)
END_F = HOLD_IN_F + MOVE_F + HOLD_OUT_F

# ---- guard rails, read from the live calibration ----
import os
import sys

REACH_PATH = ""            # set if the .blend lives outside the repo


def _find_up(rel, starts, levels=8):
    """Walk up, and look one step down into each level's subdirectories -- the
    .blend files live in the local design folder while the generators live in
    the repo, which makes them siblings."""
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

# Loaded by PATH, not by `import`: Blender's session caches modules, and a guard
# must not go stale after a re-calibration.
reach = type(sys)("reach")
reach.__file__ = _rp
with open(_rp) as _fh:
    exec(compile(_fh.read(), _rp, "exec"), reach.__dict__)
print("[s3] " + reach.summary())

for _j, _v, _w in (("pan", USER_PAN, "user"),
                   ("tilt", LEAN, "inherited lean"), ("tilt", END_TILT, "straightened"),
                   ("nod", CHIN, "inherited chin"), ("nod", CHIN - CYCLE1_MAG, "cycle 1 bottom"),
                   ("nod", CHIN - CYCLE2_MAG, "cycle 2 bottom"), ("nod", END_NOD, "level")):
    reach.check(_j, _v, _w)
for _j, _d, _w in (("nod", CYCLE1_MAG, "cycle 1"), ("nod", CYCLE2_MAG, "cycle 2"),
                   ("tilt", END_TILT - LEAN, "straighten"), ("nod", CHIN - END_NOD, "settle")):
    reach.check_floor(_j, _d, _w)
_FAC = math.pi / 2.0 if EASE_MODE == "cosine" else 1.875
for _w, _d, _t in (("cycle 1 half", CYCLE1_MAG, CYCLE_S / 2.0),
                   ("cycle 2 half", CYCLE2_MAG, CYCLE_S / 2.0),
                   ("straighten tilt", END_TILT - LEAN, STRAIGHTEN_S),
                   ("straighten nod", CHIN - END_NOD, STRAIGHTEN_S)):
    _pk = abs(_d) / _t * _FAC
    if _pk > 200.0:
        raise RuntimeError(f"{_w} peaks at {_pk:.0f} deg/s under EASE_MODE="
                           f"{EASE_MODE!r}, over the 200 deg/s ceiling.")

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
    led_color.default_value = (0.24, 0.59, 0.90, 1.0)     # cool: still attending
    mat.diffuse_color = (0.24, 0.59, 0.90, 1.0)


def key_led(frame, value):
    if led_strength is not None:
        led_strength.default_value = value
        led_strength.keyframe_insert(data_path="default_value", frame=frame)


def ease(t):
    """0..1 with zero velocity at both ends. minjerk = Flash & Hogan (1985),
    the profile human point-to-point movement has."""
    t = max(0.0, min(1.0, t))
    if EASE_MODE == "cosine":
        return 0.5 * (1.0 - math.cos(math.pi * t))
    return t * t * t * (10.0 + t * (-15.0 + 6.0 * t))


def lerp(a, b, t):
    return a + (b - a) * t


def cycle_dip(i, start_f, mag):
    """How far BELOW the inherited chin the head is, at frame-offset i.

    Down then up, each half eased independently, so the two halves are
    symmetrical -- and symmetry is half of what Hadar et al. identify as the
    affirmative signature. Returns 0 outside the cycle, which is what lets the
    two cycles and the straighten be summed rather than branched between."""
    u = (i - start_f) / float(CYCLE_F)
    if u <= 0.0 or u >= 1.0:
        return 0.0
    if u < 0.5:
        return mag * ease(u * 2.0)              # stroke
    return mag * (1.0 - ease((u - 0.5) * 2.0))  # recovery


for f in range(1, END_F + 1, SAMPLE_F):
    i = (f - 1) - HOLD_IN_F

    # The nod: two dips below a HELD chin. Nothing descends underneath them.
    dip = cycle_dip(i, 0, CYCLE1_MAG) + cycle_dip(i, CYCLE_F, CYCLE2_MAG)

    # The straighten, overlapping the tail of the second cycle.
    t_str = (ease((i - STRAIGHTEN_AT_F) / float(STRAIGHTEN_F))
             if i > STRAIGHTEN_AT_F else 0.0)

    key(pan, "z", f, USER_PAN)
    key(tilt, "x", f, lerp(LEAN, END_TILT, t_str))
    key(nod, "x", f, lerp(CHIN - dip, END_NOD, t_str))

    # One swell, on the first cycle only: under declination the accent is
    # cycle 1, and the light reports the accent rather than every beat.
    swell = cycle_dip(i, 0, 1.0) if i < CYCLE_F else 0.0
    key_led(f, lerp(LED_BASE, LED_SWELL, swell))

# Land exactly on S4's opening pose. A handover, not a loop seam.
key(pan, "z", END_F, USER_PAN)
key(tilt, "x", END_F, END_TILT)
key(nod, "x", END_F, END_NOD)
key_led(END_F, LED_BASE)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = END_F

# states.py fires the 'ack' sound at the BOTTOM OF THE FIRST CYCLE. That fraction
# was measured against a 35-frame clip that no longer exists, so it is recomputed
# here and must be copied across, or the sound will arrive somewhere arbitrary.
SFX_AT = (HOLD_IN_F + CYCLE_F / 2.0) / float(END_F)
pk1 = CYCLE1_MAG / (CYCLE_S / 2.0) * _FAC
msg = (f"S3 ack: {CYCLE1_MAG:.0f} deg + {CYCLE2_MAG:.1f} deg "
       f"(ratio {CYCLE_RATIO:.3f}) at {1.0 / CYCLE_S:.1f} Hz, then straighten "
       f"{LEAN:+.0f}->{END_TILT:+.0f} tilt / {CHIN:+.0f}->{END_NOD:+.0f} nod; "
       f"{END_F}f ({END_F / FPS:.2f}s), peak {pk1:.0f} deg/s")
print(msg)
print(f"[s3] set states.py S3_ACK sfx_at = {SFX_AT:.2f}")


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text="Two cycles: cyclicity is what marks assent "
                           "(Hadar 1985); 0.545 is declination x final")
    self.layout.label(text="lowering (Kimura 2025), not a taste value.")
    self.layout.label(text=f"SET states.py S3_ACK sfx_at = {SFX_AT:.2f}")
    self.layout.label(text="Opens on S2's close; ends on S4's opening pose.")
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(draw, title="S3 ack", icon='INFO')
