# Widen (or narrow) the nod joint's Limit Rotation constraint. Nothing else.
#
# Run this in a .blend that already has the 3-DOF rig. add_nod_joint.py creates
# the constraint but SKIPS it if one already exists, so it cannot widen a rig
# that has already been built -- and re-running the whole of add_nod_joint.py is
# not a safe substitute, because it also resets nod.location to its own NOD_H
# and would undo a neck length that build_shell.py owns.
#
# Why this is needed now: nod was re-calibrated on 2026-08-01 to (474,814) about
# centre 644 = +/-49.9 deg, nearly double the previous +/-26.4. The Blender
# constraint of +/-30 had quietly become the binding limit -- S1's chin tuck was
# being capped in the rig while the servo had 20 degrees spare.

import bpy
import math

LIMIT_DEG = 60.0       # keep BELOW the calibrated hardware range (+/-49.9) so
                       # Blender stays the conservative one. If the calibration
                       # changes, change this with it.

nod = bpy.data.objects.get("nod_pivot")
if not nod:
    raise RuntimeError("no nod_pivot -- run build_pantilt_rig.py, repair_rig.py, "
                       "then add_nod_joint.py first.")

con = next((c for c in nod.constraints if c.type == 'LIMIT_ROTATION'), None)
if con is None:
    con = nod.constraints.new('LIMIT_ROTATION')
    con.owner_space = 'LOCAL'
    was = "created"
else:
    was = f"was +/-{math.degrees(con.max_x):.0f}"

con.use_limit_x = True
con.min_x = math.radians(-LIMIT_DEG)
con.max_x = math.radians(LIMIT_DEG)

# The pose itself is keyframed, so widening the rail does not move anything on
# its own -- re-run generate_s1_idle.py afterwards to write the deeper pose.
msg = f"nod Limit Rotation: {was} -> +/-{LIMIT_DEG:.0f} deg"
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text="Nothing else was touched -- location, parenting and")
    self.layout.label(text="keyframes are as they were.")
    self.layout.label(text="Now re-run generate_s1_idle.py to write the pose.")


bpy.context.window_manager.popup_menu(draw, title="nod limit", icon='INFO')
