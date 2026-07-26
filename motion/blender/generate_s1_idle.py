# Auto-generates the S1 IDLE loop (3-DOF + LED). Run inside S1_IDLE.blend after
# repair_rig.py + add_nod_joint.py. OVERWRITES all keys.
#
# NOTE: S1 was hand-animated first. The motion below reproduces what was measured
# out of the exported clip -- pan 0 -> +5 -> 0 and tilt 0 -> +3 -> 0 across 150
# frames, peaking near the middle -- so running this does not discard the pose
# design. What it adds is the two channels the hand animation never had: a nod
# key, and an LED envelope tied to the same cycle.
#
# Why S1 needed a generator at all: with no LED keys, export_clip.py writes the
# material's static Strength on every frame. A constant column is worse than an
# absent one -- the player streams it, the CoreS3 never times out back to its own
# breathing, and the resting state ends up a flat glow. Idle is exactly the state
# where "alive but not attending" has to read.
#
# S1's job in the grammar: present, not attending. It is the floor everything
# else is measured against, so it must not compete with anything -- no accents,
# no gesture, nothing that could be mistaken for a claim.

import bpy
import math

# ---- motion (measured from the hand-animated original) ----
SWAY_PAN = 5.0         # a slow lean, not a look. Below ~2 deg it stops reading
SWAY_TILT = 3.0        # at all on this build (1 unit = 0.293 deg)
IDLE_NOD = 0.0         # head stays level. Deliberate: the sway and the light are
                       # already carrying "alive", and a third channel moving
                       # would start to look like intent. Same reasoning as S5.
CYCLE_F = 150          # 5.0 s at 30 fps. One full out-and-back = one loop.

# ---- LED: warm, synced to the sway, deliberately low contrast ----
LED_LO = 0.8           # never off -- off reads as powered down
LED_HI = 2.5           # vs 8.0 for S7's accents. Idle must not draw the eye.
LED_WARM = (1.00, 0.62, 0.22, 1.0)

SAMPLE_F = 3           # LED keyed as a sampled cosine, not two extremes
FPS = 30
# -----------------------------------------------------------

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


end = CYCLE_F

# One raised cosine on every channel, so motion and light share a phase: the lean
# and the glow swell together and the robot reads as one thing breathing rather
# than a light and a servo that happen to be in the same room.
for f in range(1, end + 1, SAMPLE_F):
    phase = (f - 1) / float(CYCLE_F)
    s = 0.5 * (1.0 - math.cos(2.0 * math.pi * phase))     # 0 -> 1 -> 0
    key(pan, "z", f, SWAY_PAN * s)
    key(tilt, "x", f, SWAY_TILT * s)
    key_led(f, LED_LO + (LED_HI - LED_LO) * s)

# Land every channel exactly back on zero: this clip is played on repeat, and a
# one-unit mismatch at the seam becomes a visible tick every 5 seconds.
key(pan, "z", end, 0.0)
key(tilt, "x", end, 0.0)
key_led(end, LED_LO)
key(nod, "x", 1, IDLE_NOD)
key(nod, "x", end, IDLE_NOD)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = end

peak = 2.0 * math.pi * max(SWAY_PAN, SWAY_TILT) / (CYCLE_F / FPS) / 2
msg = (f"S1 idle: sway pan {SWAY_PAN:.0f} / tilt {SWAY_TILT:.0f}, nod "
       f"{IDLE_NOD:.0f}, warm LED {LED_LO}-{LED_HI} in phase, "
       f"{end}f ({end / FPS:.1f}s loop), peak {peak:.1f} deg/s")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text="Loop clip: all channels start and end at rest")
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(draw, title="S1 idle", icon='INFO')
