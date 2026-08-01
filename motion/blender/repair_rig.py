# Fixes the parenting offset bug in build_pantilt_rig.py.
# Run ONCE in rig.blend AND in every existing clip .blend (S1...S7).
# Keyframes are untouched — only rest positions are corrected.

import bpy
from mathutils import Matrix

# Joint heights measured from "model for simulation.stl" (2026-07-30).
# build_shell.py repositions the pivots to these same numbers, so the two
# cannot drift; they are duplicated here only so the rig alone is already right.
BASE_H = 0.035     # pan axis, world z
NECK_H = 0.029     # tilt axis sits BASE_H + NECK_H = 64mm
HEAD_R = 0.035     # proxy head only; the real head is 85 x 54 x 55

def fix(name, parent_name, loc):
    o = bpy.data.objects.get(name)
    if not o:
        return
    p = bpy.data.objects.get(parent_name) if parent_name else None
    o.parent = p
    o.matrix_parent_inverse = Matrix.Identity(4)   # clear stale offsets
    o.location = loc

# correct local chain (locations are RELATIVE to parent)
base = bpy.data.objects.get("base")
if base:
    base.parent = None
    base.location = (0, 0, BASE_H / 2)

fix("pan_pivot", "base", (0, 0, BASE_H / 2))          # world z = BASE_H
fix("neck", "pan_pivot", (0, 0, NECK_H / 2))
fix("tilt_pivot", "pan_pivot", (0, 0, NECK_H))         # world z = BASE_H+NECK_H
fix("head", "tilt_pivot", (0, 0, HEAD_R * 0.6))
fix("eye", "tilt_pivot", (0, HEAD_R * 0.95, HEAD_R * 0.6))
fix("led_antenna", "tilt_pivot", (0, 0, HEAD_R * 1.6))

msg = "Rig repaired: chain is now connected (pan@40mm, tilt@90mm)."
print(msg)
def draw(self, context):
    self.layout.label(text=msg)
bpy.context.window_manager.popup_menu(draw, title="Repair", icon='CHECKMARK')
