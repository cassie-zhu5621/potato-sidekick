# Auto-generates S7 found (one-shot intro, 3-DOF). Run inside S7a_found.blend
# after repair_rig.py + add_nod_joint.py. Overwrites pan/tilt/nod/LED keys.
#
# Sequence: anticipation crouch -> turn to the user -> NECK EXTENDS FORWARD (the
# craning lean, head counter-tilting to hold eye contact) -> settles into the
# exact start pose of the beckon loop.
#
# v2 (2026-07-26). Two changes, both from the real hardware:
#
# 1. TIMING IS DERIVED FROM A SPEED CEILING, not hardcoded frame counts. v1 put
#    65 deg of pan into 12 frames: 162 deg/s average, 204 deg/s peak, over the
#    200 deg/s authoring ceiling -- and that ceiling assumes 6 V, while the
#    prototype runs 5.9 V off AA cells with a head on the end of the neck.
#    Frame counts are now computed from PEAK_DPS, so changing an angle later
#    cannot silently reintroduce the problem.
#
# 2. END_NOD LIFTS THE HEAD. Same issue as in S4: leaning the neck forward by
#    LEAN_DEG pitches the camera down by roughly the same amount, because the
#    camera rides on the head at the end of the neck. v1 ended at nod 0, so this
#    clip -- whose entire job is to make eye contact and beckon -- finished
#    looking at the user's desk rather than at the user.
#
#    >> END_NOD must equal generate_s7_beckon.py's opening nod. Update both. <<
#
# SIGN CONVENTION, two frames that run opposite:
#   BLENDER, what you author here:   positive nod = head UP
#   SERVO UNITS, what the bus sees:  higher unit  = head DOWN
# INVERT in hardware/calibration.py reconciles them. Always author against the
# render; the servo frame is an implementation detail INVERT absorbs.

import bpy
import math

# ---- keep in sync with generate_s7_beckon.py ----
USER_PAN = 60.0
LEAN_DEG = -12.0
END_NOD = 12.0     # head up at the end so the gaze is level rather than pitched
                   # into the desk. Cancels LEAN_DEG. MUST match beckon's frame 1.
# ---- shape ----
CROUCH = -8.0      # anticipation dip before the turn (tilt)
OVERSHOOT = 4.0    # pan overshoot past the user
COUNTER = 6.0      # head counter-tilt while the neck dives (holds your gaze)

# ---- speed ----
PEAK_DPS = 120.0   # ceiling on PEAK joint speed. The authoring limit is 200,
                   # but that is a no-load figure at 6 V; this is the clip that
                   # sits closest to it, and it is both loaded and under-volted.
                   # Being conservative costs a fraction of a second and buys
                   # the difference between a turn and a lurch.
PEAK_FACTOR = 1.5  # Bezier easing peaks above a segment's average by roughly
                   # this much, so budget the average at PEAK_DPS / PEAK_FACTOR.
FPS = 30

# Reachable Blender angles, derived from hardware/calibration.py LIMITS. Used to
# fail loudly here rather than let the runtime clamp quietly eat the motion.
REACH = {"pan": (-76.4, 70.2), "tilt": (-38.3, 29.2), "nod": (-47.1, 26.2)}
# -------------------------------------------------

LED_COLOR = (0.00, 1.00, 0.16, 1.0)   # GREEN = a result worth your attention

pan = bpy.data.objects["pan_pivot"]
tilt = bpy.data.objects["tilt_pivot"]
nod = bpy.data.objects.get("nod_pivot")
if not nod:
    raise RuntimeError("Run add_nod_joint.py first.")

for obj in (pan, tilt, nod):
    if obj.animation_data and obj.animation_data.action:
        obj.animation_data_clear()


def check(name, deg):
    lo, hi = REACH[name]
    if not (lo <= deg <= hi):
        raise RuntimeError(f"{name} {deg:+.1f} deg is outside what this build "
                           f"can reach ({lo:+.1f}..{hi:+.1f}). Re-author, or "
                           f"re-measure -- do not let LIMITS clamp it silently.")
    return deg


def frames_for(deg):
    """Frames needed to travel `deg` without the peak exceeding PEAK_DPS."""
    return max(4, math.ceil(abs(deg) / (PEAK_DPS / PEAK_FACTOR) * FPS))


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
# Colour set here as well as on the CoreS3 (states.py `hue`), so the render and
# the robot agree. The render is the design reference -- direction and timing get
# judged against renders/statemachine_full_demo.mp4, and a render whose light
# says something different from the hardware quietly invalidates that comparison.
if led_color is not None:
    led_color.default_value = LED_COLOR
    mat.diffuse_color = LED_COLOR


def key_led(frame, value):
    if led_strength is not None:
        led_strength.default_value = value
        led_strength.keyframe_insert(data_path="default_value", frame=frame)


for nm, v in (("pan", USER_PAN + OVERSHOOT), ("tilt", LEAN_DEG),
              ("tilt", CROUCH), ("nod", END_NOD), ("nod", COUNTER)):
    check(nm, v)

turn_deg = USER_PAN + OVERSHOOT
f_crouch = frames_for(CROUCH)
f_turn = frames_for(turn_deg)
f_lean = frames_for(LEAN_DEG - 3)
f_nod = frames_for(END_NOD)

# --- phase 1: anticipation crouch ---
key(pan, "z", 1, 0); key(tilt, "x", 1, 0); key(nod, "x", 1, 0)
f_c = 1 + f_crouch
key(pan, "z", f_c, 0)
key(tilt, "x", f_c, CROUCH)
key(nod, "x", f_c, 3)                        # head tucks slightly opposite
f_c += 2                                      # a beat of stillness before it goes
key(pan, "z", f_c, 0)

# --- phase 2: turn to the user ---
f_t = f_c + f_turn
key(pan, "z", f_t, turn_deg)                 # arrive with overshoot
key(pan, "z", f_t + 5, USER_PAN)             # settle back onto them
key(tilt, "x", f_t, 0)                       # neck returns to neutral in transit
key(nod, "x", f_t, 0)

# --- phase 3: THE LEAN -- neck extends toward you ---
f_l = f_t + 5
key(tilt, "x", f_l, 0)
key(tilt, "x", f_l + f_lean, LEAN_DEG - 3)   # extend with slight overshoot
key(tilt, "x", f_l + f_lean + 6, LEAN_DEG)   # settle into posture
key(nod, "x", f_l, 0)
key(nod, "x", f_l + f_lean, COUNTER)         # head stays on you while neck dives

# --- phase 4: head comes up to level the gaze, then hold ---
f_n = f_l + f_lean + 6
key(nod, "x", f_n + f_nod, END_NOD)
end = f_n + f_nod + 8
key(pan, "z", end, USER_PAN)
key(tilt, "x", end, LEAN_DEG)
key(nod, "x", end, END_NOD)

# LED: flashes through the turn and lean, lands on beckon's baseline
key_led(1, 1.0)
key_led(f_c, 8.0)
key_led(f_t, 1.0)
key_led(f_l, 8.0)
key_led(f_n, 1.0)
key_led(end, 1.0)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = end

worst = max(turn_deg / (f_turn / FPS),
            abs(CROUCH) / (f_crouch / FPS),
            abs(LEAN_DEG - 3) / (f_lean / FPS),
            abs(END_NOD) / (f_nod / FPS)) * PEAK_FACTOR

msg = (f"S7 found v2: crouch -> turn to {USER_PAN:.0f} deg -> lean "
       f"{LEAN_DEG:.0f} deg -> head up {END_NOD:.0f} deg, "
       f"{end}f ({end / FPS:.1f}s), est peak {worst:.0f} deg/s")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text=f"S7b beckon must OPEN at nod {END_NOD:.0f}, not 0")
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(draw, title="S7 found v2", icon='INFO')
