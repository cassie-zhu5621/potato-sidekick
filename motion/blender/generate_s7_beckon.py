# Auto-generates the S7 beckon loop (3-DOF). Run inside S7b.blend after
# repair_rig.py + add_nod_joint.py. Overwrites pan/tilt/nod/LED keyframes.
#
# v2 (2026-07-26). The gesture was inverted and the handover was broken:
#
# 1. IT WAS A DOWNWARD PECK. v1 drove nod to -15, and in the Blender rig
#    negative nod is head-DOWN, so the "beckon" was pecking at the floor. A head
#    dropping toward you is a nod of assent, not a summons.
#
#    The fix is not just flipping the sign. WHICH STROKE IS FAST is what decides
#    how the gesture reads: between the same two positions, a quick stroke up
#    with a slow return says "come here", while a quick stroke down with a slow
#    return says "yes". The eye assigns the meaning to the accented stroke. So
#    the rise is short and sharp, there is a beat of hang at the top, and the
#    return is more than twice as long and eased.
#
# 2. IT OPENED AT nod 0, but S7a now ends at BASE_NOD (head lifted so the gaze
#    clears the forward lean). Opening at 0 dropped the head the instant the
#    loop started. The base pose here must equal S7a's last frame.
#
# The toss is shared between nod and tilt: the head flicks up while the neck
# straightens slightly under it. That reads as the whole creature lifting rather
# than a head hinging, and it keeps nod clear of its ceiling -- nod has only
# 26 deg of UP travel on this build and BASE_NOD already spends 12 of it.
#
# Loop-safe: every channel starts and ends on the base pose.

import bpy
import math

# ---- keep in sync with generate_s7_found.py ----
USER_PAN = 60.0     # user direction (template; firmware retargets at runtime)
LEAN_DEG = -12.0    # neck forward-lean posture
BASE_NOD = 12.0     # = S7a END_NOD. Head up so the gaze clears the lean.
# ---- the toss ----
THROW_NOD = 10.0    # head flicks UP this far (positive = up in Blender)
THROW_TILT = 8.0    # neck straightens up under it, same direction
# ---- rhythm: the asymmetry IS the gesture ----
RISE_F = 5          # sharp. This is the stroke that carries the meaning.
TOP_F = 5           # hang at the top -- the "well? come on" beat
FALL_F = 11         # slow, eased return. Must be clearly longer than RISE_F.
REST_F = 12         # stillness before asking again
CYCLES = 2          # tosses per loop; any number loops cleanly

FPS = 30

# Reachable Blender angles, from hardware/calibration.py LIMITS.
REACH = {"pan": (-76.4, 70.2), "tilt": (-38.3, 29.2), "nod": (-47.1, 26.2)}
# ------------------------------------------------

LED_COLOR = (0.00, 1.00, 0.16, 1.0)   # GREEN, same as S7a -- one event, one colour

pan = bpy.data.objects["pan_pivot"]
tilt = bpy.data.objects["tilt_pivot"]
nod = bpy.data.objects.get("nod_pivot")
if not nod:
    raise RuntimeError("Run add_nod_joint.py first — this clip needs the 3rd DOF.")

for obj in (pan, tilt, nod):
    if obj.animation_data and obj.animation_data.action:
        obj.animation_data_clear()


def check(name, deg):
    lo, hi = REACH[name]
    if not (lo <= deg <= hi):
        raise RuntimeError(f"{name} {deg:+.1f} deg is outside what this build "
                           f"can reach ({lo:+.1f}..{hi:+.1f}). Reduce THROW_* or "
                           f"BASE_NOD -- do not let LIMITS clamp it silently.")
    return deg


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


top_nod = check("nod", BASE_NOD + THROW_NOD)
top_tilt = check("tilt", LEAN_DEG + THROW_TILT)
check("pan", USER_PAN)
check("nod", BASE_NOD)
check("tilt", LEAN_DEG)
if FALL_F <= RISE_F:
    raise RuntimeError("FALL_F must be longer than RISE_F, or the accent lands "
                       "on the way down and this reads as a nod, not a beckon.")

cycle_f = RISE_F + TOP_F + FALL_F + REST_F
end = 1 + CYCLES * cycle_f

# pan stays locked on the user for the whole loop
key(pan, "z", 1, USER_PAN)
key(pan, "z", end, USER_PAN)

for i in range(CYCLES):
    f0 = 1 + i * cycle_f
    f_up = f0 + RISE_F
    f_hang = f_up + TOP_F
    f_down = f_hang + FALL_F
    f_next = f0 + cycle_f

    key(nod, "x", f0, BASE_NOD)
    key(tilt, "x", f0, LEAN_DEG)

    key(nod, "x", f_up, top_nod)             # the accent
    key(tilt, "x", f_up, top_tilt)

    key(nod, "x", f_hang, top_nod)           # hold the question open
    key(tilt, "x", f_hang, top_tilt)

    key(nod, "x", f_down, BASE_NOD)          # eased, unhurried return
    key(tilt, "x", f_down, LEAN_DEG)

    key(nod, "x", f_next, BASE_NOD)          # rest
    key(tilt, "x", f_next, LEAN_DEG)

    # light peaks at the TOP of the toss, with the accent -- not at the bottom
    key_led(f0, 1.0)
    key_led(f_up, 8.0)
    key_led(f_hang, 6.0)
    key_led(f_down, 1.0)
    key_led(f_next, 1.0)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = end

peak = 1.5 * max(THROW_NOD, THROW_TILT) / (RISE_F / FPS)
msg = (f"S7 beckon v2: base {USER_PAN:.0f}/{LEAN_DEG:.0f}/{BASE_NOD:.0f}, "
       f"toss UP +{THROW_NOD:.0f} nod +{THROW_TILT:.0f} tilt, "
       f"rise {RISE_F}f vs fall {FALL_F}f, {CYCLES}x{cycle_f}f "
       f"= {end}f ({end / FPS:.1f}s), est peak {peak:.0f} deg/s")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text=f"Opens at nod {BASE_NOD:.0f} = S7a's last frame")
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(draw, title="S7 beckon v2", icon='INFO')
