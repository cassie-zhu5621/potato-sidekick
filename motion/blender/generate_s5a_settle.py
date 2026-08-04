# Auto-generates S5a SETTLE (3-DOF + LED). Run inside S5A_SETTLE.blend after
# repair_rig.py + add_nod_joint.py. OVERWRITES all keys.
# Design rationale: ../../../robot_motion/S4_S5_DESIGN.md (local, not in this repo).
#
# S5a is the ARRIVAL: coming to rest on a thing the robot has just chosen to
# watch. "I have come to this one, and now I am looking at it."
#
# IT ONLY RUNS WHEN THE TARGET ACTUALLY CHANGED.
#
#   The periodic sweep is additive, so it often changes nothing, and a robot that
#   performed "I have chosen!" every five minutes about the object it was already
#   watching would be making a movement whose result did not change.
#   NO RESULT, NO MOVEMENT.
#
#   That limit case is expressed here by NOT ENTERING THE STATE at all, rather
#   than by a branch inside a clip:
#
#     target changed  ->  S4 -> S5a -> S5b   the crane is an AUTHORED BEAT
#     target same     ->  S4 ->        S5b   the crane is a TRANSITION, which by
#                                            the library rule carries no
#                                            expressive content and merely
#                                            travels between two held poses
#
#   Same joints, same endpoints, two entirely different claims. What separates
#   them is whether anyone authored the move.
#
# A BEAT THAT IS LOSSLESS IF MISSED.
#
#   S5a fires autonomously, potentially every few minutes, and a state that
#   demands attention on that schedule would be intolerable. So it is designed to
#   be READ IF SEEN AND LOST WITHOUT COST IF NOT: whatever S5a says, S5b's steady
#   pose says too, because the direction it settles into is the direction it then
#   holds. Compare S7, which exists precisely to demand attention.
#
#   That is why there is no LED accent here. The move is available, not
#   addressed.
#
# AND THE GAZE NEVER MOVES.
#
#   The first version craned forward while the head lifted AFTERWARDS, and it
#   read as "looking up at you for a response" rather than as settling. The cause
#   was the ORDER, not the size: a gaze that dips and then rises LAST is S2's
#   contact structure, so copying the timing imported the meaning.
#
#   Here the two pitch joints run one schedule in exact opposition, so tilt + nod
#   is identically zero on every frame. The head's orientation in the world does
#   not change at all; it is only carried forward and down by the neck. That also
#   keeps the camera frame right, which matters because S5b holds this pose for
#   minutes at a time.
#
#     SOCIAL states    -- the gaze moves, the body supports it   (S2)
#     EPISTEMIC states -- the body moves, the gaze holds         (S5a)
#
#   Stated that way the two states are grammatical mirrors, which is exactly what
#   stops S5a being read as S2.
#
# SIGN CONVENTION: BLENDER positive nod = head UP; on the bus a higher unit is
# DOWN. INVERT in robot/calibration.py reconciles them. Author against the render.

import bpy
import math

# ---- inherited from S4 (its closing pose) ----
TARGET_PAN = 25.0      # = S4 RICHEST_DEG. At runtime both come from the VLM's
                       # chosen station, so this is a template, not a decision.
START_TILT = 0.0       # = S4 SWEEP_TILT. S4 ends LEVEL and never cranes.
START_NOD = 0.0

# ---- handed to S5b (its held pose) ----
LEAN_TILT = -12.0      # = S5B_TRACK HOLD_TILT. The crane: coming forward at the
                       # thing. An EPISTEMIC lean -- toward an object in order to
                       # see it -- which is the move S7_DESIGN sec 2 marks as the
                       # novel one, as against a social lean into a person.
LEAN_NOD = 12.0        # = S5B_TRACK HOLD_NOD. EXACTLY cancels the lean, so the
                       # gaze is level: -12 + 12 = 0. The head's orientation in
                       # the world never changes at all -- it is only carried
                       # forward and down by the neck.
                       #
                       # WAS 15 (gaze +3) with the nod TRAILING the crane, and
                       # that read as "looking up at you for a response" rather
                       # than as settling. The cause was not the size of the lift
                       # but its ORDER: a gaze that dips and then rises LAST is
                       # S2's contact structure, and copying the timing imported
                       # the meaning. Kip1 (Hoffman et al., HRI '15) is explicit
                       # about the vocabulary -- its CURIOUS gesture is literally
                       # "stretches out towards the conversant ... and raises its
                       # head upwards", while its CALM state lives at the LOWER
                       # EDGE of the movement range.
                       #
                       # Hence the rule this clip now states, which is the exact
                       # inverse of S2:
                       #   SOCIAL states  -- the gaze moves, the body supports it
                       #   EPISTEMIC states -- the body moves, the gaze holds

# ---- the settle ----
# A settle is not an arrival at a pose, it is the RESIDUE of a movement: mass
# going slightly past and relaxing back. So the clip overshoots and decays.
#
# OVERSHOOT_DEG IS SET BY THE AMPLITUDE FLOOR, NOT BY TASTE. Both joints must be
# able to express the relax, or the gaze drifts during it: tilt's floor is 2.05
# deg and nod's is 3.52, so a 3 deg overshoot would move the neck and NOT the
# head, tipping the camera 3 deg on the way to rest. 4 deg is the smallest
# settle this build can actually perform.
OVERSHOOT_DEG = 4.0
APPROACH_S = 0.50      # out to the overshoot
RELAX_S = 0.35         # and back. Slower than it needs to be, because decay is
                       # what "coming to rest" looks like; a symmetric return
                       # would read as a small second move.
HOLD_IN_S = 0.20       # library boundary holds
HOLD_OUT_S = 0.20

# ---- LED ----
# FLAT. S5a announces nothing: the light is the channel that reports results, and
# choosing where to look is not yet a result. It sits at S5b's breath trough so
# the handover has no step, and S5b's breath simply takes over.
LED_LEVEL = 0.8

EASE_MODE = "minjerk"  # Flash & Hogan 1985 -- see generate_s2_listen.py
FPS = 30
SAMPLE_F = 2
# -----------------------------------------------------------

HOLD_IN_F = int(round(HOLD_IN_S * FPS))
HOLD_OUT_F = int(round(HOLD_OUT_S * FPS))
APPROACH_F = int(round(APPROACH_S * FPS))
RELAX_F = int(round(RELAX_S * FPS))
MOVE_F = APPROACH_F + RELAX_F
END_F = HOLD_IN_F + MOVE_F + HOLD_OUT_F

# ---- guard rails, read from the live calibration ----
import os
import sys

REACH_PATH = ""


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

reach = type(sys)("reach")
reach.__file__ = _rp
with open(_rp) as _fh:
    exec(compile(_fh.read(), _rp, "exec"), reach.__dict__)
print("[s5a] " + reach.summary())

if abs((LEAN_TILT + LEAN_NOD) - (START_TILT + START_NOD)) > 0.01:
    raise RuntimeError(f"gaze is not held: tilt+nod goes "
                       f"{START_TILT + START_NOD:+.1f} -> {LEAN_TILT + LEAN_NOD:+.1f}. "
                       f"S5b holds this pose for minutes, so the camera frame "
                       f"must be right; set LEAN_NOD = -LEAN_TILT.")
for _j, _v, _w in (("pan", TARGET_PAN, "target"),
                   ("tilt", START_TILT, "level"), ("tilt", LEAN_TILT, "craned"),
                   ("tilt", LEAN_TILT - OVERSHOOT_DEG, "overshoot"),
                   ("nod", START_NOD, "level"), ("nod", LEAN_NOD, "held"),
                   ("nod", LEAN_NOD + OVERSHOOT_DEG, "overshoot")):
    reach.check(_j, _v, _w)
for _j, _d, _w in (("tilt", LEAN_TILT - START_TILT, "crane"),
                   ("nod", LEAN_NOD - START_NOD, "counter-rotation"),
                   ("tilt", OVERSHOOT_DEG, "settle relax, neck"),
                   ("nod", OVERSHOOT_DEG, "settle relax, head")):
    reach.check_floor(_j, _d, _w)
_FAC = math.pi / 2.0 if EASE_MODE == "cosine" else 1.875
for _w, _d, _t in (("approach tilt", LEAN_TILT - OVERSHOOT_DEG - START_TILT, APPROACH_S),
                   ("approach nod", LEAN_NOD + OVERSHOOT_DEG - START_NOD, APPROACH_S)):
    _pk = abs(_d) / _t * _FAC
    if _pk > 200.0:
        raise RuntimeError(f"{_w} peaks at {_pk:.0f} deg/s under EASE_MODE="
                           f"{EASE_MODE!r}, over the 200 deg/s ceiling.")

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
    led_color.default_value = (0.38, 0.72, 1.00, 1.0)     # = S5b LED_COOL
    mat.diffuse_color = (0.38, 0.72, 1.00, 1.0)


def key_led(frame, value):
    if led_strength is not None:
        led_strength.default_value = value
        led_strength.keyframe_insert(data_path="default_value", frame=frame)


def ease(t):
    t = max(0.0, min(1.0, t))
    if EASE_MODE == "cosine":
        return 0.5 * (1.0 - math.cos(math.pi * t))
    return t * t * t * (10.0 + t * (-15.0 + 6.0 * t))


def lerp(a, b, t):
    return a + (b - a) * t


for f in range(1, END_F + 1, SAMPLE_F):
    i = (f - 1) - HOLD_IN_F
    t_app = ease(i / float(APPROACH_F))
    t_rel = ease((i - APPROACH_F) / float(RELAX_F)) if i > APPROACH_F else 0.0

    # ONE schedule drives both pitch joints, which is what guarantees the gaze
    # cannot drift: whatever the neck does, the head does the exact opposite on
    # the same curve. tilt + nod is identically zero on every frame, overshoot
    # included.
    over_t = LEAN_TILT - OVERSHOOT_DEG
    over_n = LEAN_NOD + OVERSHOOT_DEG
    tilt_deg = lerp(lerp(START_TILT, over_t, t_app), LEAN_TILT, t_rel)
    nod_deg = lerp(lerp(START_NOD, over_n, t_app), LEAN_NOD, t_rel)

    key(pan, "z", f, TARGET_PAN)                       # pan does not move here
    key(tilt, "x", f, tilt_deg)
    key(nod, "x", f, nod_deg)
    key_led(f, LED_LEVEL)

# Land exactly on S5b's held pose. A handover, not a loop seam.
key(pan, "z", END_F, TARGET_PAN)
key(tilt, "x", END_F, LEAN_TILT)
key(nod, "x", END_F, LEAN_NOD)
key_led(END_F, LED_LEVEL)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = END_F

msg = (f"S5a settle: neck {START_TILT:+.0f} -> {LEAN_TILT - OVERSHOOT_DEG:+.0f} "
       f"-> {LEAN_TILT:+.0f}, head counter-rotating exactly; GAZE HELD at "
       f"{START_TILT + START_NOD:+.0f} throughout; {END_F}f ({END_F / FPS:.2f}s)")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text="ONLY plays when the target CHANGED. Unchanged ->")
    self.layout.label(text="S4 hands straight to S5b and the re-crane is a")
    self.layout.label(text="plain transition. No result, no movement.")
    self.layout.label(text="Gaze NEVER moves: the neck settles, the head holds")
    self.layout.label(text="the frame. Inverse of S2, where the gaze arrives last.")
    self.layout.label(text="Opens on S4's close; ends on S5b's held pose.")


bpy.context.window_manager.popup_menu(draw, title="S5a settle", icon='INFO')
