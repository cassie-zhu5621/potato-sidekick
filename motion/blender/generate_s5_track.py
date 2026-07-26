# Auto-generates the S5 TRACKING clip — 3-DOF. Run inside S5_TRACK.blend after
# repair_rig.py + add_nod_joint.py. Overwrites all keys.
#
# S5 is a LOOP clip and it is deliberately motionless: the head holds the pose
# S4 handed it and the only thing alive is a slow cool-white breath on the LED.
#
# The division of labour: LIGHT carries state, MOTION is reserved for events.
# The cool breathing LED is what says "watching, not crashed", so stillness here
# costs nothing in legibility. What it buys is S7: FOUND has to read as
# "something just happened", and an onset only reads that way against a
# background of stillness. A robot that stirs during S5 spends that contrast on
# nothing, and blurs tracking into finding. Scarcity is what makes the motion
# vocabulary mean anything.
#
# (Tested both ways on the real robot before deciding — see MICRO_BREATH_DEG.)
#
# HANDOVER: the pose below MUST equal the end of generate_s4_sweep.py
# (RICHEST_DEG / LEAN_TILT / LEAN_NOD). If you retune S4's lock, retune here or
# the robot will jump at the S4->S5 boundary.

import bpy
import math

# ---- pose: keep in sync with generate_s4_sweep.py ----
HOLD_PAN = 25.0        # = S4 RICHEST_DEG
HOLD_TILT = -12.0      # = S4 LEAN_TILT   (neck craned forward at the target)
HOLD_NOD = 15.0        # = S4 LEAN_NOD (head lifted; in BLENDER positive = UP.
                       # On the bus a higher unit is DOWN, which is what
                       # INVERT["nod"]=True reconciles — author against the
                       # render, not the servo frame.)
                       # The -12° neck lean pitches the camera down by about as
                       # much, so a level gaze needs the head brought back up.
                       # The lift itself happens at the END OF S4, not here: S5
                       # is a loop, and a movement inside a loop would repeat
                       # every 7.2 s. S5 only holds what S4 arrived at.

# ---- breath ----
BREATH_S = 3.6         # one full breath. Slower than a resting human (~4 s) so
                       # it reads as calm rather than expectant.
N_CYCLES = 2           # any whole number loops seamlessly; 1 is the minimal
                       # correct unit if you want the smallest CSV
LED_LO = 0.8           # trough. Not 0 -- it should never look switched off
LED_HI = 3.0           # crest. Matches S4's steady-on level so the handover
                       # into S5 has no brightness step
LED_COOL = (0.38, 0.72, 1.00, 1.0)   # cool white-blue, vs the warm (1.0,.62,.22)

MICRO_BREATH_DEG = 0.0 # DELIBERATELY ZERO -- this is a decision, not a TODO.
                       #
                       # A neck micro-breath was prototyped and rejected on two
                       # grounds, one design and one hardware:
                       #
                       # DESIGN (the deciding one). The cool breathing LED
                       # already says "watching, not crashed", so motion is not
                       # needed to prove aliveness. Spending motion here costs
                       # something: S7 FOUND is the moment that has to read as
                       # "something happened", and it can only read that way
                       # against a background of stillness. If S5 stirs, S7's
                       # onset is just more of the same. Keeping motion scarce
                       # is what gives it meaning. Light carries state; movement
                       # is reserved for events.
                       #
                       # HARDWARE (why it would have been awkward anyway).
                       # Measured on the real robot with hardware/breath_test.py:
                       # servo resolution is 1 unit = 300/1023 = 0.293 deg, so a
                       # 12-unit (3.5 deg) breath is exactly 12 discrete steps.
                       # Under 8 units nothing visibly moved at all; at 12 units
                       # over 3.6 s the steps land 150 ms apart and tick
                       # visibly. Slow AND smooth is unreachable at small
                       # amplitude: the step count is set by amplitude, so
                       # stretching the duration only spreads the same steps
                       # further apart. Smooth would have required either a
                       # larger movement or a faster one -- and both of those
                       # are exactly what S5 must not do.
                       #
                       # If you ever revisit this, hardware/breath_test.py
                       # --split shares the arc across tilt and nod so their
                       # steps interleave; that was the only way found to buy
                       # smoothness without moving further or faster.

SAMPLE_F = 3           # breath is keyed as sampled cosine, not two extremes, so
                       # the curve is a real breath rather than Bezier's guess
FPS = 30
# ------------------------------------------------------

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
    mat.diffuse_color = LED_COOL          # so Solid view shows it cool too


def key_led(frame, value):
    if led_strength is not None:
        led_strength.default_value = value
        led_strength.keyframe_insert(data_path="default_value", frame=frame)


cycle_f = int(round(BREATH_S * FPS))
end = 1 + N_CYCLES * cycle_f

# Pose: keyed at both ends only. Nothing moves, so the exporter samples a
# constant -- which is exactly what should reach the servos.
for f in (1, end):
    key(pan, "z", f, HOLD_PAN)
    key(nod, "x", f, HOLD_NOD)

# Breath, sampled from a raised cosine. Starting and ending at the trough means
# value AND slope match across the loop seam, so --loop has no visible hitch.
for f in range(1, end + 1, SAMPLE_F):
    phase = ((f - 1) % cycle_f) / cycle_f
    breath = 0.5 * (1.0 - math.cos(2.0 * math.pi * phase))     # 0 -> 1 -> 0
    key_led(f, LED_LO + (LED_HI - LED_LO) * breath)
    key(tilt, "x", f, HOLD_TILT - MICRO_BREATH_DEG * breath)
key_led(end, LED_LO)
key(tilt, "x", end, HOLD_TILT)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = end

peak = (math.pi * MICRO_BREATH_DEG) / BREATH_S if MICRO_BREATH_DEG else 0.0
motion = "STILL" if not MICRO_BREATH_DEG else f"neck {MICRO_BREATH_DEG:.1f}°"
msg = (f"S5: hold {HOLD_PAN:.0f}°/{HOLD_TILT:.0f}°/{HOLD_NOD:.0f}° {motion}, "
       f"cool LED breath {BREATH_S:.1f}s x{N_CYCLES}, "
       f"{end}f ({end / FPS:.1f}s), peak {peak:.1f} deg/s")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text="Loop clip: starts and ends at the breath trough")
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(draw, title="S5 track v2", icon='INFO')
