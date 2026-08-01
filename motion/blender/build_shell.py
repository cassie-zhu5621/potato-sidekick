# Shell v7 — massing model matched to "model for simulation.stl" (measured).
# Run AFTER build_pantilt_rig.py. Re-runnable and self-repairing.
#
# v7 fixes three things that made v6 look fragmented in the viewport:
#   1. Old proxy geometry (head / eye / neck / base) is now DELETED, not hidden.
#      hide_set() silently does nothing for objects outside the active view
#      layer, so the old cone head kept rendering next to the new one.
#   2. Every object is built with scale = 1 and rotation = 0, with size and
#      orientation baked into the MESH DATA. v6 scaled objects non-uniformly,
#      which distorts every bevel modifier: the rounding came out different on
#      each axis and the parts read as broken.
#   3. Shapes now follow the STL instead of being boxes. Measured profiles:
#      head front fits an ellipse (squircle n = 2.0) at d54, so the head is a
#      HORIZONTAL CAPSULE 85.5 deep, not a block. Neck likewise round, d18.
#      Only the face plate is a rounded square (n = 2.8, corner r ~14).
#
# Measured from the STL, table surface at z = 0:
#   base            cyl d115, h29           z   0 .. 29
#   pan housing     cyl d60,  h31           z   2 .. 33
#   pan disc        cyl d60,  h5            z  33 .. 38
#   tilt servo      block 36 x 36 x 32      z  38 .. 70    PAN AXIS   z = 35
#   tilt yoke       2 plates 16x7x36, y+-23 z  50 .. 86    TILT AXIS  z = 64
#   crossbar        16 x 52 x 7             z  79 .. 86
#   neck            cyl d18, h60            z  86 .. 146
#   nod servo       block 19 x 36 x 32      z 146 .. 178
#   nod yoke        2 plates 16x7x35, y+-22 z 163 .. 198   NOD AXIS   z = 180
#   head            capsule d54, 85.5 deep  z 178 .. 234
#   face plate      rounded sq 61.5, t10    flush with the head front
#
# AS BUILT: the neck was shortened 5 mm on the real prototype, to sit the head
# lower and read as cuter. NECK_SHORTEN carries that, and everything above the
# neck is derived from it, so a further neck change is a one-line edit. The
# table above stays as the raw STL measurement, for provenance.
#
# Orientation: the STL faces -X. The Blender rig faces +Y, which is what all
# existing clips were authored against, so the shell is built facing +Y.
#
# That -90 deg mapping has to be applied to the STRUCTURE as well as the head,
# and versions up to v7 did not, which is what made the forks look wrong:
#
#   In the STL both forks are pairs of plates separated along Y and thin along Y
#   (tilt = parts 11+12, 46mm apart; nod = parts 9+13, 44mm apart), so both pins
#   run Y. The face plate at x=-38 against a head spanning x=-33..52 puts the
#   heading at -X. Pin Y is perpendicular to heading -X, which is correct.
#
#   Mapping into the Blender rig is Rz(-90): heading -X -> +Y, and pin Y -> X.
#   The head was rotated. The forks were not: their Y separation was copied
#   across unchanged, so they drew a Y pin while nod_pivot drove X.
#
# So the forks genuinely were 90 deg out, in the model only. The hardware is
# fine, the motion is fine, and nothing needs re-calibrating or re-mounting.
# STRUCT_YAW below carries the rotation that was missing.

import bpy
import math
from mathutils import Matrix, Vector

# ---- as-built deviation from the STL ----
NECK_SHORTEN = 0.005

# Head mounting yaw, degrees, about the vertical axis through the nod joint.
#
# 0 = correct: the telescope's optical axis points +Y (forward) and the nod axis
# runs left-right, so nod pitches the head up and down.
#
# 90 = the mis-assembled state found on the prototype. It puts the nod axis
# PARALLEL to the optical axis, so nod rolls the head about its own line of
# sight instead of pitching it: S3's acknowledgement and S7's beckon become a
# sideways lurch, and the camera image rotates with them.
#
# The fix belongs on the head, not on the body. Rotating the body instead means
# re-zeroing pan by 90 deg, and pan is the one joint with no margin: it is
# cable-limited to -70/+76 deg (calibration 2026-07-26), and the S4 sweep spends
# +-60 of that to reach 178 deg of coverage. Shifting its zero moves the usable
# arc to roughly 20..166 deg and the forward field stops being reachable.
# Rotating the head costs nothing: no clip changes, no re-calibration.
#
# Set this to 90 only to reproduce the fault in the render.
HEAD_MOUNT_YAW = 0.0

# Yaw of the STRUCTURE (both forks, the crossbar, both servo blocks) about the
# vertical, degrees. This is the STL -> Blender heading rotation that v6 and v7
# applied to the head but forgot to apply here. 90 is correct; 0 reproduces the
# old bug, where the forks drew a Y pin while nod_pivot drove X.
#
# With this at 90 both forks straddle left-right, both pins run X, and both
# servo blocks sit with their long edge along X, which is the axis a Feetech
# turns about. tilt and nod are then parallel, as on the hardware: same
# direction, different speed and lever arm, which is what lets tilt read as a
# slow postural lean and nod as a fast head gesture. The motion grammar depends
# on it, since vertical carries affirmation and only pan makes the S6 shake.
STRUCT_YAW = 90.0

# Heading of the whole robot, degrees about the vertical, applied at a root
# empty above the base. Everything hangs off it: base, all three pivots, both
# forks, the barrel. Rotating here turns the robot RIGIDLY, so every internal
# angle is preserved and the clips play exactly as authored; only where the
# robot points in world space changes.
#
#    0  = Blender convention, faces +Y. What every clip was authored against.
#  -90  = matches the STL heading (-X), for overlaying the model on the print.
#
# Use this, not STRUCT_YAW, to overlay the model on the STL for checking.
# Note that if the hardware is ever re-oriented this way for real, pan's zero
# moves with it, and pan is cable-limited to -70/+76 deg with the S4 sweep
# already spending +-60 of that.
ROOT_YAW = 0.0

# ---- joint heights, metres ----
PAN_Z = 0.035
TILT_Z = 0.064
NOD_Z = 0.180 - NECK_SHORTEN


def up(z_stl):
    """World z of something above the neck, as built."""
    return z_stl - NECK_SHORTEN


# ---- masses, metres ----
BASE_D, BASE_H = 0.115, 0.029
PANH_D, PANH_H = 0.060, 0.031
DISC_D, DISC_H = 0.060, 0.005
TSERVO = (0.036, 0.036, 0.032)
YOKE = (0.016, 0.007, 0.036)
YOKE_Y = 0.023
BAR = (0.016, 0.052, 0.007)
NECK_D, NECK_H = 0.018, 0.060 - NECK_SHORTEN
NSERVO = (0.019, 0.036, 0.032)
NYOKE = (0.016, 0.007, 0.035)
NYOKE_Y = 0.022
# ---- HEAD: telescope barrel, front to back. TUNE HERE. ----
# (diameter, length) per segment, metres. Front segment is the objective end and
# carries the lens. Total length should stay near 0.0855 and the widest diameter
# near 0.054, or the head stops matching the neck and yoke it mounts to.
HEAD_SEGS = [
    (0.054, 0.012),    # objective / lens hood, widest
    (0.046, 0.028),    # front barrel
    (0.052, 0.008),    # raised focus ring, steps back out
    (0.038, 0.036),    # rear barrel, tapering into the nod yoke
]
HEAD_D = max(d for d, _ in HEAD_SEGS)
HEAD_LEN = sum(l for _, l in HEAD_SEGS)

# The flat square face plate belongs to the earlier block head, not to a
# telescope. Set True to bring it back.
FACE_PLATE = False
FACE_W, FACE_T, FACE_R = 0.0615, 0.010, 0.014

# Camera: a bore through the centre of the front cap, with the lens sitting at
# the bottom of it, rather than a disc stuck on the outside.
BORE_D, BORE_DEPTH = 0.026, 0.009
LENS_D, LENS_T = 0.022, 0.003
LED_D = 0.010

col = bpy.data.collections.get("PanTiltBot")
if not col:
    raise RuntimeError("Run build_pantilt_rig.py first (no PanTiltBot collection).")

# ---------------- rig: create or repair the three pivots ----------------
def empty(name, parent, loc):
    o = bpy.data.objects.get(name)
    if not o:
        o = bpy.data.objects.new(name, None)
        o.empty_display_type = 'PLAIN_AXES'
        o.empty_display_size = 0.02
        col.objects.link(o)
    o.parent = parent
    o.matrix_parent_inverse = Matrix.Identity(4)
    o.location = loc
    return o


root = empty("bot_root", None, (0, 0, 0))
root.rotation_mode = 'XYZ'
root.rotation_euler = (0, 0, math.radians(ROOT_YAW))
root.empty_display_size = 0.06

pan = empty("pan_pivot", root, (0, 0, PAN_Z))
pan.rotation_mode = 'XYZ'
tilt = empty("tilt_pivot", pan, (0, 0, TILT_Z - PAN_Z))
tilt.rotation_mode = 'XYZ'
nod = empty("nod_pivot", tilt, (0, 0, NOD_Z - TILT_Z))
nod.rotation_mode = 'XYZ'

# ---------------- clear out anything we are about to replace ----------------
# Proxy geometry from the original rig, plus every shell part from any earlier
# run. Deleted outright: hiding is not enough, and a stale part left parented to
# a pivot is exactly what makes the robot look like it fell apart.
OLD_PROXY = ["head", "eye", "neck", "base", "led_antenna"]
SHELL = ["sh_base", "sh_panhouse", "sh_disc", "servo_tilt", "sh_yokeL", "sh_yokeR",
         "sh_bar", "sh_neck", "servo_nod", "sh_nyokeL", "sh_nyokeR",
         "sh_head", "sh_face", "sh_lens", "sh_led"]
SHELL += ["sh_head%d" % i for i in range(8)]   # barrel segments, generous range
removed = 0
for n in OLD_PROXY + SHELL:
    o = bpy.data.objects.get(n)
    if o and o.type == 'MESH':
        bpy.data.objects.remove(o, do_unlink=True)
        removed += 1
# and any orphan mesh still parented to a pivot that we did not just name
for o in list(col.objects):
    if o.type == 'MESH' and o.parent in (root, pan, tilt, nod) and o.name not in SHELL:
        bpy.data.objects.remove(o, do_unlink=True)
        removed += 1


# ---------------- materials ----------------
def mat(name, color, rough=0.6, metallic=0.0, emit=False):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    if emit:
        s = nt.nodes.new('ShaderNodeEmission')
        s.inputs['Color'].default_value = color
        s.inputs['Strength'].default_value = 5.0
        nt.links.new(s.outputs['Emission'], out.inputs['Surface'])
    else:
        s = nt.nodes.new('ShaderNodeBsdfPrincipled')
        s.inputs["Base Color"].default_value = color
        s.inputs["Metallic"].default_value = metallic
        s.inputs["Roughness"].default_value = rough
        nt.links.new(s.outputs['BSDF'], out.inputs['Surface'])
    m.diffuse_color = color
    return m


white = mat("print_white", (0.87, 0.86, 0.83, 1), rough=0.65)
charcoal = mat("charcoal", (0.07, 0.07, 0.075, 1), rough=0.45)
teal = mat("servo_teal", (0.0, 0.55, 0.55, 1), rough=0.5)
lens_m = mat("lens_black", (0.01, 0.01, 0.02, 1), rough=0.1)
led_m = mat("led_mat", (1.0, 0.62, 0.22, 1), emit=True)


# ---------------- builders ----------------
# Every builder bakes size and orientation into the mesh, then links the object
# with scale 1 / rotation 0. Nothing downstream can distort a bevel.
def _finish(o, name, parent, material, loc, bevel=0.0, segs=3, smooth=True, yaw=0.0):
    if yaw:
        R = Matrix.Rotation(math.radians(yaw), 4, 'Z')
        o.data.transform(R)          # bake into the mesh, object stays unrotated
        loc = R @ Vector(loc)        # and swing the placement with it
    o.name = name
    o.data.name = name
    for c in list(o.users_collection):
        c.objects.unlink(o)
    col.objects.link(o)
    o.parent = parent
    o.matrix_parent_inverse = Matrix.Identity(4)
    o.location = loc
    o.rotation_euler = (0, 0, 0)
    o.scale = (1, 1, 1)
    o.data.materials.clear()
    o.data.materials.append(material)
    if bevel > 0:
        b = o.modifiers.new("round", 'BEVEL')
        b.width = bevel
        b.segments = segs
        b.limit_method = 'ANGLE'
        b.angle_limit = math.radians(35)
        b.miter_outer = 'MITER_ARC'
    if smooth:
        ctx = bpy.context.view_layer.objects
        prev = ctx.active
        ctx.active = o
        try:
            bpy.ops.object.shade_auto_smooth(angle=math.radians(35))
        except Exception:
            bpy.ops.object.shade_smooth()
        ctx.active = prev
    return o


def _bake(o, mat4):
    o.data.transform(mat4)


def box(name, parent, material, size, loc, bevel=0.0015, segs=2, yaw=0.0):
    bpy.ops.mesh.primitive_cube_add(size=1)
    o = bpy.context.object
    _bake(o, Matrix.Diagonal(Vector((size[0], size[1], size[2], 1.0))))
    return _finish(o, name, parent, material, loc, bevel, segs, yaw=yaw)


def cyl(name, parent, material, d, h, loc, axis='Z', bevel=0.0, segs=3, verts=48, yaw=0.0):
    bpy.ops.mesh.primitive_cylinder_add(radius=d / 2, depth=h, vertices=verts)
    o = bpy.context.object
    if axis == 'Y':
        _bake(o, Matrix.Rotation(math.radians(90), 4, 'X'))
    elif axis == 'X':
        _bake(o, Matrix.Rotation(math.radians(90), 4, 'Y'))
    return _finish(o, name, parent, material, loc, bevel, segs, yaw=yaw)


def plate(name, parent, material, w, t, loc, r, yaw=0.0):
    """Rounded square plate, thickness along Y, facing +Y."""
    bpy.ops.mesh.primitive_cube_add(size=1)
    o = bpy.context.object
    _bake(o, Matrix.Diagonal(Vector((w, t, w, 1.0))))
    return _finish(o, name, parent, material, loc, bevel=r, segs=8, yaw=yaw)


# ================= static: base =================
cyl("sh_base", root, white, BASE_D, BASE_H, (0, 0, BASE_H / 2), bevel=0.003, segs=3)

# ================= on pan (local z = world z - PAN_Z) =================
cyl("sh_panhouse", pan, white, PANH_D, PANH_H,
    (0, 0, (0.002 + PANH_H / 2) - PAN_Z), bevel=0.002)
cyl("sh_disc", pan, charcoal, DISC_D, DISC_H,
    (0, 0, (0.033 + DISC_H / 2) - PAN_Z), bevel=0.001)
S = STRUCT_YAW   # STL -> Blender heading rotation for the structure
box("servo_tilt", pan, teal, TSERVO, (0, 0, (0.038 + TSERVO[2] / 2) - PAN_Z), yaw=S)

# ================= on tilt (local z = world z - TILT_Z) =================
# Fork arms are placed at +-YOKE_Y along Y and then yawed, so at STRUCT_YAW 90
# they end up straddling X and the pin through them runs X, matching the axis
# tilt_pivot actually turns about.
zc = (0.050 + YOKE[2] / 2) - TILT_Z
box("sh_yokeL", tilt, white, YOKE, (0, -YOKE_Y, zc), yaw=S)
box("sh_yokeR", tilt, white, YOKE, (0, YOKE_Y, zc), yaw=S)
box("sh_bar", tilt, white, BAR, (0, 0, (0.079 + BAR[2] / 2) - TILT_Z), yaw=S)
cyl("sh_neck", tilt, white, NECK_D, NECK_H,
    (0, 0, (0.086 + NECK_H / 2) - TILT_Z), bevel=0.001)
box("servo_nod", tilt, teal, NSERVO,
    (0, 0, (up(0.146) + NSERVO[2] / 2) - TILT_Z), yaw=S)
zc = (up(0.163) + NYOKE[2] / 2) - TILT_Z
box("sh_nyokeL", tilt, white, NYOKE, (0, -NYOKE_Y, zc), yaw=S)
box("sh_nyokeR", tilt, white, NYOKE, (0, NYOKE_Y, zc), yaw=S)

# ================= on nod (local z = world z - NOD_Z) =================
# Head is a capsule lying along the facing axis (+Y), d54 x 85.5 deep.
# Placed by its measured centre (STL 206.2) rather than by its underside, so the
# capsule sits where the STL head sits despite being 1mm shallower than the
# 54 x 55 profile it approximates.
HEAD_CZ = up(0.2062)
head_z = HEAD_CZ - NOD_Z
Y = HEAD_MOUNT_YAW      # 0 = correct. See the note at the top of this file.

# Telescope barrel, laid out front to back from the nose. y_front is the tip.
y_front = HEAD_LEN / 2
y = y_front
for i, (d, ln) in enumerate(HEAD_SEGS):
    is_nose = (i == 0)
    is_tail = (i == len(HEAD_SEGS) - 1)
    cyl("sh_head%d" % i, nod, white, d, ln, (0, y - ln / 2, head_z),
        axis='Y', bevel=0.004 if (is_nose or is_tail) else 0.0015,
        segs=4, verts=64, yaw=Y)
    y -= ln

if FACE_PLATE:
    face_y = y_front + FACE_T / 2
    plate("sh_face", nod, charcoal, FACE_W, FACE_T, (0, face_y, head_z),
          r=FACE_R, yaw=Y)
    y_front = face_y + FACE_T / 2

# Camera: bore a hole through the centre of the front cap, then seat the lens at
# the bottom of it. Cutting the hole rather than facing a disc onto the nose is
# what makes the barrel read as an instrument at a distance: the shadowed ring
# reads even when the lens itself is too small to see.
nose = bpy.data.objects.get("sh_head0")
if nose:
    bpy.ops.mesh.primitive_cylinder_add(radius=BORE_D / 2, depth=BORE_DEPTH * 2,
                                        vertices=48)
    cutter = bpy.context.object
    _bake(cutter, Matrix.Rotation(math.radians(90), 4, 'X'))
    # centred on the nose face, so it cuts exactly BORE_DEPTH inward
    cloc = Vector((0, y_front, head_z))
    if Y:
        cloc = Matrix.Rotation(math.radians(Y), 4, 'Z') @ cloc
    cutter.location = cloc
    cutter.parent = nod
    cutter.matrix_parent_inverse = Matrix.Identity(4)

    b = nose.modifiers.new("camera_bore", 'BOOLEAN')
    b.operation = 'DIFFERENCE'
    b.object = cutter
    b.solver = 'EXACT'
    ctx = bpy.context.view_layer.objects
    ctx.active = nose
    # The bore must be cut before the rim bevel runs, or Blender applies it out
    # of stack order and rounds an edge that is about to be deleted.
    try:
        bpy.ops.object.modifier_move_to_index(modifier="camera_bore", index=0)
    except Exception:
        pass
    try:
        bpy.ops.object.modifier_apply(modifier="camera_bore")
    except Exception as e:
        print("bore boolean not applied (%s); cutter left in scene" % e)
    if cutter.name in bpy.data.objects:
        bpy.data.objects.remove(cutter, do_unlink=True)

cyl("sh_lens", nod, lens_m, LENS_D, LENS_T,
    (0, y_front - BORE_DEPTH + LENS_T / 2, head_z), axis='Y', yaw=Y)

# LED sitting on top of the main barrel, just behind the objective. Its height
# follows whichever segment it lands on, so it never floats off a thinner one.
led_y = HEAD_LEN / 2 - HEAD_SEGS[0][1] - 0.008
_yy, led_r = HEAD_LEN / 2, HEAD_D / 2
for d, ln in HEAD_SEGS:
    if _yy - ln <= led_y <= _yy:
        led_r = d / 2
        break
    _yy -= ln
bpy.ops.mesh.primitive_uv_sphere_add(radius=LED_D / 2, segments=24, ring_count=12)
_finish(bpy.context.object, "sh_led", nod, led_m,
        (0, led_y, head_z + led_r + LED_D / 3), yaw=Y)

# ---------------- report ----------------
total = HEAD_CZ + HEAD_D / 2
anim = [p.name for p in (pan, tilt, nod) if p.animation_data and p.animation_data.action]
msg = (f"Shell v7: {total*1000:.0f}mm tall, base d{BASE_D*1000:.0f}, "
       f"capsule head d{HEAD_D*1000:.0f} x {HEAD_LEN*1000:.0f} deep")
msg2 = (f"pan {PAN_Z*1000:.0f} / tilt {TILT_Z*1000:.0f} / nod {NOD_Z*1000:.0f}mm, "
        f"neck {(NOD_Z-TILT_Z)*1000:.0f}mm. Removed {removed} stale objects.")
msg3 = ("Pivots are KEYFRAMED: what you see is the current frame, not the rest pose."
        if anim else "No keyframes on the pivots; this is the rest pose.")
if HEAD_MOUNT_YAW:
    msg3 = (f"HEAD_MOUNT_YAW={HEAD_MOUNT_YAW:g} deg -- nod now ROLLS the head, "
            f"not pitches it. This is the fault state, not the design.")
msg4 = (f"STRUCT_YAW={STRUCT_YAW:g}: both forks straddle X, pins run X, matching "
        f"tilt/nod. ROOT_YAW={ROOT_YAW:g}."
        if STRUCT_YAW else
        "WARNING STRUCT_YAW=0: forks draw a Y pin but the rig drives X.")
print(msg4)
for m in (msg, msg2, msg3):
    print(m)


def draw(self, context):
    for t in (msg, msg2, msg3, msg4):
        self.layout.label(text=t)


bpy.context.window_manager.popup_menu(draw, title="Shell v7", icon='INFO')
