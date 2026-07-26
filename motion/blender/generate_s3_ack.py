# Auto-generates S3 ACKNOWLEDGE (3-DOF). Run inside S3_ack.blend
# after repair_rig.py + add_nod_joint.py. Overwrites pan/tilt/nod/LED keys.
#
# Starts from S2's listening end pose (leaning in, chin up at the user):
# one decisive nod from the raised chin, then the body straightens up
# ("on it") — flows into the S4 sweep.
# Keep USER_PAN / LEAN / CHIN in sync with your S2 clip's end pose.

import bpy
import math

USER_PAN = 60.0
LEAN = -6.0     # S2 end: neck leaning toward user
CHIN = 10.0     # S2 end: chin up
NOD_DIP = -12.0    # decisive dip depth
FPS = 30

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
mat = bpy.data.materials.get("led_mat")
if mat and mat.use_nodes:
    if mat.node_tree.animation_data:
        mat.node_tree.animation_data_clear()
    for n in mat.node_tree.nodes:
        if n.type == 'EMISSION':
            led_strength = n.inputs['Strength']
            break

def key_led(frame, value):
    if led_strength is not None:
        led_strength.default_value = value
        led_strength.keyframe_insert(data_path="default_value", frame=frame)

# start = S2 listening end pose
key(pan, "z", 1, USER_PAN)
key(tilt, "x", 1, LEAN)
key(nod, "x", 1, CHIN)
key_led(1, 3.0)

# decisive nod from the raised chin (big travel reads confident)
key(nod, "x", 8, NOD_DIP)
key_led(8, 6.0)                 # pulse at the bottom of the nod
key(nod, "x", 14, 4)            # overshoot up
key_led(14, 3.0)
key(nod, "x", 20, 0)

# body straightens: "message received, getting to work"
key(tilt, "x", 14, LEAN)        # hold through the nod
key(tilt, "x", 24, 0)

end = 35
key(pan, "z", end, USER_PAN)
key(tilt, "x", end, 0)
key(nod, "x", end, 0)
key_led(end, 3.0)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = end

msg = f"S3 ack: decisive nod from listening pose + straighten, {end}f ({end/FPS:.1f}s)"
print(msg)
def draw(self, context):
    self.layout.label(text=msg)
bpy.context.window_manager.popup_menu(draw, title="S3 ack", icon='INFO')
