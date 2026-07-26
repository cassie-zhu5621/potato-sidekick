# Auto-generates the S4 PLANNING sweep clip — 3-DOF. Run inside S4_PLAN.blend
# after repair_rig.py + add_nod_joint.py. Overwrites all keys.
#
# v2 (2026-07-26). Rewritten after testing v1 on the real prototype. Three
# things changed, all driven by hardware/observation rather than taste:
#
# 1. NO NOD ANYWHERE. v1 gave a nod "peck" at every capture station and ended
#    with the head dropped (LOCK_NOD = -6). Two problems: the peck swings the
#    camera exactly when it is supposed to be taking a picture, and the dropped
#    head means the pose S4 hands to S5 is a downward stare, not a level gaze.
#    Capture is now signalled by the LED alone — the camera stays still, and
#    "I photographed this" is carried by light rather than motion.
#
# 2. PAN RANGE ±150° -> ±60°. The real robot cannot do ±150°: measured travel
#    is 268..768 servo units about centre 508, i.e. -70°..+76°, and it is the
#    cable loom through the pan axis that sets that, not the servo. ±60° leaves
#    ~10° of margin at the tighter end. (hardware/calibration.py has the
#    measurements and how they were taken.)
#
# 3. SOFTER ARRIVAL. v1 snapped in with 4° of pan overshoot plus overshoot on
#    both tilt and nod. On hardware that reads as a slam. Now: a long
#    decelerating return, 1.5° of overshoot, and a lean-in that eases.
#
# Ends at pan +25°, tilt -12°, nod 0° — which IS S5_TRACK's opening pose, so the
# handover into tracking needs no transition of its own.

import bpy
import math

# ---- tune ----
CAM_HFOV = 58.0        # OV4688 UVC module, 32x32mm: D=71°, H=58°.
                       # NOT the 96° in HANDOFF_hardware_playback.md -- that
                       # figure was for the old OV2735 and is now stale.

N_STATIONS = 5         # Now partly a COVERAGE constraint, not just taste: with
                       # a 58° horizontal frame the step must stay under 58° or
                       # the sweep leaves unphotographed gaps between stations.
                       #   N=3  step 60°  5.0 s   GAP -- do not use
                       #   N=4  step 40°  5.6 s   31% overlap, minimum sensible
                       #   N=5  step 30°  6.2 s   48% overlap
                       #   N=7  step 20°  7.4 s   66% overlap
                       # Beyond coverage it still buys how deliberate the scan
                       # LOOKS, and how finely the "richest position" resolves
                       # (= one step). Travel time rescales itself via
                       # STATION_SPEED, so the character holds at any N.
SWEEP_DEG = 60.0       # sweep runs -SWEEP_DEG .. +SWEEP_DEG.
                       # Each frame also sees CAM_HFOV/2 past the head's aim, so
                       # the covered field is 2*SWEEP_DEG + CAM_HFOV = 178° —
                       # i.e. the forward 180° the study cares about. The pan
                       # cable limit (-70°/+76°) and that requirement land on the
                       # same number, so widening the sweep buys nothing.
RICHEST_DEG = 25.0     # demo value; at runtime from the VLM's richest_frame_index
                       # (also S5_TRACK's pan, so S4 hands straight into S5)

STATION_SPEED = 75.0   # deg/s between stations. Set the SPEED, not the frame
                       # count, so changing N_STATIONS keeps the same feel and
                       # cannot accidentally push past the servo ceiling.
SETTLE_F = 8           # frames standing still after arriving, BEFORE the shutter
DWELL_F = 18           # total frames parked at each station

SWEEP_TILT = 0.0       # neck stays VERTICAL for the whole sweep. The lean is
                       # what makes the final "I'm looking at this one" read as
                       # a change of posture; leaning the whole time spends that
                       # signal on nothing.
LEAN_TILT = -12.0      # final "craning forward to look" — matches S5_TRACK
LEAN_NOD = 15.0        # head LIFTS at the lock. In Blender, positive nod = UP.

RETURN_SPEED = 60.0    # deg/s for the return to the richest station. Derived,
                       # not a frame count: sweeping right-to-left means the
                       # return can be anything from 35 to 145 deg depending on
                       # which station won, and a fixed frame count would turn
                       # the long ones into a lunge. Slower than STATION_SPEED
                       # because this move is a decision being shown, not travel.
OVERSHOOT_DEG = 1.5    # a little life, not a slam
FPS = 30

# SIGN CONVENTION — two different frames, do not mix them up:
#
#   BLENDER (what you author here):  positive nod = head UP.
#   SERVO UNITS (what the bus sees): higher unit    = head DOWN.
#
# They run opposite, which is exactly what INVERT["nod"] = True in
# hardware/calibration.py exists to reconcile. Author against the RENDER, always
# — the render is the thing being designed, and the servo frame is an
# implementation detail that INVERT absorbs.
#
# Budget IN BLENDER SIGNS: nod may run **+26° (up) .. -47° (down)**. Note the
# asymmetry is now on the up side, which is the side this clip uses — +15° here
# leaves only 11° of headroom.
#
# LEAN_NOD note: tilt and nod compound, because the camera rides on the head at
# the end of the neck. Leaning the neck forward by LEAN_TILT pitches the optical
# axis down by roughly that much even with nod at zero — nod 0 keeps the head
# level RELATIVE TO THE NECK, which is not the same as the camera being level.
# Hence +15° up: it cancels the 12° of lean and leaves the gaze ~3° above
# horizontal.
#
# It earns its keep twice. Geometrically it levels the view of the thing the
# robot just chose. Socially, a head coming up at the end of a search is the
# reading beat — "I have stopped looking FOR and started looking AT" — so the
# handover into S5 is announced by posture instead of just happening.
# --------------

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


# Sweep RIGHT-to-LEFT, not left-to-right. The direction is arbitrary for
# coverage, but not for the cycle: S2_LISTEN and S3_ACK both end at pan +60 --
# the robot is facing the person who just spoke to it. Starting the sweep at -60
# meant a 120 deg, ~1 s unauthored swing sat in the middle of the designed cycle,
# inserted by the player because no clip covered it. Starting at +60 makes
# S3 -> S4 seamless, and S4 still ends at RICHEST_DEG so S4 -> S5 stays seamless.
start_deg, end_deg = SWEEP_DEG, -SWEEP_DEG
step = (end_deg - start_deg) / (N_STATIONS - 1)          # negative: sweeps left
MOVE_F = max(6, round(abs(step) / STATION_SPEED * FPS))

f = 1
key(tilt, "x", f, SWEEP_TILT)
key(nod, "x", f, 0.0)
key_led(f, 1.0)

for i in range(N_STATIONS):
    a = start_deg + i * step
    key(pan, "z", f, a)                       # arrive
    key(pan, "z", f + DWELL_F, a)             # and stay put for the whole dwell

    # Shutter fires only after the head has stopped moving. Short and bright, so
    # it reads as a discrete event rather than a pulse.
    s = f + SETTLE_F
    key_led(s - 1, 1.0)
    key_led(s + 1, 7.0)
    key_led(s + 4, 1.0)

    f += DWELL_F + MOVE_F

f -= MOVE_F                                    # no travel after the last station

# nod is pinned flat for the whole sweep. Keying it explicitly at the end as
# well as the start stops Bezier handles from drifting it in between.
key(nod, "x", f, 0.0)
key(tilt, "x", f, SWEEP_TILT)

# --- return to the richest station, then crane in ---
RETURN_F = max(8, round(abs(end_deg - RICHEST_DEG) / RETURN_SPEED * FPS))
f_ret = f + RETURN_F
approach = RICHEST_DEG + (OVERSHOOT_DEG if RICHEST_DEG < end_deg else -OVERSHOOT_DEG)
key(pan, "z", f_ret, approach)
key(pan, "z", f_ret + 8, RICHEST_DEG)          # ease onto it

key(tilt, "x", f_ret, SWEEP_TILT)              # neck starts leaning as it arrives
key(tilt, "x", f_ret + 12, LEAN_TILT)          # eased, no overshoot

# The head lift trails the neck lean rather than moving with it. Simultaneous
# reads as one mechanical pose change; offset reads as two beats -- "I've come
# to this one", then "and now I'm looking at it". The second beat is the one
# that has to be legible, so it gets to happen on its own.
key(nod, "x", f_ret + 10, 0.0)
key(nod, "x", f_ret + 24, LEAN_NOD)

key_led(f_ret, 1.0)
key_led(f_ret + 24, 3.0)                       # steady on as the head comes up

end = f_ret + 34
key(pan, "z", end, RICHEST_DEG)
key(tilt, "x", end, LEAN_TILT)
key(nod, "x", end, LEAN_NOD)
key_led(end, 3.0)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = end

# --- sanity check before you even export ---
# Bezier easing peaks at roughly 2x a segment's average rate, so check the
# average against half the ceiling. Catching this here beats finding it in
# export_clip.py, and finding it there beats finding it on the servo.
if abs(step) >= CAM_HFOV:
    raise RuntimeError(
        f"step is {abs(step):.0f}° but the camera only sees {CAM_HFOV:.0f}° "
        f"horizontally -- the sweep would leave gaps. Raise N_STATIONS to at "
        f"least {int(2 * SWEEP_DEG // CAM_HFOV) + 2}.")

seg_avg = abs(step) / (MOVE_F / FPS)
ret_avg = abs(end_deg - RICHEST_DEG) / (RETURN_F / FPS)
peak_est = 2.0 * max(seg_avg, ret_avg)
warn = "  !! over 200 deg/s — raise MOVE_F / RETURN_F" if peak_est > 200 else ""

msg = (f"S4 v2: {N_STATIONS} stations x {step:.0f}° over ±{SWEEP_DEG:.0f}°, "
       f"neck vertical during sweep, lock at {RICHEST_DEG:.0f}°/{LEAN_TILT:.0f}°"
       f"/{LEAN_NOD:.0f}° (head up), "
       f"{end}f ({end / FPS:.1f}s), est peak {peak_est:.0f} deg/s{warn}")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(
    draw, title="S4 sweep v2" if not warn else "S4 sweep v2 — TOO FAST", icon='INFO')
