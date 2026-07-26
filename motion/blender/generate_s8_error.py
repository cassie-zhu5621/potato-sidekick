# Auto-generates the S8 ERROR loop (3-DOF + LED). Run inside S8_ERROR.blend after
# repair_rig.py + add_nod_joint.py. OVERWRITES all keys.
#
# NOTE: S8 was hand-animated first. The swing below reproduces what was measured
# out of the exported clip -- pan 0 -> -15 -> +10 -> -5 -> 0 over 120 frames, a
# damped oscillation decaying by roughly two thirds each pass -- so the pose
# design survives. What this adds is a nod channel and an LED envelope keyed to
# the same swing.
#
# WHY IT IS NOT A CONFLICT WITH S6. The grammar note says "horizontal shake =
# negation, S6 only", and S8's horizontal excursion is in fact LARGER than S6's
# (25 deg vs 12). They are separated by SPEED, not amplitude: S6 peaks at
# 125 deg/s and reads as a shaken head saying no; S8 peaks at 36 deg/s and reads
# as a slow, searching sway with nowhere to land. Same axis, same amplitude
# order, opposite meaning -- decided by which stroke is quick. (The same rule
# that makes S7b a summons rather than a nod.)
#
# So the two must be kept apart by their timing, and the timing is therefore not
# a free parameter: raising SWING_SPEED far enough turns "I am lost" into "no".

import bpy
import math

# ---- the damped swing (measured) ----
SWING_DEG = 15.0       # first excursion, degrees
DECAY = 0.67           # each pass keeps this fraction. Measured 15 -> 10 -> 5.
SWING_FRAMES = [19, 35, 20, 45]   # frames per pass, also measured: the swing
                                  # slows as it decays, which is what makes it
                                  # read as running out of ideas rather than
                                  # oscillating mechanically
DROOP_TILT = -5.0      # neck sinks while it searches
DROOP_NOD = -8.0       # and the head drops -- "at a loss". NEGATIVE IS DOWN in
                       # Blender for nod, and down is the generous side of the
                       # budget (47 deg available vs 26 up). Set to 0 to keep the
                       # head level if this reads as too dejected.

# ---- LED: amber, accented on every swing extreme, decaying with the motion ----
LED_LO = 0.9
LED_HI = 4.5           # below S7's 8.0: this is a problem being reported, not
                       # an invitation
LED_AMBER = (0.94, 0.55, 0.08, 1.0)

FPS = 30
# -------------------------------------

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


# --- pan: the damped swing, alternating sides ---
f = 1
key(pan, "z", f, 0.0)
key_led(f, LED_LO)
amp, sign = SWING_DEG, -1.0          # measured original goes left first
extremes = []
for i, dur in enumerate(SWING_FRAMES):
    f += dur
    target = 0.0 if i == len(SWING_FRAMES) - 1 else sign * amp
    key(pan, "z", f, target)
    if i < len(SWING_FRAMES) - 1:
        extremes.append((f, amp / SWING_DEG))   # relative effort at this extreme
        amp *= DECAY
        sign = -sign
end = f

# --- tilt / nod: sink early, recover by the seam ---
key(tilt, "x", 1, 0.0)
key(tilt, "x", 1 + SWING_FRAMES[0] + SWING_FRAMES[1] // 2, DROOP_TILT)
key(tilt, "x", end, 0.0)
key(nod, "x", 1, 0.0)
key(nod, "x", 1 + SWING_FRAMES[0] + SWING_FRAMES[1] // 2, DROOP_NOD)
key(nod, "x", end, 0.0)

# --- LED: a beat at each extreme, fading exactly as the swing fades ---
# Tied to the extremes rather than free-running, so the light is visibly the same
# effort as the movement. A free-running blink here would read as a separate
# indicator lamp bolted on, which is the opposite of the intent.
for fr, rel in extremes:
    key_led(max(1, fr - 4), LED_LO)
    key_led(fr, LED_LO + (LED_HI - LED_LO) * rel)
    key_led(fr + 5, LED_LO)
key_led(end, LED_LO)                    # loop-safe: same as frame 1

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = end

peak = 1.6 * SWING_DEG / (SWING_FRAMES[0] / FPS)
warn = ("  !! fast enough to read as S6's negation -- slow it down"
        if peak > 80 else "")
msg = (f"S8 error: damped swing {SWING_DEG:.0f} deg x{DECAY} over {end}f "
       f"({end / FPS:.1f}s loop), droop {DROOP_TILT:.0f}/{DROOP_NOD:.0f}, "
       f"amber LED beat per extreme, est peak {peak:.0f} deg/s{warn}")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text="Must stay SLOWER than S6, or it reads as 'no'")
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(
    draw, title="S8 error" if not warn else "S8 error — TOO FAST", icon='INFO')
