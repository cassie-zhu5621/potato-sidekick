# Auto-generates S7a FOUND (one-shot, 3-DOF + LED). Run inside S7a.blend after
# repair_rig.py + add_nod_joint.py. OVERWRITES all keys.
# Design rationale: ../../../robot_motion/S7_DESIGN.md (local, not in this repo).
#
# Sequence: NOTICE (lean deeper onto the finding) -> ATTENTION-GET (turn to the
# user, eye contact, no lean) -> DIRECT (turn back to the finding, crane at IT)
# -> HOLD. Beats 1-2 of Mundy's initiating-joint-attention triple; S7b carries 3.
#
# v4 (2026-08-03). Two structural changes.
#
# 1. THE CLIP NOW STARTS WHERE THE ROBOT ACTUALLY IS.
#
#    v3 opened at 0/0/0 while S5b holds pan/-12/+12. Two consequences, and the
#    second is a design failure rather than a defect:
#
#      - ~0.2 s of UNAUTHORED travel at the top of the announcement, and 25 deg
#        of pan that the clip depicted itself as not having travelled. The turn
#        to the user was authored as 60 deg when the real journey is 85.
#      - THE ANTICIPATION BEAT WAS INVERTED. Beat 1 is the detection registering
#        before the body acts on it, and its comment said "the head is still on
#        the watched region -- S5 left it there." It was not. The crouch ran
#        tilt 0 -> -8 while S5b had already been at -12, so the dip was a NET
#        RISE OF 4 DEG: the beat that means "I saw that" was performed as
#        straightening up away from the thing seen.
#
#    Same fault class as S6 v3 (see S6_DESIGN.md Sec.2.1) and it is now a library
#    rule: A CLIP INHERITS THE POSE OF THE STATE IT IS ENTERED FROM, AND ANY
#    DEVIATION FROM THAT IS AUTHORED, NOT INTERPOLATED.
#
# 2. THE NOTICE IS NOW A DEEPENING, AND IT HAS ITS OWN MORPHEME.
#
#    The neck leans FURTHER IN and the head does NOT compensate, so the gaze
#    drops from the watched region onto the object itself. Leaning in without
#    levelling is "looking closer" -- and it is exactly one contrast away from
#    S5a, where the two pitch joints run in exact opposition so the gaze does not
#    move at all. Same joints, opposite compensation, two different claims:
#
#      S5a  lean + cancel  ->  gaze fixed  ->  ARRIVING at a region
#      S7a  lean, no cancel ->  gaze drops  ->  LOCKING ON to a thing in it
#
#    It is also the right layer. The notice is EPISTEMIC -- the robot registering
#    something about the world, addressed to nobody -- so by the two-layer rule
#    the body moves and the head is passive. The social beat starts afterwards,
#    with the turn, and that one moves the gaze. The two are not confusable.
#
# THE OBJECT'S BEARING AND S5b's HELD PAN ARE THE SAME QUANTITY.
#
#    Not two numbers that happen to agree: the finding was found in the region
#    the robot was watching. OBJECT_PAN is therefore the pan S5b was holding, and
#    the clip opens there. Both are templates -- the runtime remap supplies the
#    truth -- but they must be remapped TOGETHER or the clip will turn away from
#    the object in order to announce it.
#
# ! STILL OPEN, AND IT IS THE BIG ONE: the two-point remap that S7_DESIGN.md
#   Sec.5 specifies IS NOT IMPLEMENTED. clip_player clears _pan_override on
#   entering S7a and nothing supplies one, so on hardware this clip plays at the
#   TEMPLATE angles no matter where the person or the finding is. The remove-it
#   test that v3 applied to v2 -- "delete the object and the robot performs
#   exactly the same sequence" -- still passes at runtime. Authoring cannot fix
#   this; it is a player change.
#
# The final pose IS S7b's frame 1. Both files hold the object leg; keep them in
# sync or the loop will jump on entry.
#
# SIGN CONVENTION: BLENDER positive nod = head UP; on the bus a higher unit is
# DOWN. INVERT in robot/calibration.py reconciles them. Author against the render.

import bpy
import math

# ---- inherited from S5b (its held pose) ----
HOLD_TILT = -12.0     # = generate_s5b_track.py HOLD_TILT
HOLD_NOD = 12.0       # = generate_s5b_track.py HOLD_NOD. Cancels exactly, so the
                      # inherited gaze is LEVEL: watching a region, not a thing.

# ---- keep in sync with generate_s7_beckon.py ----
OBJECT_PAN = -25.0    # == the pan S5b was holding. A TEMPLATE: the player
                      # translates the whole clip onto the real finding angle.

# ---- the finding ----
OBJECT_ELEV = 0.0     # WAS -10, and that number was never measured -- it was
                      # authored as "a desk is below". The finding fires INSIDE
                      # S5b's own view, so it sits at S5b's gaze, which is level.
                      # The -10 put S7's whole resting posture ten degrees below
                      # the watching one, and since the camera rides on the head
                      # that is a third of the vertical frame: the storyboard cut
                      # between its opening panel and every panel taken during
                      # S7's rests. Reported 2026-08-08.
LEAN_DEG = -12.0      # = S5b's HOLD_TILT. THE REST POSE OF S7 IS THE WATCHING
                      # POSE, so the viewpoint never changes between noticing
                      # and reporting, and the strip is one continuous shot.
                      #
                      # WAS -22, held, and that ten-degree difference was the
                      # epistemic escalation -- "I am watching" vs "I have found
                      # it". It has not been dropped, it has MOVED: the escalation
                      # is now the PUSH, a transient toward the finding, rather
                      # than a posture held while the camera is trying to record
                      # what it found. Admoni HRI'13 already argued the same way
                      # for S7b's train of accents over a single held stare; this
                      # applies it to S7a as well, so the whole of S7 is one
                      # gesture repeated rather than a posture plus a gesture.
                      #
                      # THE COST, NAMED: a still photograph of S7 between pushes
                      # is indistinguishable from S5b watching. The difference
                      # lives in motion, in the LED (summon), and in the sound.
OBJECT_NOD = OBJECT_ELEV - LEAN_DEG   # cancel the lean's pitch, THEN aim.

# ---- v5: THE ATTENTION-GET IS GONE ----
#
# Beat 2 used to turn 89 degrees to USER_PAN, hold eye contact, and come back.
# It is removed for the same reason S7b's alternation was (see
# generate_s7_beckon.py), and for one more that only showed up on hardware.
#
# THE PREMISE WAS NEVER TRUE. The participant places the robot anywhere on the
# desk and nothing measures where they then sit, so the turn went to an authored
# template -- an empty corner, performed with full confidence, in the clip whose
# entire job is to be believed.
#
# AND THE CAMERA IS ON THE HEAD. That turn took the eye off the event for 3.4 of
# S7a's 4.6 s, at exactly the moment the storyboard opens. Measured on a saved
# strip (e2e_20260805_155419): panel 1 blurred mid-turn, panel 2 the participant
# looking into the lens BECAUSE THE ROBOT HAD TURNED TO HER, panel 3 a wall. The
# narration was then written from panel 2 -- "a woman looking towards the
# camera" -- so the record of what the robot noticed had become a record of
# someone reacting to the robot. With pan held, the finding stays in frame
# through S7a and S7b, and the story can keep watching while the robot performs.
#
# WHAT IS GIVEN UP, plainly: the robot no longer addresses the person at any
# point in S7. Beats 1-2 of Mundy's initiating-joint-attention triple are now
# beat 1 alone; "look at me, then look at this" is just "look at this". The
# cue to look up is carried by the sound (`excited`, at frame 1), the LED going
# to summon, and the visible deepening of the lean -- none of which need to know
# where anyone is. Whether that is enough is the one thing a dry run answers and
# no amount of authoring does.
#
# The clip is short on purpose. It says "I have seen something" and hands over;
# S7b, which loops, is where the insisting happens. A long preamble in front of
# a loop is time the loop then has to fill.

# ---- the notice: ONE PUSH, the same one S7b then repeats ----
#
# v6. The notice used to be a CHANGE OF RESTING POSTURE -- sink from S5b's -12
# to -22 and stay there. That posture is what the storyboard then photographed
# for the rest of the loop, ten degrees below its own opening panel, and the
# strip cut between them.
#
# So the escalation moved from the posture to the MOTION. The clip rests where
# S5b rests, pushes once toward the finding, and comes back. S7b then repeats
# that push three times per loop, so the whole of S7 is one gesture said once
# and then insisted on -- rather than a posture plus a different gesture.
#
# Same depth and the same rhythm as S7b's pushes, deliberately: the first push
# and the ones that follow it are the same word, and authoring them from two
# sets of numbers is how they would drift into two.
PUSH_DEG = 12.0       # = generate_s7_beckon.BOB_DEG. -12 -> -24.
PUSH_S = 0.23         # sharp: this is the accent
BOTTOM_S = 0.13       # "see, feel, react" -- Invisible Strings P4. Acting
                      # without first being seen to have seen reads as
                      # distracted.
RETURN_S = 0.50       # slow, eased. Quick out and slow back is what separates
                      # "look there" from a nod; get it backwards and the notice
                      # reads as agreement.

# ---- boundaries ----
HOLD_IN_S = 0.20      # S5b's pose, held so the push has something to leave FROM.
HOLD_OUT_S = 0.67     # the held fixation that ends the clip, continued by S7b's
                      # opening hold -- the same pose, so the two run together.

# ---- LED ----
LED_ENTER = 2.0       # = S5b's breath midpoint, so the entry does not jump
LED_CALL = 8.0        # 255 -- on the bottom of the dive. This accent used to
                      # sit on the eye contact; with no eye contact it belongs
                      # on the only event left, which is the lock-on.
LED_HOLD = 4.0        # 127/255 -- lit, not an event. MUST equal S7b's opening
                      # value or the handover blinks.
LED_EDGE_S = 0.13
LED_COLOR = (1.00, 0.75, 0.00, 1.0)   # = SUMMON (45/100/100), matching S7b.

# ---- speed ----
PEAK_DPS = 120.0
PEAK_FACTOR = 1.875
EASE_MODE = "minjerk"  # Flash & Hogan 1985 -- see generate_s2_listen.py.
FPS = 30
SAMPLE_F = 1

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
print("[s7a] " + reach.summary())

# --- structural assertions: the things that silently stop meaning anything ---
if abs(HOLD_TILT + HOLD_NOD) > 0.01:
    raise RuntimeError(
        f"the inherited gaze is not level: tilt+nod = {HOLD_TILT + HOLD_NOD:+.1f}. "
        f"S7a opens on S5b's hold, so these must match generate_s5b_track.py.")
if abs(LEAN_DEG - HOLD_TILT) > 0.01 or abs(OBJECT_NOD - HOLD_NOD) > 0.01:
    raise RuntimeError(
        f"S7's rest pose ({LEAN_DEG:+.1f}/{OBJECT_NOD:+.1f}) is not S5b's "
        f"({HOLD_TILT:+.1f}/{HOLD_NOD:+.1f}).\n"
        f"  THE v4 RULE HERE WAS THE OPPOSITE: it required the crane to be "
        f"VISIBLY DEEPER than the watching posture, because that difference was "
        f"the epistemic escalation.\n"
        f"  v6 moves the escalation into the PUSH and requires the rest poses "
        f"to be identical instead. The camera rides on the head, so any standing "
        f"difference between watching and reporting is a cut in the middle of "
        f"the storyboard's own strip -- ten degrees of it, a third of the "
        f"vertical frame, reported 2026-08-08.\n"
        f"  If you are re-introducing a deeper held pose, the storyboard's "
        f"POSE_TOL_DEG will start refusing every panel taken during S7.")
if abs((LEAN_DEG - PUSH_DEG) + (OBJECT_NOD + PUSH_DEG) - OBJECT_ELEV) > 0.01:
    raise RuntimeError(
        f"the push drags the gaze off the finding. nod must rise by exactly the "
        f"PUSH_DEG that tilt falls, or the robot lunges past what it is "
        f"pointing at.")

def frames_for(deg, floor=4):
    """Frames needed to travel `deg` with the MINJERK peak under PEAK_DPS."""
    return max(floor, int(math.ceil(abs(deg) * PEAK_FACTOR / PEAK_DPS * FPS)))


HOLD_IN_F = int(round(HOLD_IN_S * FPS))
PUSH_F = max(int(round(PUSH_S * FPS)), frames_for(PUSH_DEG))
BOTTOM_F = int(round(BOTTOM_S * FPS))
RETURN_F = int(round(RETURN_S * FPS))
HOLD_OUT_F = int(round(HOLD_OUT_S * FPS))
LED_EDGE_F = int(round(LED_EDGE_S * FPS))

# beat boundaries, in frames, cumulative from 1
F_IN = 1 + HOLD_IN_F                   # the push starts
F_DEEP = F_IN + PUSH_F                 # deepest point
F_BOTTOM = F_DEEP + BOTTOM_F           # the beat held there
F_BACK = F_BOTTOM + RETURN_F           # home, on S5b's pose again
END_F = F_BACK + HOLD_OUT_F

if abs(LEAN_DEG - HOLD_TILT) > 0.01:
    raise RuntimeError(
        f"the rest pose is {LEAN_DEG:+.1f} but S5b holds {HOLD_TILT:+.1f}. THE "
        f"WHOLE POINT OF v6 is that they are the same: the camera is on the "
        f"head, so any difference is a cut in the middle of the storyboard's "
        f"own strip.")
if RETURN_F <= PUSH_F:
    raise RuntimeError(
        "the return must be slower than the push, or the accent lands on the "
        "way home and the notice reads as a nod rather than as noticing.")
if abs((LEAN_DEG + OBJECT_NOD) - OBJECT_ELEV) > 0.01:
    raise RuntimeError(
        f"the clip does not aim at the object: gaze {LEAN_DEG + OBJECT_NOD:+.1f} "
        f"vs OBJECT_ELEV {OBJECT_ELEV:+.1f}.")
if HOLD_OUT_F < 14:
    raise RuntimeError(
        f"HOLD_OUT_F {HOLD_OUT_F} < S7b's opening hold (14 f). The two run "
        f"together as one fixation; if this end is the shorter the handover "
        f"reads as the robot losing interest in what it just found.")

for _j, _v, _w in (("pan", OBJECT_PAN, "held all clip"),
                   ("tilt", LEAN_DEG, "the rest pose = S5b's"),
                   ("tilt", LEAN_DEG - PUSH_DEG, "the bottom of the push"),
                   ("nod", OBJECT_NOD, "the rest pose = S5b's"),
                   ("nod", OBJECT_NOD + PUSH_DEG, "counter-rotated at the bottom")):
    reach.check(_j, _v, _w)
for _j, _d, _w in (("tilt", PUSH_DEG, "the push, neck"),
                   ("nod", PUSH_DEG, "the push, head -- the counter-rotation")):
    reach.check_floor(_j, _d, _w)

_FAC = math.pi / 2.0 if EASE_MODE == "cosine" else 1.875
for _w, _deg, _f in (("the push, neck", PUSH_DEG, PUSH_F),
                     ("the push, head", PUSH_DEG, PUSH_F),
                     ("the return", PUSH_DEG, RETURN_F)):
    _pk = abs(_deg) / (_f / float(FPS)) * _FAC
    if _pk > PEAK_DPS:
        raise RuntimeError(
            f"{_w} peaks at {_pk:.0f} deg/s, over PEAK_DPS {PEAK_DPS:.0f}.")

pan = bpy.data.objects["pan_pivot"]
tilt = bpy.data.objects["tilt_pivot"]
nod = bpy.data.objects.get("nod_pivot")
if not nod:
    raise RuntimeError("Run add_nod_joint.py first -- this clip needs the 3rd DOF.")

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
# Colour set here as well as on the CoreS3 (states.py `hue`), so the render and
# the robot agree -- a render whose light says something different from the
# hardware quietly invalidates the comparison the renders exist for.
if led_color is not None:
    led_color.default_value = LED_COLOR
    mat.diffuse_color = LED_COLOR


def key_led(frame, value):
    if led_strength is not None:
        led_strength.default_value = value
        led_strength.keyframe_insert(data_path="default_value", frame=frame)


def ease(t):
    t = max(0.0, min(1.0, t))
    if EASE_MODE == "cosine":
        return 0.5 * (1.0 - math.cos(math.pi * t))
    return t * t * t * (10.0 + t * (-15.0 + 6.0 * t))


def track(points, f):
    """Value at frame `f` on a waypoint list, eased between consecutive points.
    Sampling every frame from ONE schedule per joint is what replaces v3's sparse
    keys: the curve is authored here rather than left to whatever Blender's
    default interpolator happens to do between them."""
    if f <= points[0][0]:
        return points[0][1]
    for (f0, v0), (f1, v1) in zip(points, points[1:]):
        if f <= f1:
            if f1 == f0:
                return v1
            return v0 + (v1 - v0) * ease((f - f0) / float(f1 - f0))
    return points[-1][1]


# One schedule per joint. PAN IS A SINGLE VALUE: the clip never turns, so the
# whole of the retargeting is a translation onto the real finding angle.
PAN = [(1, OBJECT_PAN), (END_F, OBJECT_PAN)]

TILT = [(1, LEAN_DEG), (F_IN, LEAN_DEG),                         # = S5b's pose
        (F_DEEP, LEAN_DEG - PUSH_DEG),                           # push at it
        (F_BOTTOM, LEAN_DEG - PUSH_DEG),                         # the beat
        (F_BACK, LEAN_DEG), (END_F, LEAN_DEG)]                   # home, held

# EQUAL AND OPPOSITE, so tilt+nod holds at OBJECT_ELEV for the whole push: the
# creature lunges, the look does not leave the finding. Identical to S7b's
# pushes -- the same word, said once here and repeated there.
NOD = [(1, OBJECT_NOD), (F_IN, OBJECT_NOD),
       (F_DEEP, OBJECT_NOD + PUSH_DEG), (F_BOTTOM, OBJECT_NOD + PUSH_DEG),
       (F_BACK, OBJECT_NOD), (END_F, OBJECT_NOD)]

LED = [(1, LED_ENTER), (F_IN, LED_ENTER),
       (F_DEEP, LED_CALL), (F_BOTTOM, LED_CALL),                 # on the bottom
       (F_BOTTOM + LED_EDGE_F, LED_HOLD),
       (F_BACK, LED_HOLD), (END_F, LED_HOLD)]                    # = S7b's open

for f in range(1, END_F + 1, SAMPLE_F):
    key(pan, "z", f, track(PAN, f))
    key(tilt, "x", f, track(TILT, f))
    key(nod, "x", f, track(NOD, f))
    key_led(f, track(LED, f))

if (END_F - 1) % SAMPLE_F:
    key(pan, "z", END_F, OBJECT_PAN)
    key(tilt, "x", END_F, LEAN_DEG)
    key(nod, "x", END_F, OBJECT_NOD)
    key_led(END_F, LED_HOLD)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = END_F

_pk = PUSH_DEG / (PUSH_F / float(FPS)) * _FAC

# states.py fires 'excited' at 0.0. With the turn gone the sound is doing more
# work than it used to -- it is the one channel that reaches someone reading a
# comic without asking them to already be looking -- so it stays at frame 1,
# ahead of the dive it announces.
msg = (f"S7a found v6: pan HELD at {OBJECT_PAN:+.0f}; RESTS ON S5b's POSE "
       f"({LEAN_DEG:+.0f}/{OBJECT_NOD:+.0f}), ONE push of {PUSH_DEG:.0f} deg "
       f"to {LEAN_DEG - PUSH_DEG:+.0f}, gaze locked at {OBJECT_ELEV:+.0f}, home "
       f"-> holds {HOLD_OUT_F}f; {END_F}f ({END_F / FPS:.2f}s), peak "
       f"{_pk:.0f} deg/s. No attention-get; rest pose = watching pose.")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text="v5: the 89-degree turn to the user is GONE.")
    self.layout.label(text="Placement is arbitrary, so it addressed a template;")
    self.layout.label(text="and the camera is on the head, so it took the eye")
    self.layout.label(text="off the event for 3.4s while the story was opening.")
    self.layout.label(text=f"ENDS on pan {OBJECT_PAN:.0f} / tilt {LEAN_DEG:.0f} "
                           f"/ nod {OBJECT_NOD:.0f}")
    self.layout.label(text="S7b must OPEN on that exact pose.")
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(draw, title="S7a found v5", icon='INFO')
