# Auto-generates S6 FINE-TUNE (3-DOF): the "corrected" sequence.
# Run inside S6_finetune.blend after repair_rig.py + add_nod_joint.py.
#
# Startle (tap lands) -> abashed droop ("my bad") -> small clearing shake
# -> perk up with overshoot ("ok, show me"). One-shot, ends attentive-neutral.
# Refs: Cozmo/Vector deflate-perk; Takayama, Dooley & Ju (HRI'11).

import bpy
import math

DROOP_TILT = -8.0
DROOP_NOD = -14.0
SHAKE_DEG = 6.0

# Shake keyframes, shared by the pan channel and the LED. One list, because the
# flash has to land ON the extremes: if the two were written out separately, any
# retiming of the shake would silently leave the light blinking on the old beat.
# (frame, side) -- side +1/-1 alternates, 0 = pass through centre.
SHAKE_KEYS = [(30, 0), (33, -1), (38, +1), (42, -1), (46, +1), (50, 0)]

FPS = 30

LED_COLOR = (1.00, 0.16, 0.12, 1.0)   # RED = negation, "not that one"

# The negation is carried on THREE channels at once, all on the same beat:
# colour (red), motion (fast horizontal shake), and rhythm (a flash per extreme).
# Any one of them can fail -- red/green colour deficiency, a participant looking
# away, peripheral vision that misses a small shake -- and the meaning survives.
# v1 got this wrong in a way that is easy to miss: the LED sat flat at 0.6 for the
# whole shake and then double-blinked during the PERK UP. So the light was
# accenting the recovery, not the refusal, and the brightest moment of the clip
# meant "I'm fine now" while the head was saying "no".
LED_DIM = 0.5          # drooped, abashed
LED_FLASH = 6.0        # on each shake extreme -- this is the "no"
LED_NORMAL = 3.0       # attentive baseline, start and end

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

# --- 1 startle: duck under the tap (f1-f6) ---
key(pan, "z", 1, 0); key(tilt, "x", 1, 0); key(nod, "x", 1, 0)
key_led(1, LED_NORMAL)
key(nod, "x", 4, -10)          # head ducks fast
key(tilt, "x", 5, 3)           # slight recoil back

# --- 2 abashed droop: "...my bad" — held a beat longer (f6-f30) ---
key(tilt, "x", 16, DROOP_TILT)
key(nod, "x", 16, DROOP_NOD)
key_led(8, LED_DIM)            # light deflates too
key(tilt, "x", 30, DROOP_TILT)
key(nod, "x", 30, DROOP_NOD)
key_led(30, LED_DIM)

# --- 3 clearing shake: two full cycles, in the drooped posture (f30-f50) ---
# pan and the LED walk the SAME list, so the flash cannot drift off the beat.
for f, side in SHAKE_KEYS:
    key(pan, "z", f, side * SHAKE_DEG)
    if side:
        key_led(max(1, f - 2), LED_DIM)     # dark between, so each flash reads
        key_led(f, LED_FLASH)               # discrete rather than as a glow
        key_led(f + 2, LED_DIM)
key_led(SHAKE_KEYS[-1][0], LED_DIM)

# --- 4 perk up with overshoot: "ok! show me" (f50-f66) ---
# Deliberately NOT another blink. The refusal was the event; this is settling
# back to attentive, and giving it its own accent would compete with the "no".
key(tilt, "x", 58, 2)          # spring past neutral
key(nod, "x", 58, 6)
key(tilt, "x", 64, 0)
key(nod, "x", 64, 0)
key_led(66, LED_NORMAL)

end = 70
key(pan, "z", end, 0); key(tilt, "x", end, 0); key(nod, "x", end, 0)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = end

n_flash = sum(1 for _, s in SHAKE_KEYS if s)
shake_peak = 1.6 * 2 * SHAKE_DEG / ((SHAKE_KEYS[2][0] - SHAKE_KEYS[1][0]) / FPS)
msg = (f"S6 fine-tune: startle -> droop -> shake x{n_flash} w/ red flash on each "
       f"extreme -> perk, {end}f ({end/FPS:.1f}s), shake peak {shake_peak:.0f} deg/s")
print(msg)
def draw(self, context):
    self.layout.label(text=msg)
bpy.context.window_manager.popup_menu(draw, title="S6 fine-tune", icon='INFO')
