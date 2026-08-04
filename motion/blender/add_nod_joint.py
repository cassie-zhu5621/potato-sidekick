# Upgrades the rig to 3 DOF: pan (turn) -> tilt (neck lean) -> nod (head nod).
# Run ONCE in rig.blend and in each clip file (after repair_rig.py).
# Existing tilt keyframes are untouched — redistribute lean vs nod per clip after.
#
# nod_pivot: rotation X only, +/-45 deg, sits NOD_H above the tilt joint
# (= top of the telescoping stalk, where the third servo lives).
#
# The limit was +/-30. Hardware calibration now gives nod +/-49.9 deg, so 30 had
# become the binding constraint rather than a safety margin -- S1's chin-tuck was
# being capped in BLENDER while the servo had 20 more degrees available.
#
# RE-RUNNING THIS SCRIPT WILL NOT UPDATE AN EXISTING .blend: the guard below only
# creates the constraint when there is none. To widen it in a file that already
# has one, edit min_x / max_x on the nod_pivot's Limit Rotation constraint in the
# N-panel, or delete the constraint and run this again.

import bpy
import math
from mathutils import Matrix

NOD_H = 0.111     # tilt axis -> nod axis, AS BUILT: STL measured 116mm (64 -> 180)
                  # and the neck was then shortened 5mm on the prototype so the
                  # head sits lower. Was 0.08. The longer neck makes every tilt
                  # angle travel ~39% further at the head than the old rig;
                  # see HANDOFF_motion_design.md. build_shell.py owns this number
                  # via NECK_SHORTEN and will overwrite the pivot anyway.
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
# UPDATE an existing constraint rather than skipping it. The old version only
# created one when absent, so widening the limit in this file had no effect on
# any .blend already built -- the rig kept the value it was born with while the
# source claimed otherwise. To change ONLY the limit on an existing rig, use
# set_nod_limit.py: re-running all of this also resets nod.location to NOD_H,
# which build_shell.py owns.
_c = next((c for c in nod.constraints if c.type == 'LIMIT_ROTATION'), None)
if _c is None:
    _c = nod.constraints.new('LIMIT_ROTATION')
    _c.owner_space = 'LOCAL'
_c.use_limit_x = True
_c.min_x = math.radians(-45)
_c.max_x = math.radians(45)

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

msg = "3-DOF rig: pan -> tilt (neck lean) -> nod (head nod, +/-45)."
print(msg)
def draw(self, context):
    self.layout.label(text=msg)
bpy.context.window_manager.popup_menu(draw, title="Nod joint added", icon='INFO')
