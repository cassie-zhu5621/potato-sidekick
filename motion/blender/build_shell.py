# Shell v5 — compact white-industrial, mechanically correct pan-tilt.
# Run AFTER repair_rig.py. Re-runnable.
#
# Mechanism (matches the v1 prototype):
#   pan servo: body FIXED inside the base, shaft up -> turns everything above.
#   tilt servo: body rides on the panning neck (pans, never tilts),
#               horizontal shaft -> tilts the head.
# So: servo_pan is static, servo_tilt is parented to pan_pivot, and only
# the stalk/head are parented to tilt_pivot. Shafts + horn are drawn so the
# drive chain is readable.

import bpy
import math
from mathutils import Matrix

# ---- SCS0009 (m) ----
SERVO_W = 0.0235   # long side
SERVO_D = 0.0118   # thin side
SERVO_H = 0.0242   # height incl. shaft face
# ---- camera: Waveshare OV2735, FOV 96 (datasheet dims) ----
CAM_BOARD = 0.025     # 25 x 25 mm PCB (sets minimum head diameter)
CAM_R = 0.007        # lens barrel Ø14mm -> radius 7mm
CAM_LEN = 0.016        # barrel protrudes ~16mm from the board face
# ---- form ----
# --- neck as signal gain, matched to proxemic placement zone (Hall 1966) ---
# swappable printed segments; pick one at placement. 1:2:3 = personal:social:public.
NECK_PRESETS = {"short": 0.04, "mid": 0.08, "long": 0.12}
NECK_PRESET = "mid"           # <-- change to "short" / "mid" / "long" and rerun
STALK_LEN = NECK_PRESETS[NECK_PRESET]
STALK_R = 0.0025             # thin frosted sleeve radius (OD 5mm) — just wraps wires
BASE_R = 0.045
BASE_TH = 0.030    # deep enough to swallow the pan servo
NECK_H = 0.018    # must match repair_rig (tilt axis just above the base)
HUB_R = 0.0155   # joint hub at the tilt joint — houses the tilt servo
CLEAR = 0.003
WALL = 0.002
# ---------------------

col = bpy.data.collections.get("PanTiltBot")
pan = bpy.data.objects.get("pan_pivot")
tilt = bpy.data.objects.get("tilt_pivot")
nod = bpy.data.objects.get("nod_pivot")   # 3-DOF upgrade (add_nod_joint.py)
head_parent = nod if nod else tilt
if not all([col, pan, tilt]):
    raise RuntimeError("Run build_pantilt_rig.py + repair_rig.py first.")

# keep the nod joint at the top of the chosen neck length (rotation keys unaffected)
if nod:
    nod.location = (0, 0, STALK_LEN)

for pname in ["head", "eye", "neck", "base"]:
    o = bpy.data.objects.get(pname)
    if o:
        o.hide_set(True)
        o.hide_render = True      # hide_set alone still renders!
led = bpy.data.objects.get("led_antenna")

for name in ["shell_base", "shell_neck", "shell_hub", "shell_stalk", "shell_head",
             "head_cap", "eye_barrel", "eye_lens", "servo_pan", "servo_tilt",
             "shaft_pan", "shaft_tilt", "horn_tilt"]:
    old = bpy.data.objects.get(name)
    if old:
        bpy.data.objects.remove(old, do_unlink=True)

def make_mat(name, color, alpha=1.0, metallic=0.0, rough=0.6, sss=0.0):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = rough
        bsdf.inputs["Alpha"].default_value = alpha
        if sss > 0:
            try:
                bsdf.inputs["Subsurface Weight"].default_value = sss
            except Exception:
                pass
    m.diffuse_color = (*color[:3], alpha)
    try:
        m.blend_method = 'BLEND'
    except Exception:
        pass
    return m

print_white = make_mat("print_white", (0.87, 0.86, 0.83, 1), rough=0.65, sss=0.03)
frosted = make_mat("frosted", (0.95, 0.95, 0.96, 1), alpha=0.55, rough=0.3)
charcoal = make_mat("charcoal", (0.06, 0.06, 0.065, 1), rough=0.45)
teal = make_mat("servo_teal", (0.0, 0.55, 0.55, 1))
lens_m = make_mat("lens_black", (0.01, 0.01, 0.02, 1), rough=0.1)

def add(mesh_op, name, parent, mat, loc, rot=(0, 0, 0), scale=None,
        bevel=True, solidify=False, **kw):
    mesh_op(**kw)
    o = bpy.context.object
    o.name = name
    o.parent = parent
    o.matrix_parent_inverse = Matrix.Identity(4)
    o.location = loc
    o.rotation_euler = rot
    if scale:
        o.scale = scale
    for c in o.users_collection:
        c.objects.unlink(o)
    col.objects.link(o)
    o.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    if bevel:
        bv = o.modifiers.new("chamfer", 'BEVEL')
        bv.width = 0.0012
        bv.segments = 2
    if solidify:
        sd = o.modifiers.new("walls", 'SOLIDIFY')
        sd.thickness = WALL
    return o

head_r = CAM_BOARD / 2 + CLEAR + WALL + 0.003   # front wide enough for the 25mm board
head_len = 0.040                                 # longer body (deep enough to sink the lens)
head_lz = STALK_LEN + head_r * 0.5           # tilt-local
LENS_PROUD = 0.005                               # how far the lens tip sticks out of the hole

# ============ static parts (world coords) ============
# base puck — pan servo fully inside
add(bpy.ops.mesh.primitive_cylinder_add, "shell_base", None, print_white,
    (0, 0, BASE_TH / 2), solidify=True, radius=BASE_R, depth=BASE_TH)

# pan servo: vertical, ON the center axis, top flush with base top
add(bpy.ops.mesh.primitive_cube_add, "servo_pan", None, teal,
    (0, 0, BASE_TH - SERVO_H / 2), scale=(SERVO_W / 2, SERVO_D / 2, SERVO_H / 2),
    bevel=False, size=1)

# ============ panning parts (parented to pan_pivot; local coords) ============
# pan_pivot world z = 0.04 (BASE_H). Base top world 0.030 -> pan-local -0.010.
# pan shaft: from servo top up into the neck (this is what visibly rotates)
add(bpy.ops.mesh.primitive_cylinder_add, "shaft_pan", pan, charcoal,
    (0, 0, -0.005), bevel=False, radius=0.0035, depth=0.014)

# joint hub: sits directly on the base, honestly houses the tilt servo.
# No filler neck — nothing between base and tilt joint has a reason to exist.
add(bpy.ops.mesh.primitive_cylinder_add, "shell_hub", pan, frosted,
    (0, 0, NECK_H - 0.006), solidify=True,
    radius=HUB_R, depth=0.034)

# tilt servo: vertical body near the neck top, shaft axis along X at z=NECK_H
add(bpy.ops.mesh.primitive_cube_add, "servo_tilt", pan, teal,
    (0, 0, NECK_H - 0.006), scale=(SERVO_D / 2, SERVO_H / 2, SERVO_W / 2),
    bevel=False, size=1)   # thin side along X -> shaft protrudes along X

# tilt shaft: horizontal, exactly on the tilt axis
add(bpy.ops.mesh.primitive_cylinder_add, "shaft_tilt", pan, charcoal,
    (0, 0, NECK_H), rot=(0, math.radians(90), 0), bevel=False,
    radius=0.003, depth=SERVO_D + 0.014)

# ============ tilting parts (parented to tilt_pivot; local coords) ============
# horn disc on the shaft end — visibly transfers rotation to the head side
add(bpy.ops.mesh.primitive_cylinder_add, "horn_tilt", tilt, charcoal,
    (SERVO_D / 2 + 0.008, 0, 0), rot=(0, math.radians(90), 0), bevel=False,
    radius=0.007, depth=0.003)

# telescoping neck: fixed OUTER tube (housing) + INNER tube that slides out to
# STALK_LEN, carrying the nod servo + head. Friction collar locks it.
for nm in ["shell_stalk", "shell_stalk_outer", "shell_stalk_inner", "neck_collar"]:
    o = bpy.data.objects.get(nm)
    if o:
        bpy.data.objects.remove(o, do_unlink=True)

OUTER_LEN = NECK_PRESETS["short"]            # housing = shortest preset (fully retracted)
top = STALK_LEN if nod else STALK_LEN + head_r * 0.5

# outer (fixed housing on the tilt hub)
add(bpy.ops.mesh.primitive_cylinder_add, "shell_stalk_outer", tilt, frosted,
    (0, 0, OUTER_LEN / 2), radius=STALK_R + 0.0018, depth=OUTER_LEN, solidify=True)
# inner (slides out to the chosen length, thin, carries the head)
add(bpy.ops.mesh.primitive_cylinder_add, "shell_stalk_inner", tilt, frosted,
    (0, 0, top / 2), radius=STALK_R, depth=top, solidify=True)
# friction collar / lock knob at the top of the housing
add(bpy.ops.mesh.primitive_cylinder_add, "neck_collar", tilt, charcoal,
    (0, 0, OUTER_LEN), radius=STALK_R + 0.004, depth=0.006, bevel=False)

if nod:
    # nod servo: sits at the TOP of the thin stalk (won't fit inside 5mm);
    # tilts with the neck, drives the head
    old = bpy.data.objects.get("servo_nod")
    if old:
        bpy.data.objects.remove(old, do_unlink=True)
    add(bpy.ops.mesh.primitive_cube_add, "servo_nod", tilt, teal,
        (0, 0, STALK_LEN - SERVO_W / 2), scale=(SERVO_D / 2, SERVO_H / 2, SERVO_W / 2),
        bevel=False, size=1)
    head_lz = head_r * 0.5               # head-local height above the NOD joint
else:
    head_lz = STALK_LEN + head_r * 0.5   # legacy 2-DOF placement

# head = the original plain CYLINDER (searchlight tube) along +Y. Clean, directional.
for nm in ["shell_head", "shell_head_tail", "head_cap", "face_plate", "visor",
           "eye_ring", "eye_barrel", "eye_lens"]:
    o = bpy.data.objects.get(nm)
    if o:
        bpy.data.objects.remove(o, do_unlink=True)

face_y = 0.002 + head_len / 2   # front face (+Y, wide, holds the camera = FRONT)
BACK_R = head_r * 0.78          # rear radius -> gentle, gradual taper
# cone body: wide at the camera face (+Y), tapering to a smaller back (-Y)
add(bpy.ops.mesh.primitive_cone_add, "shell_head", head_parent, print_white,
    (0, 0.002, head_lz), rot=(math.radians(90), 0, 0), solidify=True,
    radius1=head_r, radius2=BACK_R, depth=head_len)
# rounded back cap closes the small end (lamp-like)
add(bpy.ops.mesh.primitive_uv_sphere_add, "head_cap", head_parent, print_white,
    (0, 0.002 - head_len / 2, head_lz),
    scale=(BACK_R, BACK_R * 0.6, BACK_R), solidify=True)

# eye = a HOLE in the front face; the Ø14 barrel sits inside the head and the
# lens tip pokes out only LENS_PROUD. Dark recessed socket reads as "the eye hole".
# recessed dark socket (the hole), flush in the front face
add(bpy.ops.mesh.primitive_cylinder_add, "eye_ring", head_parent, charcoal,
    (0, face_y - 0.003, head_lz), rot=(math.radians(90), 0, 0),
    radius=CAM_R + 0.003, depth=0.006)
# lens barrel: mostly INSIDE the head, tip protrudes LENS_PROUD past the face
add(bpy.ops.mesh.primitive_cylinder_add, "eye_barrel", head_parent, charcoal,
    (0, face_y + LENS_PROUD - CAM_LEN / 2, head_lz), rot=(math.radians(90), 0, 0),
    radius=CAM_R, depth=CAM_LEN)
# flat dark glass at the protruding tip
add(bpy.ops.mesh.primitive_cylinder_add, "eye_lens", head_parent, lens_m,
    (0, face_y + LENS_PROUD + 0.0005, head_lz), rot=(math.radians(90), 0, 0),
    bevel=False, radius=CAM_R * 0.8, depth=0.0015)

# ---- LED antenna: rebuilt fresh so it is ALWAYS visible & emissive ----
# (animation targets the led_mat material, not this object, so recreating is safe)
old_led = bpy.data.objects.get("led_antenna")
if old_led:
    bpy.data.objects.remove(old_led, do_unlink=True)
lm = bpy.data.materials.get("led_mat")
if lm is None or not any(n.type == 'EMISSION' for n in lm.node_tree.nodes):
    lm = lm or bpy.data.materials.new("led_mat")
    lm.use_nodes = True
    nt = lm.node_tree
    nt.nodes.clear()
    em = nt.nodes.new('ShaderNodeEmission')
    em.inputs['Color'].default_value = (1.0, 0.62, 0.22, 1.0)   # warm
    em.inputs['Strength'].default_value = 5.0
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    nt.links.new(em.outputs['Emission'], out.inputs['Surface'])
lm.diffuse_color = (1.0, 0.62, 0.22, 1.0)   # so it also shows in Solid view
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.006, location=(0, 0, 0))
led = bpy.context.object
led.name = "led_antenna"
led.parent = head_parent
led.matrix_parent_inverse = Matrix.Identity(4)
led.location = (0, face_y - 0.003, head_lz + head_r + 0.004)   # top-FRONT: reinforces heading
for c in led.users_collection:
    c.objects.unlink(led)
col.objects.link(led)
led.data.materials.append(lm)
bpy.ops.object.shade_smooth()

msg = ("Shell v5: pan servo sunk in base (static, correct), tilt servo inside "
       "frosted neck, shafts + horn drawn. No gaps.")
print(msg)
def draw(self, context):
    self.layout.label(text=msg)
bpy.context.window_manager.popup_menu(draw, title="Shell v5", icon='INFO')
