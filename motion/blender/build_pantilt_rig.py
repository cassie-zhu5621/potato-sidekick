# Run inside Blender (Scripting tab). Builds a minimal pan-tilt sidekick rig.
# Only pan_pivot (Z) and tilt_pivot (X) are animatable — matching the real 2-DOF hardware.

import bpy
import math

# ---- tune these to match the real robot ----
# Feetech SC-series bus servo: 300 deg total travel (0-300, center 150).
# Rig uses 0 = center, so pan can reach +/-150. Tilt is limited by the head
# design, not the motor — set to whatever the mechanics allow.
PAN_LIMIT_DEG = 150      # pan: +/- this (motor max: 150)
TILT_MIN_DEG = -45       # tilt down limit (mechanical, adjust to your build)
TILT_MAX_DEG = 60        # tilt up limit (mechanical, adjust to your build)
BASE_H = 0.04            # meters (rough footprint ~ desk gadget scale)
NECK_H = 0.05
HEAD_R = 0.035
# --------------------------------------------


def make_collection(name):
    if name in bpy.data.collections:
        col = bpy.data.collections[name]
    else:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


def link_to(col, obj):
    for c in obj.users_collection:
        c.objects.unlink(obj)
    col.objects.link(obj)


col = make_collection("PanTiltBot")

# base
bpy.ops.mesh.primitive_cylinder_add(radius=0.05, depth=BASE_H, location=(0, 0, BASE_H / 2))
base = bpy.context.object
base.name = "base"
link_to(col, base)

# pan pivot (empty) on top of base
bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0, 0, BASE_H))
pan = bpy.context.object
pan.name = "pan_pivot"
pan.empty_display_size = 0.03
pan.parent = base
pan.rotation_mode = 'XYZ'
pan.lock_rotation = (True, True, False)          # only Z free
pan.lock_location = (True, True, True)
c = pan.constraints.new('LIMIT_ROTATION')
c.use_limit_z = True
c.min_z = math.radians(-PAN_LIMIT_DEG)
c.max_z = math.radians(PAN_LIMIT_DEG)
c.owner_space = 'LOCAL'
link_to(col, pan)

# neck
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, NECK_H / 2))
neck = bpy.context.object
neck.name = "neck"
neck.scale = (0.015, 0.015, NECK_H / 2)
neck.parent = pan
link_to(col, neck)

# tilt pivot (empty) at top of neck
bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0, 0, BASE_H + NECK_H))
tilt = bpy.context.object
tilt.name = "tilt_pivot"
tilt.empty_display_size = 0.03
tilt.parent = pan
tilt.rotation_mode = 'XYZ'
tilt.lock_rotation = (False, True, True)         # only X free
tilt.lock_location = (True, True, True)
c = tilt.constraints.new('LIMIT_ROTATION')
c.use_limit_x = True
c.min_x = math.radians(TILT_MIN_DEG)
c.max_x = math.radians(TILT_MAX_DEG)
c.owner_space = 'LOCAL'
link_to(col, tilt)

# head
bpy.ops.mesh.primitive_uv_sphere_add(radius=HEAD_R, location=(0, 0, BASE_H + NECK_H + HEAD_R * 0.6))
head = bpy.context.object
head.name = "head"
head.parent = tilt
link_to(col, head)

# single eye (camera lens), on +Y face of head — robot "faces" +Y
bpy.ops.mesh.primitive_cylinder_add(
    radius=0.012, depth=0.01,
    location=(0, HEAD_R * 0.95, BASE_H + NECK_H + HEAD_R * 0.6),
    rotation=(math.radians(90), 0, 0))
eye = bpy.context.object
eye.name = "eye"
eye.parent = tilt
link_to(col, eye)

# LED antenna — emissive; keyframe material color/strength to preview LED states
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.008, location=(0, 0, BASE_H + NECK_H + HEAD_R * 1.6))
led = bpy.context.object
led.name = "led_antenna"
led.parent = tilt
mat = bpy.data.materials.new("led_mat")
mat.use_nodes = True
nodes = mat.node_tree.nodes
nodes.clear()
em = nodes.new('ShaderNodeEmission')
em.inputs['Color'].default_value = (1.0, 0.6, 0.2, 1.0)   # warm
em.inputs['Strength'].default_value = 3.0
out = nodes.new('ShaderNodeOutputMaterial')
mat.node_tree.links.new(em.outputs['Emission'], out.inputs['Surface'])
led.data.materials.append(mat)
link_to(col, led)

bpy.context.scene.render.fps = 30
print("Rig built. Animate ONLY pan_pivot rotation Z and tilt_pivot rotation X.")
