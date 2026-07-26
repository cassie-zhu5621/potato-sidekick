# Fixes the parenting offset bug in build_pantilt_rig.py.
# Run ONCE in rig.blend AND in every existing clip .blend (S1...S7).
# Keyframes are untouched — only rest positions are corrected.

import bpy
from mathutils import Matrix

BASE_H = 0.04
NECK_H = 0.018     # tilt joint sits just above the base (no filler neck)
HEAD_R = 0.035

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
