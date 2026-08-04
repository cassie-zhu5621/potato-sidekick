# PREVIEW ONLY -- NOT A SHIPPED CLIP. Run inside S5B_TRACK.blend (or a copy) to
# look at the slow settling drift; then re-run generate_s5b_track.py to put the
# real loop back.
# Design rationale: ../../../robot_motion/S4_S5_DESIGN.md (local, not in this repo).
#
# WHY THIS IS A PREVIEW AND NOT A CLIP
#
#   The drift spans a whole watch -- up to REPLAN_PERIOD_S, five minutes -- and
#   S5b is a 57.6 s LOOP. A drift cannot live in a loop; it would snap back every
#   time round. So on the robot it belongs to clip_player, as a slow offset added
#   to the held pose from however long S5b has been running, exactly like the pan
#   retargeting. This file only bakes it out so it can be looked at.
#
# THE IDEA, AND WHY IT ESCAPES THE WALL THAT KILLED THE OTHER THREE
#
#   Three attempts at ambient motion all failed between two measured bounds --
#   slow enough to be unobtrusive means under the 8.8 deg/s smoothness floor, and
#   smooth enough to clear it means over the 5.4 deg/s salience ceiling. Every
#   one of them assumed motion happens on the scale of SECONDS.
#
#   This one is two orders of magnitude slower:
#
#     breath sway      ~3    deg/s   stutters
#     re-fixation      ~25   deg/s   startles
#     THIS DRIFT        0.027 deg/s  is never perceived as motion at all
#
#   At 0.027 deg/s the joint advances one servo unit every ~11 s, and one unit
#   moves the head 0.65 mm. It cannot read as an event and it cannot read as a
#   stutter, because it is below the threshold at which motion is seen as motion.
#   What it produces instead is a STATE DIFFERENCE BETWEEN GLANCES -- which is
#   what an ambient object is actually observed by. A clock's hour hand is the
#   reference case.
#
#   Hallnas & Redstrom (2001), "Slow Technology -- Designing for Reflection",
#   Personal and Ubiquitous Computing 5, 201-212: as computing becomes ambient,
#   some of it stops being a tool used in a moment and becomes "continuously
#   present as part of a designed environment", and should express itself over
#   long durations rather than in moments. That is the design tradition this sits
#   in, and the argument for looking at the minute scale at all.
#
# WHAT IT MEANS -- IT STILL NEEDS A REFERENT
#
#   "How long I have been on this." That is a real fact and one the person cannot
#   otherwise see. The drift is LINEAR for exactly that reason: a constant rate
#   makes the pose a clock, and the angle reads elapsed time proportionally. An
#   eased drift would look nicer and would lie about the rate.
#
#   The next re-plan resets it, and the reset is invisible because it happens
#   inside S4's sweep. So the range is bounded by the re-plan period, not chosen.
#
#   A glance therefore gets THREE facts instead of two: attending (craned),
#   attending THERE (aimed), and been at it a while (how deep).
#
# THE RISK, WHICH IS REAL
#
#   A deepening lean is semantically ambiguous: settling in, or tiring. Only
#   sitting next to it for an hour decides that, and it is the one thing here
#   that can actually be wrong.

import bpy
import math

# ---- the held pose (must match generate_s5b_track.py) ----
HOLD_PAN = 25.0
HOLD_TILT = -12.0
HOLD_NOD = 12.0        # exactly cancels the lean: gaze level

# ---- the drift ----
DRIFT_DEG = 8.0        # how much deeper the lean gets over a full watch.
                       # tilt -12 -> -20, with nod +12 -> +20 tracking it, so
                       # the gaze stays at 0 the whole way.
WATCH_S = 300.0        # = states.py REPLAN_PERIOD_S. The drift is bounded by the
                       # re-plan period rather than by a chosen endpoint.

# ---- preview controls ----
TIME_SCALE = 20.0      # 20.0 -> 15 s, for checking the trajectory and the gaze.
                       # 1.0  -> 300 s / 9000 frames, for checking that it is
                       #         genuinely imperceptible. Watch that one WITHOUT
                       #         looking at it: do something else and glance.
BREATH_S = 3.6         # the LED breath carries on underneath, unchanged
LED_LO = 0.8
LED_HI = 3.0
LED_COOL = (0.38, 0.72, 1.00, 1.0)

FPS = 30
SAMPLE_F = 2
# -----------------------------------------------------------

END_F = max(2, int(round(WATCH_S / TIME_SCALE * FPS)))
RATE = DRIFT_DEG / WATCH_S                      # deg/s, real time

# ---- guard rails, read from the live calibration ----
import os
import sys

REACH_PATH = ""


def _find_up(rel, starts, levels=8):
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
    raise RuntimeError("Cannot find motion/blender/reach.py -- see "
                       "generate_s2_listen.py. bpy.data.filepath="
                       + repr(bpy.data.filepath))
reach = type(sys)("reach")
reach.__file__ = _rp
with open(_rp) as _fh:
    exec(compile(_fh.read(), _rp, "exec"), reach.__dict__)
print("[preview] " + reach.summary())

END_TILT = HOLD_TILT - DRIFT_DEG
END_NOD = HOLD_NOD + DRIFT_DEG
if abs(END_TILT + END_NOD) > 0.01:
    raise RuntimeError("the drift does not hold the gaze -- tilt and nod must "
                       "move by equal and opposite amounts.")
for _j, _v, _w in (("pan", HOLD_PAN, "target"),
                   ("tilt", HOLD_TILT, "start"), ("tilt", END_TILT, "deepest"),
                   ("nod", HOLD_NOD, "start"), ("nod", END_NOD, "deepest")):
    reach.check(_j, _v, _w)

NECK_MM = (0.111 + 0.035 * 0.5) * 1000.0
UPD = reach.UNITS_PER_DEG

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
    led_color.default_value = LED_COOL
    mat.diffuse_color = LED_COOL


def key_led(frame, value):
    if led_strength is not None:
        led_strength.default_value = value
        led_strength.keyframe_insert(data_path="default_value", frame=frame)


# LINEAR. The pose is a clock; easing it would look nicer and misreport the rate.
breath_f = max(2, BREATH_S / TIME_SCALE * FPS)
for f in range(1, END_F + 1, SAMPLE_F):
    u = (f - 1) / float(END_F - 1)
    key(pan, "z", f, HOLD_PAN)
    key(tilt, "x", f, HOLD_TILT - DRIFT_DEG * u)
    key(nod, "x", f, HOLD_NOD + DRIFT_DEG * u)
    phase = ((f - 1) % breath_f) / breath_f
    key_led(f, LED_LO + (LED_HI - LED_LO) * 0.5 * (1.0 - math.cos(2.0 * math.pi * phase)))

key(pan, "z", END_F, HOLD_PAN)
key(tilt, "x", END_F, END_TILT)
key(nod, "x", END_F, END_NOD)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = END_F

step_s = 1.0 / (RATE * UPD)
lines = ["PREVIEW ONLY -- re-run generate_s5b_track.py afterwards",
         f"drift {DRIFT_DEG:.0f} deg over {WATCH_S:.0f} s = {RATE:.3f} deg/s",
         f"one servo unit every {step_s:.0f} s; one unit moves the head "
         f"{NECK_MM * math.radians(1.0 / UPD):.2f} mm",
         f"TIME_SCALE {TIME_SCALE:.0f}x -> {END_F} f ({END_F / FPS:.1f} s to watch)"]
print("\n".join("[preview] " + s for s in lines))
print("\n[preview] what two glances N seconds apart would differ by:")
for gap in (30, 60, 120, 300):
    d = min(DRIFT_DEG, RATE * gap)
    mm = NECK_MM * 2.0 * math.sin(math.radians(d) / 2.0)
    print(f"[preview]   {gap:3d} s apart -> {d:4.1f} deg, head moved {mm:5.1f} mm")


def draw(self, context):
    for s in lines:
        self.layout.label(text=s)
    self.layout.label(text="Watch at 20x for the trajectory, at 1x WITHOUT")
    self.layout.label(text="looking -- do something else and glance twice.")
    self.layout.label(text="Risk: does a deepening lean read as settling in,")
    self.layout.label(text="or as tiring? That is the one thing that can be wrong.")


bpy.context.window_manager.popup_menu(draw, title="S5b drift PREVIEW", icon='INFO')
