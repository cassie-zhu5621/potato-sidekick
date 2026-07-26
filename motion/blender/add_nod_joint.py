# Upgrades the rig to 3 DOF: pan (turn) -> tilt (neck lean) -> nod (head nod).
# Run ONCE in rig.blend and in each clip file (after repair_rig.py).
# Existing tilt keyframes are untouched — redistribute lean vs nod per clip after.
#
# nod_pivot: rotation X only, +/-30 deg, sits NOD_H above the tilt joint
# (= top of the telescoping stalk, where the third servo lives).

import bpy
import math
from mathutils import Matrix

NOD_H = 0.08      # distance tilt joint -> nod joint (= stalk length); 8cm thin neck
HEAD_R = 0.035

tilt = bpy.data.objects.get("tilt_pivot")
if not tilt:
    raise RuntimeError("Run build_pantilt_rig.py + repair_rig.py first.")

nod = bpy.data.objects.get("nod_pivot")
if not nod:
    bpy.ops.object.empty_add(type='PLAIN_AXES')
    nod = bpy.context.object
    nod.name = "nod_pivot"
    nod.empty_display_size = 0.02
    col = bpy.data.collections.get("PanTiltBot")
    if col:
        for c in nod.users_collection:
            c.objects.unlink(nod)
        col.objects.link(nod)

nod.parent = tilt
nod.matrix_parent_inverse = Matrix.Identity(4)
nod.location = (0, 0, NOD_H)
nod.rotation_mode = 'XYZ'
nod.lock_rotation = (False, True, True)
nod.lock_location = (True, True, True)
if not any(c.type == 'LIMIT_ROTATION' for c in nod.constraints):
    c = nod.constraints.new('LIMIT_ROTATION')
    c.use_limit_x = True
    c.min_x = math.radians(-30)
    c.max_x = math.radians(30)
    c.owner_space = 'LOCAL'

# head parts now ride on the nod joint
def reparent(name, loc):
    o = bpy.data.objects.get(name)
    if o:
        o.parent = nod
        o.matrix_parent_inverse = Matrix.Identity(4)
        o.location = loc

reparent("head", (0, 0, HEAD_R * 0.5))
reparent("eye", (0, HEAD_R * 0.95, HEAD_R * 0.5))
reparent("led_antenna", (0, 0, HEAD_R * 1.4))

msg = "3-DOF rig: pan -> tilt (neck lean) -> nod (head nod, +/-30)."
print(msg)
def draw(self, context):
    self.layout.label(text=msg)
bpy.context.window_manager.popup_menu(draw, title="Nod joint added", icon='INFO')
