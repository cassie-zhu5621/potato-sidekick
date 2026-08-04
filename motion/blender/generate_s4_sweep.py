# Auto-generates S4 PLAN (3-DOF + LED). Run inside S4_PLAN.blend after
# repair_rig.py + add_nod_joint.py. OVERWRITES all keys.
# Design rationale: ../../../robot_motion/S4_S5_DESIGN.md (local, not in this repo).
#
# S4 goes and looks: it sweeps the forward field and captures one frame per
# station. IT DOES NOTHING ELSE. Arriving at the chosen thing belongs to S5a.
#
# ONE JOB, AND THE REASON IS THE PROJECT'S OWN LIMIT CASE.
#
#   S4 no longer runs only after S3. It also fires on its own -- after 30 s of
#   tracking nothing, and periodically -- because an object that entered the room
#   after the last plan has never been detected, is not in the candidate set, and
#   can therefore never be chosen. The camera sees 58 deg on a body that pans
#   ~115: it cannot know what is outside the frame without moving.
#
#   That is not a movement <=> result violation. The rule governs EXPRESSIVE
#   movement; a sweep is EPISTEMIC -- it is not reporting a detection, it IS the
#   detecting, and it is honest because the robot genuinely cannot see without it.
#
#   The periodic sweep is ADDITIVE: it extends the candidate set rather than
#   re-choosing from scratch, so it cannot silently undo a correction the user
#   made with a body tap.
#
#   Being additive, it will often change nothing -- and a clip that ended by
#   craning in to "the chosen one" would then perform a decision that did not
#   happen, every five minutes, about the object it was already watching.
#   NO RESULT, NO MOVEMENT.
#
#   Rather than branch inside this clip, the arrival was made its own state:
#
#     target CHANGED  ->  S4 -> S5a -> S5b.  S5a is the arrival, AUTHORED: the
#                         crane with the head lift trailing it. "I have come to
#                         this one, and now I am looking at it."
#     target SAME     ->  S4 -> S5b.         The re-crane is a TRANSITION -- the
#                         player travels between two held poses, carrying no
#                         expressive content. Nothing is authored because nothing
#                         happened.
#
#   So the limit case is expressed by NOT ENTERING A STATE, which is cleaner than
#   any branch. S4 therefore ends LEVEL at the chosen pan, and never cranes.
#
# SIGN CONVENTION, two frames that run opposite:
#   BLENDER, what you author here:   positive nod = head UP
#   SERVO UNITS, what the bus sees:  higher unit  = head DOWN
# INVERT in robot/calibration.py reconciles them. Author against the render.

import bpy
import math

# ---- field ----
CAM_HFOV = 58.0        # OV4688 UVC module, 32x32mm: D=71 deg, H=58 deg.
SWEEP_DEG = 60.0       # sweep runs +SWEEP_DEG .. -SWEEP_DEG.
                       #
                       # Covered field is 2*SWEEP + CAM_HFOV = 178 deg, i.e. the
                       # forward 180 the study cares about.
                       #
                       # This value went 60 -> 55 -> 60. It was cut when the pan
                       # rail read +57.4 and a sweep starting at +60 was clamped
                       # on the state's FIRST FRAME. The rail turned out to be a
                       # DATA CABLE routed in front of the neck; re-routing it
                       # gave pan +69.7 back, and 60 now has 9.7 deg of margin.
                       #
                       # So the old note -- "the cable limit and the coverage
                       # requirement land on the same number" -- was accidentally
                       # true, and for the wrong reason. A REACHABLE RANGE IS AN
                       # ASSEMBLY STATE, NOT A PROPERTY OF THE BUILD.
N_STATIONS = 5         # COVERAGE constraint, not taste: the step must stay under
                       # CAM_HFOV or the sweep leaves unphotographed gaps.
                       #   N=3  step 60.0  GAP -- over the 58 deg frame
                       #   N=4  step 40.0  31% overlap
                       #   N=5  step 30.0  48% overlap   <- used
                       # Beyond coverage it buys how deliberate the scan LOOKS,
                       # and how finely "richest position" resolves (= one step).
RICHEST_DEG = 25.0     # demo value; at runtime from the VLM's richest_frame_index.
                       # ALSO S5B_TRACK's HOLD_PAN -- S4 hands straight into S5.

# ---- pose ----
SWEEP_TILT = 0.0       # neck VERTICAL for the whole sweep. The lean is what makes
                       # the ending read as a change of posture; leaning
                       # throughout would spend that signal on nothing.
LEAN_TILT = -12.0      # = S5B_TRACK HOLD_TILT
LEAN_NOD = 12.0        # = S5B_TRACK HOLD_NOD. EXACTLY cancels the lean: the
                       # gaze is level at -12 + 12 = 0. S5b holds this for
                       # minutes, so the camera frame has to be right.

# ---- timing ----
STATION_SPEED = 75.0   # deg/s between stations. Set the SPEED, not the frame
                       # count, so changing N_STATIONS keeps the same feel and
                       # cannot accidentally push past the servo ceiling.
SETTLE_F = 8           # frames standing still after arriving, BEFORE the shutter
DWELL_F = 18           # total frames parked at each station
RETURN_SPEED = 75.0    # deg/s back to the chosen station. SAME as the sweep:
                       # this is travel, not a decision. The decision, if there
                       # was one, is S5a's.
HOLD_IN_S = 0.20       # the library's boundary holds -- an event boundary, a
HOLD_OUT_S = 0.20      # moving hold, and a guard against colliding with the
                       # neighbouring clip. Newtson 1973; Zacks et al. 2007.

# ---- LED ----
LED_BASE = 1.0         # dim while travelling: the sweep is not an announcement
LED_SHUTTER = 7.0      # short and bright, AFTER the head has stopped. The
                       # clearest movement <=> result instance in the library --
                       # it marks a frame actually being captured, and nothing
                       # else in S4 flashes.
LED_END = 0.8          # back to S5b's breath TROUGH. No closing rise: S4 has
                       # captured, not chosen, and the light announces results.

FPS = 30
PEAK_FACTOR = 1.5      # Bezier easing peaks above a segment's average by roughly
                       # this much. These keys are sparse rather than per-frame
                       # sampled, so the check budgets the AVERAGE against
                       # 200 / PEAK_FACTOR.
# -----------------------------------------------------------

HOLD_IN_F = int(round(HOLD_IN_S * FPS))
HOLD_OUT_F = int(round(HOLD_OUT_S * FPS))

# ---- guard rails, read from the live calibration ----
import os
import sys

REACH_PATH = ""        # set if the .blend lives outside the repo


def _find_up(rel, starts, levels=8):
    """Walk up, and look one step down into each level's subdirectories -- the
    .blend files live in the local design folder and the generators in the repo,
    which makes them siblings. See generate_s2_listen.py."""
    for s in starts:
        if not s:
            continue
        d = os.path.abspath(s)
        for _ in range(levels):
            p = os.path.join(d, rel)
            if os.path.exists(p):
                return p
            try:
                subs = sorted(os.listdir(d))
            except OSError:
                subs = []
            for name in subs:
                if name.startswith("."):
                    continue
                p = os.path.join(d, name, rel)
                if os.path.exists(p):
                    return p
            up = os.path.dirname(d)
            if up == d:
                break
            d = up
    return None


_starts = [os.path.dirname(bpy.data.filepath), os.getcwd()]
_rp = REACH_PATH or (_find_up(os.path.join("motion", "blender", "reach.py"), _starts)
                     or _find_up("reach.py", _starts))
if not _rp or not os.path.exists(_rp):
    raise RuntimeError(
        "Cannot find motion/blender/reach.py.\n"
        "  bpy.data.filepath = " + repr(bpy.data.filepath) + "\n"
        "  cwd               = " + os.getcwd() + "\n"
        "Save the .blend inside the repo, or set REACH_PATH above.")

# Loaded by PATH, not by `import`: Blender caches modules for the whole session
# and a guard must not go stale after a re-calibration.
reach = type(sys)("reach")
reach.__file__ = _rp
with open(_rp) as _fh:
    exec(compile(_fh.read(), _rp, "exec"), reach.__dict__)
print("[s4] " + reach.summary())

_step = 2.0 * SWEEP_DEG / (N_STATIONS - 1)
if _step >= CAM_HFOV:
    raise RuntimeError(f"station step {_step:.1f} deg is not under the "
                       f"{CAM_HFOV:.0f} deg frame: the sweep would leave "
                       f"unphotographed gaps. Raise N_STATIONS or lower "
                       f"SWEEP_DEG.")
_extremes = [("pan", SWEEP_DEG, "sweep start"), ("pan", -SWEEP_DEG, "sweep end"),
             ("pan", RICHEST_DEG, "richest station"),
             ("tilt", SWEEP_TILT, "sweeping"), ("nod", 0.0, "level")]
for _j, _v, _w in _extremes:
    reach.check(_j, _v, _w)
reach.check_floor("pan", _step, "station step")

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
led_color = None
mat = bpy.data.materials.get("led_mat")
if mat and mat.use_nodes:
    if mat.node_tree.animation_data:
        mat.node_tree.animation_data_clear()
    for n in mat.node_tree.nodes:
        if n.type == 'EMISSION':
            led_strength = n.inputs['Strength']
            led_color = n.inputs['Color']
            break
if led_color is not None:
    led_color.default_value = (0.24, 0.59, 0.90, 1.0)     # cool: working
    mat.diffuse_color = (0.24, 0.59, 0.90, 1.0)


def key_led(frame, value):
    if led_strength is not None:
        led_strength.default_value = value
        led_strength.keyframe_insert(data_path="default_value", frame=frame)


def check_speed(deg, frames, what):
    """Sparse keys, so budget the AVERAGE and let PEAK_FACTOR cover the easing."""
    if frames <= 0:
        raise RuntimeError(f"{what}: zero frames")
    avg = abs(deg) / (frames / float(FPS))
    if avg * PEAK_FACTOR > 200.0:
        raise RuntimeError(f"{what} averages {avg:.0f} deg/s, peaking near "
                           f"{avg * PEAK_FACTOR:.0f} -- over the 200 deg/s "
                           f"ceiling. Give it more frames.")
    return frames


# Sweep RIGHT-to-LEFT. Arbitrary for coverage, not for the cycle: S3 leaves the
# robot facing the person at pan +50, so starting at +SWEEP keeps S3 -> S4 short.
start_deg, end_deg = SWEEP_DEG, -SWEEP_DEG
step = (end_deg - start_deg) / (N_STATIONS - 1)
MOVE_F = check_speed(step, max(6, round(abs(step) / STATION_SPEED * FPS)),
                     "station travel")

f = 1
key(pan, "z", f, start_deg)
key(tilt, "x", f, SWEEP_TILT)
key(nod, "x", f, 0.0)
key_led(f, LED_BASE)

# Opening hold. Entry from S5 is an un-crane of 12 deg tilt / 15 deg nod that no
# clip contains -- a TRANSITION, which by the library rule carries no expressive
# content and travels between two held poses. This hold is what keeps it from
# blurring into the first sweep leg.
f += HOLD_IN_F
key(pan, "z", f, start_deg)
key(tilt, "x", f, SWEEP_TILT)
key(nod, "x", f, 0.0)

for i in range(N_STATIONS):
    a = start_deg + i * step
    key(pan, "z", f, a)                       # arrive
    key(pan, "z", f + DWELL_F, a)             # and stay put for the whole dwell

    # Shutter fires only AFTER the head has stopped moving.
    s = f + SETTLE_F
    key_led(s - 1, LED_BASE)
    key_led(s + 1, LED_SHUTTER)
    key_led(s + 4, LED_BASE)

    f += DWELL_F + MOVE_F

f -= MOVE_F                                   # no travel after the last station

# nod is pinned flat for the whole sweep; keying it at the end as well as the
# start stops Bezier handles from drifting it in between.
key(nod, "x", f, 0.0)
key(tilt, "x", f, SWEEP_TILT)

# --- return to the chosen station, LEVEL. No crane: that is S5a's, if it runs.
RETURN_F = check_speed(end_deg - RICHEST_DEG,
                       max(8, round(abs(end_deg - RICHEST_DEG) / RETURN_SPEED * FPS)),
                       "return")
f_ret = f + RETURN_F
key(pan, "z", f_ret, RICHEST_DEG)
key(tilt, "x", f_ret, SWEEP_TILT)
key(nod, "x", f_ret, 0.0)
key_led(f_ret, LED_END)

# Closing hold. S4 hands to S5a (authored arrival) or straight to S5b (a plain
# transition) -- both start from this level pose at the chosen pan.
end = f_ret + HOLD_OUT_F
key(pan, "z", end, RICHEST_DEG)
key(tilt, "x", end, SWEEP_TILT)
key(nod, "x", end, 0.0)
key_led(end, LED_END)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = end

covered = 2 * SWEEP_DEG + CAM_HFOV
msg = (f"S4 sweep: {N_STATIONS} stations {start_deg:+.0f}->{end_deg:+.0f} "
       f"(step {abs(step):.1f} deg, covers {covered:.0f} deg), return LEVEL to "
       f"{RICHEST_DEG:+.0f}; {end}f ({end / FPS:.2f}s)")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text="S4 only ACQUIRES. Arriving at the thing is S5a,")
    self.layout.label(text="and S5a only runs if the target actually changed.")
    self.layout.label(text=f"Covers {covered:.0f} deg = the forward 180.")
    self.layout.label(text="Ends LEVEL at the chosen pan. Then: save, export_clip.py")


bpy.context.window_manager.popup_menu(draw, title="S4 sweep", icon='INFO')
