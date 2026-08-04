# Auto-generates S5b TRACK (3-DOF + LED). Run inside S5B_TRACK.blend after
# repair_rig.py + add_nod_joint.py. OVERWRITES all keys.
# Design rationale: ../../../robot_motion/S4_S5_DESIGN.md (local, not in this repo).
#
# S5b is a LOOP: the robot is watching the thing S5a settled onto.
#
# S5b IS MOTIONLESS, AND THAT IS THE THIRD TIME THE SAME WALL DECIDED IT.
#
#   A vigilance re-fixation was built here and REJECTED. The argument for it was
#   good ethology -- watching in animals is stillness punctuated by discrete
#   observatory movements, stares of 1-5 s, not a continuous sway -- and it is
#   the one motion shape this hardware can actually produce.
#
#   It was rejected on the viewing condition, which is the thing the ethology
#   does not cover. AMBIENT MEANS NOBODY IS WATCHING CONTINUOUSLY. The real
#   observation is a GLANCE: a single sample, not an interval.
#
#     4 fixations x 0.30 s in a 57.6 s loop = 2.1% duty.
#     A glance lands in stillness 97.9% of the time.
#
#   So the re-fixation fails the common case -- the glance still sees a
#   motionless object -- and actively hurts the rare one, because a movement that
#   erupts out of stillness with no warning is startling. Both ends worse.
#
#   THE GENERAL FORM, WHICH IS WORTH MORE THAN THE CLIP: a glance can read a
#   STATE but not a PROCESS. Motion, rhythm and breath all need duration; pose
#   and hue do not. So an ambient state must carry its meaning in POSE and HUE,
#   and may spend rhythm only on someone who is already looking.
#
#   That also means the LED breath is not what makes this state legible either --
#   a glance sees one brightness value, not a cycle. What a glance actually gets
#   is: craned forward and aimed somewhere (attending, and attending THERE), and
#   cool rather than warm (working, not idle). Two facts, both static.
#
# THE THREE ATTEMPTS, AND THE STRUCTURAL FINDING
#
#   S1  breath sway          slow enough to be ambient -> under the 8.8 deg/s
#                            smoothness floor, steps instead of moving
#   S5b body breath          smooth enough to clear it -> 10 deg peak-to-peak,
#                            84% of the whole lean, which is rocking
#   S5b re-fixation          fast enough to be smooth -> 25 deg/s, over the
#                            5.4 deg/s ambient salience ceiling, i.e. startling
#
#   THERE IS NO MOTION ON THIS BUILD THAT IS BOTH SMOOTH AND UNOBTRUSIVE. The
#   ambient layer cannot be carried by movement at all. That is not a taste
#   decision, it is what a salience ceiling BELOW a smoothness floor means, and
#   it is checkable on any other build: measure both, and if ceiling < floor,
#   your ambient layer has to give up motion too.
#
# (Kept below at zero rather than deleted -- see FIX_DEG.)
#
# WHAT WATCHING LOOKS LIKE OVER TIME, WHICH IS NOT THE SAME QUESTION.
#
#   This clip used to be perfectly motionless, with only a cool LED breath. The
#   reasoning was sound as far as it went -- a slow body breath cannot be smooth
#   on this build (a 3.6 s, 3.6 deg sway peaks at 3.1 deg/s, far under the
#   8.8 deg/s smoothness floor; to clear the floor it would need 10 deg
#   peak-to-peak, 84% of the whole lean, which is rocking, not breathing).
#
#   But "no slow sway" is not "no motion". THE FLOOR FORBIDS SLOW CONTINUOUS
#   MOVEMENT, NOT OCCASIONAL QUICK MOVEMENT -- and the ethology says quick and
#   occasional is what watching is actually made of:
#
#     vigilance bout  recurrent STATIONARY EPISODES punctuated by discrete
#                     observatory head movements
#     stare           the motionless period inside a bout, ~1-5 s in prey species
#     scanning        a looser survey, not fixed on anything in particular
#
#   So attention is not stillness and it is not sway. It is STILLNESS BROKEN BY
#   DISCRETE RE-FIXATIONS -- exactly the shape this hardware can produce, and the
#   opposite of the shape it cannot.
#
# WHY THIS IS NOT A MOVEMENT <=> RESULT VIOLATION.
#
#   A re-fixation is EPISTEMIC movement, like S4's sweep: it is not reporting a
#   detection, it is looking. Nudging a 58 deg frame by 4 deg genuinely changes
#   what is centred. At runtime the honest version is two-tier, exactly parallel
#   to S4's re-plan triggers:
#
#     the tracked box drifts off centre   -> re-fixate. A real referent.
#     nothing for STARE_S                 -> check anyway. The same "my view may
#                                            be stale" hedge as the 5 min sweep.
#
#   This clip is the second tier: the periodic one, which is what a loop can
#   carry. Tier one belongs to the player, which knows where the box is.
#
# WHICH JOINT, AND WHY IT MATTERS.
#
#   PAN ONLY. Pan and tilt are the AMBIENT layer (body); nod is the COMMUNICATION
#   layer (the "face"). A watching state must say nothing to anyone, so the head
#   stays silent and the body does the looking. Moving nod here would borrow the
#   communication layer's morpheme for an ambient job -- the same error that made
#   S5a read as "looking up at you for a response".
#
# THE COST, STATED RATHER THAN HIDDEN.
#
#   4 deg in 0.3 s peaks near 25 deg/s, well over the 5.4 deg/s ambient salience
#   ceiling. It WILL be noticed occasionally. That is acceptable here and was not
#   in S1: idle must ask for nothing, whereas S5b is working on the person's
#   behalf, and a colleague shifting at their desk is noticed without being an
#   interruption. It stays far below S7, which is green, large and fast.
#
# SIGN CONVENTION: BLENDER positive nod = head UP; on the bus a higher unit is
# DOWN. INVERT in robot/calibration.py reconciles them. Author against the render.

import bpy
import math

# ---- pose: keep in sync with generate_s4_sweep.py and generate_s5a_settle.py ----
HOLD_PAN = 25.0        # = S4 RICHEST_DEG. The re-fixations are relative to this.
HOLD_TILT = -12.0      # = S4 / S5a LEAN_TILT (neck craned forward at the target)
HOLD_NOD = 12.0        # = S4 / S5a LEAN_NOD. EXACTLY cancels the lean, so the
                       # held gaze is LEVEL: -12 + 12 = 0. Was 15 (+3) and paired
                       # with a trailing lift, which read as looking up for a
                       # response rather than as settling.

# ---- the vigilance re-fixation ----
FIX_DEG = 0.0          # DELIBERATELY ZERO. This is a decision, not a TODO.
                       #
                       # 4.0 was built and tested. Above pan's 1.76 deg floor,
                       # 25 deg/s so well clear of the smoothness floor, on the
                       # correct (body) layer, and shaped after real vigilance
                       # behaviour. Rejected on the viewing condition: at 2.1%
                       # duty it does not reach the glance that is the actual
                       # observation, and when it IS caught it erupts out of
                       # stillness. See the header.
                       #
                       # Set it back to 4.0 to render the rejected version --
                       # that comparison is one of the study conditions.
FIX_S = 0.30           # how long it takes. Fast, so it clears the 8.8 deg/s
                       # smoothness floor by a wide margin -- this is a saccade,
                       # not a sway, and the two are different behaviours rather
                       # than the same behaviour at different speeds.
STARE_S = 14.0         # the motionless period between re-fixations.
                       #
                       # THE LITERATURE GIVES THE SHAPE, NOT THIS NUMBER. Stares
                       # in prey species run 1-5 s, which on a desk object would
                       # read as busy rather than watchful. The interval is a
                       # design choice bounded from below by "must not demand
                       # attention" and from above by "must not read as crashed",
                       # and only an hour of sitting next to it can settle it.
FIX_PATTERN = (+1, -1, +1, -1)
                       # Alternating, so the robot returns to the target rather
                       # than walking off it. A random walk would drift; a
                       # repeated single direction would look like a slow turn.
                       # Four entries = the loop is 4 stares long before repeating.

# ---- breath ----
BREATH_S = 3.6         # one full LED breath. Slower than a resting human (~4 s)
                       # so it reads as calm rather than expectant. Runs
                       # INDEPENDENTLY of the re-fixations: the light is the
                       # ambient clock, the body is the epistemic one, and letting
                       # them drift apart is what stops the pair reading as one
                       # mechanism ticking.
LED_LO = 0.8           # trough. Never 0 -- it should not look switched off.
LED_HI = 3.0           # crest. Matches S4's and S5a's level so the handovers have
                       # no brightness step.
LED_COOL = (0.38, 0.72, 1.00, 1.0)

FPS = 30
SAMPLE_F = 2
# -----------------------------------------------------------

FIX_F = int(round(FIX_S * FPS))
BREATH_F = int(round(BREATH_S * FPS))
N_FIX = len(FIX_PATTERN)

# The loop must hold a WHOLE number of breaths or the light steps at the seam,
# and it must hold a whole number of stare+fixation cycles or the body does.
# Solve for the stare rather than fudging the breath: the breath period is a
# designed value (calm, slower than a resting human) while the stare interval is
# already an admitted guess, so it is the one that should absorb the rounding.
_want = int(round(STARE_S * FPS))
_k = max(1, round(N_FIX * (_want + FIX_F) / float(BREATH_F)))
while (BREATH_F * _k) % N_FIX:          # the loop must divide into N_FIX cycles
    _k += 1
STARE_F = BREATH_F * _k // N_FIX - FIX_F
CYCLE_F = STARE_F + FIX_F
END_F = N_FIX * CYCLE_F
assert END_F % BREATH_F == 0, "breath does not divide the loop"

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
print("[s5b] " + reach.summary())

if abs(HOLD_TILT + HOLD_NOD) > 0.01:
    raise RuntimeError(f"gaze is not level: tilt+nod = {HOLD_TILT + HOLD_NOD:+.1f}. "
                       f"This pose is HELD FOR MINUTES, so the camera frame must "
                       f"be right; set HOLD_NOD = -HOLD_TILT.")
for _j, _v, _w in (("pan", HOLD_PAN + FIX_DEG, "fixation +"),
                   ("pan", HOLD_PAN - FIX_DEG, "fixation -"),
                   ("tilt", HOLD_TILT, "craned"), ("nod", HOLD_NOD, "counter"),
                   ("pan", HOLD_PAN, "held")):
    reach.check(_j, _v, _w)
_pk = FIX_DEG / FIX_S * 1.875 if FIX_DEG else 0.0
if FIX_DEG:
    reach.check_floor("pan", FIX_DEG, "re-fixation")
    if _pk < 8.8:
        raise RuntimeError(f"the re-fixation peaks at {_pk:.1f} deg/s, under the "
                           f"8.8 deg/s smoothness floor -- it would step rather "
                           f"than move. Shorten FIX_S or enlarge FIX_DEG.")
    if _pk > 200.0:
        raise RuntimeError(f"the re-fixation peaks at {_pk:.0f} deg/s, over the "
                           f"200 deg/s ceiling.")
    print(f"[s5b] !! FIX_DEG is not zero: this is the REJECTED variant "
          f"({_pk:.0f} deg/s, over the 5.4 deg/s ambient ceiling). Fine for the "
          f"study comparison, not for the shipped clip.")

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
    led_color.default_value = LED_COOL
    mat.diffuse_color = LED_COOL


def key_led(frame, value):
    if led_strength is not None:
        led_strength.default_value = value
        led_strength.keyframe_insert(data_path="default_value", frame=frame)


def ease(t):
    """minjerk -- Flash & Hogan 1985. See generate_s2_listen.py."""
    t = max(0.0, min(1.0, t))
    return t * t * t * (10.0 + t * (-15.0 + 6.0 * t))


# The head does not move at all: tilt and nod are keyed at the ends only, so the
# exporter samples a constant. Only the BODY looks around.
for f in (1, END_F):
    key(tilt, "x", f, HOLD_TILT)
    key(nod, "x", f, HOLD_NOD)

# Pan: hold, then a quick re-fixation, N times. Keys are sparse through the
# stares -- there is nothing to sample when nothing is moving -- and dense
# through each fixation so the ease curve survives export.
offset = 0.0
f = 1
key(pan, "z", f, HOLD_PAN + offset)
for i, direction in enumerate(FIX_PATTERN):
    f_start = f + STARE_F
    key(pan, "z", f_start, HOLD_PAN + offset)      # end of the stare
    target = offset + direction * FIX_DEG
    for k in range(1, FIX_F + 1):
        key(pan, "z", f_start + k,
            HOLD_PAN + offset + (target - offset) * ease(k / float(FIX_F)))
    offset = target
    f = f_start + FIX_F
key(pan, "z", END_F, HOLD_PAN + offset)

if abs(offset) > 0.01:
    raise RuntimeError(f"the fixation pattern does not return to centre "
                       f"(ends {offset:+.1f} deg off). A loop that drifts walks "
                       f"the robot off its target; make FIX_PATTERN sum to zero.")

# Breath, sampled from a raised cosine. Starting and ending at the trough means
# value AND slope match across the loop seam, so --loop has no visible hitch.
for f in range(1, END_F + 1, SAMPLE_F):
    phase = ((f - 1) % BREATH_F) / float(BREATH_F)
    key_led(f, LED_LO + (LED_HI - LED_LO) * 0.5 * (1.0 - math.cos(2.0 * math.pi * phase)))
key_led(END_F, LED_LO)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = END_F

_motion = ("STILL -- pose and hue carry it" if not FIX_DEG else
           f"{N_FIX} x {FIX_DEG:.0f} deg re-fixations, peak {_pk:.0f} deg/s "
           f"(REJECTED VARIANT)")
msg = (f"S5b track: hold {HOLD_PAN:.0f}/{HOLD_TILT:.0f}/{HOLD_NOD:.0f}, gaze level; "
       f"{_motion}; LED breath {BREATH_F / FPS:.2f}s; "
       f"{END_F}f ({END_F / FPS:.1f}s loop)")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text="A GLANCE reads a STATE, not a PROCESS. Motion,")
    self.layout.label(text="rhythm and breath all need duration; pose and hue")
    self.layout.label(text="do not. So ambient meaning lives in POSE and HUE.")
    self.layout.label(text="What a glance gets: craned forward and aimed (attending,")
    self.layout.label(text="and THERE), and cool rather than warm (working).")
    self.layout.label(text="FIX_DEG=4.0 renders the rejected vigilance variant.")


bpy.context.window_manager.popup_menu(draw, title="S5b track", icon='INFO')
