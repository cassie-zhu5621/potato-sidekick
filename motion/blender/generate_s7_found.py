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
# Both pans are TEMPLATES; the player is meant to retarget them at runtime -- see
# S7_DESIGN.md Sec.5 and the warning above. The object leg's pan IS the
# detection, which is what makes this clip satisfy movement <=> result.
OBJECT_PAN = -25.0    # == the pan S5b was holding. Signed opposite the user so
                      # the authored transit is a real crossing rather than a
                      # nudge -- which is a STUDY-SETUP choice (place the object
                      # and the participant on opposite sides), not a claim that
                      # findings are always over there.
USER_PAN = 60.0       # SHARED with generate_s2_listen.py -- see the note there.
                      # Must be identical in s2/s3/s7_beckon/s7_found: it is the
                      # one direction 'the person' lies in. Reachable since the
                      # data cable was re-routed (pan now -67.4..+69.7).

# ---- the finding ----
OBJECT_ELEV = -10.0   # where it sits relative to level. A desk is below.
LEAN_DEG = -22.0      # the epistemic lean -- OBJECT LEG ONLY. A lean into a
                      # PERSON's space is the social lean, which FORM_DECISION
                      # rules out; toward a thing it is the novel move.
                      #
                      # WAS -12, WHICH IS S5b's WATCHING POSTURE EXACTLY. The
                      # body therefore said the same thing while announcing a
                      # finding as it did while merely watching, and the only
                      # difference between "I found it" and "I am looking" was
                      # 10 deg of head pitch. An epistemic escalation has to be
                      # visible on the layer that carries epistemic claims, and
                      # that is the body.
                      #
                      # -22 is not a new number: it is the depth the NOTICE
                      # already reaches (below). The clip now returns to the pose
                      # it locked on with -- watch -22, up to you, back to -22 --
                      # so the notice reads as a preview of the answer.
                      #
                      # It also strengthens the one open item in S7_DESIGN.md
                      # Sec.7: Naendrup-Poell & Onnasch's participants could not
                      # resolve DEPTH from direction alone, and the lean's
                      # parallax is our answer to that. A deeper lean is more
                      # parallax.
OBJECT_NOD = OBJECT_ELEV - LEAN_DEG   # cancel the lean's pitch, THEN aim.
                                      # Levelling and aiming are different jobs.

# ---- beat 1: notice ----
# Deepen the lean and let the gaze fall onto the object. NOT a new pitch for the
# head: the nod HOLDS at HOLD_NOD, so the drop is a consequence of the neck.
NOTICE_TILT = OBJECT_ELEV - HOLD_NOD  # = -22: lands the gaze exactly on
                                      # OBJECT_ELEV, the same point beat 3
                                      # returns to. Asserted below -- and since
                                      # LEAN_DEG is now -22 as well, the notice
                                      # pose and the final aimed pose are the
                                      # SAME pose. Asserted too, so the two
                                      # cannot drift apart silently.
NOTICE_S = 0.30
NOTICE_HOLD_S = 0.13  # "see, feel, react" -- Invisible Strings P4. Acting
                      # without first being seen to have seen reads as
                      # distracted.

# ---- beat 2: attention-get ----
OVERSHOOT = 4.0       # arrive past them and settle back
SETTLE_USER_S = 0.17
EYE_HOLD_S = 0.40     # long enough to be seen to have arrived

# ---- beat 3: direct ----
LEAD = 4.0            # how far the head leads its own final aim
COUNTER = OBJECT_NOD + LEAD   # the head leads while the neck dives, so the
                      # camera is on the object before the neck has finished
                      # travelling, and then settles DOWN onto it.
                      #
                      # WAS the absolute 6.0, which only led because OBJECT_NOD
                      # happened to be 2. Deepening the lean raises OBJECT_NOD,
                      # and at LEAN_DEG = -22 it becomes +12 -- so an absolute 6
                      # would have had the head arrive BELOW its final aim and
                      # rise into it, inverting the lead. Same class of fault as
                      # S3's NOD_DIP before it was made relative: an offset
                      # authored as a destination stops being an offset the
                      # moment its origin moves.
TILT_OVERSHOOT = 3.0
SETTLE_OBJ_S = 0.20

# ---- boundaries ----
HOLD_IN_S = 0.20
HOLD_OUT_S = 0.47     # the held fixation that ends the clip. Longer than the
                      # library's 0.20 on purpose: this beat CARRIES the
                      # referential content. Naendrup-Poell & Onnasch 2025
                      # recommend keeping a directional cue statically fixated on
                      # the target (their ANIMATED naturalistic gaze pattern
                      # reduced legibility), and Invisible Strings P3 says
                      # stillness is what reads as focus. Two independent
                      # sources, one eye-tracking and one puppetry craft.

# ---- LED: green = a result worth your attention ----
# Bright on the two ARRIVALS -- at you, and at the finding -- and dim through the
# transits, so the light marks the two things being connected rather than
# smearing across the whole clip.
#
# The firmware flashes above 150/255, i.e. above 4.7 on this 0..8 scale. Only the
# two arrivals are allowed to cross it; the notice and the hold sit just under,
# so they read as brightness rather than as events.
LED_ENTER = 2.0       # = S5b's breath midpoint, so the entry does not jump
LED_NOTICE = 4.5      # 143/255 -- under the flash threshold
LED_CALL = 8.0        # 255 -- "Cassie!"
LED_TRANSIT = 2.0     # dark through the crossing
LED_THERE = 8.0       # 255 -- "...there."
LED_HOLD = 4.0        # 127/255 -- lit, but not an event
LED_EDGE_S = 0.13     # how fast it falls into a transit and rises out of it.
                      # Without these the light RAMPS across the whole turn and
                      # is already at 209/255 halfway through -- so the accent
                      # smears over the travel instead of marking the arrival,
                      # which is the one thing this LED design is for.
LED_COLOR = (0.00, 1.00, 0.16, 1.0)

# ---- speed ----
# Ceiling on PEAK joint speed. The authoring limit is 200, but that is a no-load
# figure at 6 V and this clip's two big pan transits are the most loaded moves in
# the library. 120 is also what the build demonstrably runs: S2_LISTEN exports a
# pan peak of 118 deg/s and has been driven repeatedly on this hardware.
PEAK_DPS = 120.0
# WAS 1.5, which matches nothing. Minjerk peaks at 1.875x its segment average
# (raised cosine is pi/2 = 1.571). Under-estimating the factor under-estimates
# the peak, which is the direction that breaks things.
PEAK_FACTOR = 1.875
EASE_MODE = "minjerk"  # Flash & Hogan 1985 -- see generate_s2_listen.py.
                       # v3 set sparse keys and let Blender's DEFAULT BEZIER fill
                       # the gaps, so the exported curves were not authored at
                       # all -- they were whatever the interpolator did.
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
if abs((NOTICE_TILT + HOLD_NOD) - OBJECT_ELEV) > 0.01:
    raise RuntimeError(
        f"the notice does not land on the object: gaze "
        f"{NOTICE_TILT + HOLD_NOD:+.1f} vs OBJECT_ELEV {OBJECT_ELEV:+.1f}. The "
        f"beat that says 'I saw THAT' must aim where beat 3 returns to.")
if abs((LEAN_DEG + OBJECT_NOD) - OBJECT_ELEV) > 0.01:
    raise RuntimeError(
        f"the direct beat does not land on the object: gaze "
        f"{LEAN_DEG + OBJECT_NOD:+.1f} vs OBJECT_ELEV {OBJECT_ELEV:+.1f}.")
if abs(LEAN_DEG - HOLD_TILT) < 2.05:
    raise RuntimeError(
        f"the crane ({LEAN_DEG:+.1f}) is not visibly deeper than S5b's watching "
        f"posture ({HOLD_TILT:+.1f}). The body would say the same thing while "
        f"announcing a finding as it does while merely watching.")
if abs(NOTICE_TILT - LEAN_DEG) > 0.01:
    raise RuntimeError(
        f"the notice ({NOTICE_TILT:+.1f}) and the crane ({LEAN_DEG:+.1f}) are no "
        f"longer the same depth. They are meant to be the same pose -- the clip "
        f"returns to what it locked on with -- so decide which one moved.")

TURN_DEG = abs((USER_PAN + OVERSHOOT) - OBJECT_PAN)
CROSS_DEG = abs(USER_PAN - OBJECT_PAN)


def frames_for(deg, floor=4):
    """Frames needed to travel `deg` with the MINJERK peak under PEAK_DPS."""
    return max(floor, int(math.ceil(abs(deg) * PEAK_FACTOR / PEAK_DPS * FPS)))


HOLD_IN_F = int(round(HOLD_IN_S * FPS))
NOTICE_F = int(round(NOTICE_S * FPS))
NOTICE_HOLD_F = int(round(NOTICE_HOLD_S * FPS))
TURN_F = frames_for(TURN_DEG)
SETTLE_USER_F = int(round(SETTLE_USER_S * FPS))
EYE_HOLD_F = int(round(EYE_HOLD_S * FPS))
CROSS_F = frames_for(CROSS_DEG)
SETTLE_OBJ_F = int(round(SETTLE_OBJ_S * FPS))
HOLD_OUT_F = int(round(HOLD_OUT_S * FPS))
LED_EDGE_F = int(round(LED_EDGE_S * FPS))
if min(TURN_F, CROSS_F) <= 2 * LED_EDGE_F:
    raise RuntimeError(
        f"a transit ({min(TURN_F, CROSS_F)} f) is too short to go dark in "
        f"{LED_EDGE_F} f and come back. Either the turn shrank or LED_EDGE_S "
        f"grew -- the accent would ramp across the travel instead of marking "
        f"the arrival.")

# beat boundaries, in frames, cumulative from 1
F_IN = 1 + HOLD_IN_F                       # notice starts
F_NOTICE = F_IN + NOTICE_F                 # gaze has locked on
F_NOTICE_END = F_NOTICE + NOTICE_HOLD_F    # the turn starts
F_TURN = F_NOTICE_END + TURN_F             # arrives past the user
F_USER = F_TURN + SETTLE_USER_F            # settled on the user
F_EYE = F_USER + EYE_HOLD_F                # eye contact held; the cross starts
F_OBJ = F_EYE + CROSS_F                    # arrives at the finding
F_SETTLE = F_OBJ + SETTLE_OBJ_F            # craned and aimed
END_F = F_SETTLE + HOLD_OUT_F

for _j, _v, _w in (("pan", OBJECT_PAN, "object leg"),
                   ("pan", USER_PAN, "user leg"),
                   ("pan", USER_PAN + OVERSHOOT, "turn overshoot"),
                   ("tilt", HOLD_TILT, "inherited lean"),
                   ("tilt", NOTICE_TILT, "notice, deeper"),
                   ("tilt", LEAN_DEG - TILT_OVERSHOOT, "crane overshoot"),
                   ("tilt", LEAN_DEG, "crane"),
                   ("nod", HOLD_NOD, "inherited counter"),
                   ("nod", COUNTER, "head leads"),
                   ("nod", OBJECT_NOD, "aimed at the finding")):
    reach.check(_j, _v, _w)
for _j, _d, _w in (("tilt", NOTICE_TILT - HOLD_TILT, "the notice"),
                   ("pan", OVERSHOOT, "turn overshoot"),
                   ("tilt", NOTICE_TILT, "neck up for eye contact"),
                   ("nod", HOLD_NOD, "head level for eye contact"),
                   ("nod", COUNTER, "head leads the dive"),
                   ("nod", COUNTER - OBJECT_NOD, "settle onto the object"),
                   ("tilt", TILT_OVERSHOOT, "crane settle")):
    reach.check_floor(_j, _d, _w)

_FAC = math.pi / 2.0 if EASE_MODE == "cosine" else 1.875
for _w, _deg, _f in (("turn to the user", TURN_DEG, TURN_F),
                     ("cross to the finding", CROSS_DEG, CROSS_F),
                     ("the notice", NOTICE_TILT - HOLD_TILT, NOTICE_F),
                     ("neck up for eye contact", NOTICE_TILT, TURN_F),
                     ("head down onto the object", COUNTER - OBJECT_NOD,
                      SETTLE_OBJ_F)):
    _pk = abs(_deg) / (_f / float(FPS)) * _FAC
    if _pk > 200.0:
        raise RuntimeError(f"{_w} peaks at {_pk:.0f} deg/s, over the 200 deg/s "
                           f"ceiling.")

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
    """Value at frame `f` on a waypoint list [(frame, value), ...], eased between
    consecutive points. Sampling every frame from ONE schedule per joint is what
    replaces v3's sparse keys: the curve is authored here rather than left to
    whatever Blender's default interpolator happens to do between them."""
    if f <= points[0][0]:
        return points[0][1]
    for (f0, v0), (f1, v1) in zip(points, points[1:]):
        if f <= f1:
            if f1 == f0:
                return v1
            return v0 + (v1 - v0) * ease((f - f0) / float(f1 - f0))
    return points[-1][1]


# One schedule per joint. Read down a column to see a beat; read across a row to
# see a joint. Anything that must stay level for a stretch is written as two
# identical waypoints, so a hold is visible in the table rather than implied.
PAN = [(1, OBJECT_PAN), (F_IN, OBJECT_PAN),
       (F_NOTICE, OBJECT_PAN), (F_NOTICE_END, OBJECT_PAN),      # notice: no pan
       (F_TURN, USER_PAN + OVERSHOOT), (F_USER, USER_PAN),      # attention-get
       (F_EYE, USER_PAN),
       (F_OBJ, OBJECT_PAN), (F_SETTLE, OBJECT_PAN),             # direct
       (END_F, OBJECT_PAN)]

TILT = [(1, HOLD_TILT), (F_IN, HOLD_TILT),
        (F_NOTICE, NOTICE_TILT), (F_NOTICE_END, NOTICE_TILT),   # lean DEEPER
        (F_TURN, 0.0), (F_USER, 0.0), (F_EYE, 0.0),             # NO LEAN at a person
        (F_OBJ, LEAN_DEG - TILT_OVERSHOOT), (F_SETTLE, LEAN_DEG),
        (END_F, LEAN_DEG)]

NOD = [(1, HOLD_NOD), (F_IN, HOLD_NOD),
       (F_NOTICE, HOLD_NOD), (F_NOTICE_END, HOLD_NOD),          # HOLDS -- the
                                                                # gaze drops with
                                                                # the neck. This
                                                                # is the morpheme.
       (F_TURN, 0.0), (F_USER, 0.0), (F_EYE, 0.0),              # level = eyes
       (F_OBJ, COUNTER), (F_SETTLE, OBJECT_NOD),                # lead, then aim
       (END_F, OBJECT_NOD)]

LED = [(1, LED_ENTER), (F_IN, LED_ENTER),
       (F_NOTICE, LED_NOTICE), (F_NOTICE_END, LED_NOTICE),
       (F_NOTICE_END + LED_EDGE_F, LED_TRANSIT),                # go dark, and
       (F_TURN - LED_EDGE_F, LED_TRANSIT),                      # stay dark
       (F_TURN, LED_CALL), (F_USER, LED_CALL), (F_EYE, LED_CALL),
       # lit through the whole of the eye contact -- the beat is the arrival AND
       # the dwell on it, not just the instant of getting there
       (F_EYE + LED_EDGE_F, LED_TRANSIT),
       (F_OBJ - LED_EDGE_F, LED_TRANSIT),
       (F_OBJ, LED_THERE), (F_SETTLE, LED_THERE),
       (END_F, LED_HOLD)]

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

_pk_turn = TURN_DEG / (TURN_F / float(FPS)) * _FAC
_pk_cross = CROSS_DEG / (CROSS_F / float(FPS)) * _FAC

# states.py fires 'excited' at 0.0 -- the announcement should arrive WITH the
# turn, calling you before the robot has finished arriving. It stays 0.0.
msg = (f"S7a found v4: opens on S5b's hold ({OBJECT_PAN:+.0f}/{HOLD_TILT:+.0f}/"
       f"{HOLD_NOD:+.0f}) -> notice, lean to {NOTICE_TILT:+.0f}, gaze onto "
       f"{OBJECT_ELEV:+.0f} -> USER {USER_PAN:+.0f} -> back to the finding, "
       f"crane {LEAN_DEG:+.0f} nod {OBJECT_NOD:+.0f} -> hold {HOLD_OUT_F}f; "
       f"{END_F}f ({END_F / FPS:.2f}s), peaks {_pk_turn:.0f}/{_pk_cross:.0f} deg/s")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text="OPENS on S5b's hold at the OBJECT's bearing --")
    self.layout.label(text="the finding is in the region it was watching.")
    self.layout.label(text=f"ENDS on the OBJECT leg: pan {OBJECT_PAN:.0f} / "
                           f"tilt {LEAN_DEG:.0f} / nod {OBJECT_NOD:.0f}")
    self.layout.label(text="S7b must OPEN on that exact pose")
    self.layout.label(text="! runtime two-point remap still NOT implemented")
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(draw, title="S7a found v4", icon='INFO')
