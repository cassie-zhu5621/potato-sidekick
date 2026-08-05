# Auto-generates the S7b ENSURE loop (3-DOF + LED). Run inside S7b.blend after
# repair_rig.py + add_nod_joint.py. OVERWRITES all keys.
# Design rationale: ../../../robot_motion/S7_DESIGN.md (local, not in this repo).
#
# One cycle, starting and ending on the object hold so the loop is seamless:
#
#   [hold on OBJECT] --transit--> [USER: beckon toss] --transit--> [arrive, settle]
#         "there"                        "come"                        "there"
#
# Beat 3 of Mundy's initiating-joint-attention triple. S7a carries 1 and 2.
#
# v4 (2026-08-03). The structure of v3 is kept -- it is right -- and the
# authoring is brought up to the method the rest of the library now uses:
# minjerk sampled every frame instead of sparse keys left to Blender's default
# Bezier, live reach/floor/velocity guards instead of a hand-copied REACH table
# that was wrong in both directions, and PEAK_FACTOR corrected from 1.5 (which
# matches no easing curve at all) to minjerk's 1.875.
#
# Two shape changes fall out of that:
#
#   - AN ARRIVAL SETTLE ON THE OBJECT LEG, mirroring S7a's direct beat: the neck
#     overshoots the crane slightly and settles, and the head leads the dive.
#     v3 arrived at the object exactly on the cycle boundary, which left the
#     re-engagement of the lean with nowhere to resolve.
#   - LED EDGES. v3's light ramped linearly between waypoints, so it was already
#     near full brightness halfway through a transit -- the accent smeared across
#     the travel instead of marking the arrival. It now falls quickly into a
#     transit, stays dark across it, and rises on arrival.
#
# WHY THE LOOP ALTERNATES INSTEAD OF STARING (v3, unchanged):
#
#   v2 locked pan on the user for the entire loop and tossed the head, forever.
#   That is beat 1 (attention-get) repeated, not beat 3. The design calls for
#   ENSURE -- alternate between partner and referent to confirm the referent was
#   taken up (Mundy; Huang & Thomaz 2011 phase 3; FIND_AND_SHARE.md Sec.3).
#
#   Three reasons the alternation is load-bearing rather than decorative:
#
#   1. Admoni et al. HRI'13: MULTIPLE SHORT GLANCES BEAT ONE LONG STARE for
#      conveying attention, and it is the TRANSITION INTO fixation -- the
#      fixation event -- that reads as attending. v2's static lock on the user's
#      face was exactly the single long stare their data says is the weaker cue.
#      An alternation is a train of fixation events.
#   2. "Robot gaze does not reflexively cue human attention" (CogSci 2011):
#      people can INFER direction from robot gaze but do not reflexively
#      reallocate attention to the cued location. The follow has to be earned.
#   3. Naendrup-Poell & Onnasch 2025: participants who DID read the directional
#      cue still hesitated, waiting for a confirmation the system never gave.
#      The alternation is that confirmation.
#
#   The beckon toss survives, but only on the USER leg. Restricting it turns the
#   loop into a two-word sentence -- "come" / "there" -- instead of an
#   undifferentiated alarm. Invisible Strings P8, economy of movement.
#
#   WHICH STROKE IS FAST is what decides how the toss reads: between the same two
#   positions, a quick stroke up with a slow return says "come here", a quick
#   stroke down with a slow return says "yes". The eye assigns the meaning to the
#   accented stroke. Rise is short and sharp, there is a beat of hang at the top,
#   the return is more than twice as long and eased.
#
#   The toss is shared between nod and tilt: the head flicks up while the neck
#   ROCKS FORWARD under it (v5; it used to straighten up alongside it). That
#   reads as the whole creature leaning in to call rather than a head hinging,
#   it keeps nod clear of its ceiling, and because the two are equal and
#   opposite the gaze stays on the face for the whole stroke.
#
# ! STILL OPEN: the two-point runtime remap (S7_DESIGN.md Sec.5) is NOT
#   implemented, so on hardware this loop alternates between the TEMPLATE angles
#   regardless of where the person or the finding actually is. See the same note
#   in generate_s7_found.py.
#
# Loop-safe: every channel starts and ends on the OBJECT hold, which is also
# S7a's final pose. Keep OBJECT_* in sync with generate_s7_found.py or the loop
# will jump the moment it is entered. Asserted below.
#
# SIGN CONVENTION: BLENDER positive nod = head UP; on the bus a higher unit is
# DOWN. INVERT in robot/calibration.py reconciles them. Author against the render.

import bpy
import math

# ---- keep in sync with generate_s7_found.py ----
# Both pans are TEMPLATES, meant to be retargeted at runtime -- see the warning
# above and S7_DESIGN.md Sec.5.
USER_PAN = 60.0       # SHARED with generate_s2_listen.py -- see the note there.
                      # Must be identical in s2/s3/s7_beckon/s7_found: it is the
                      # one direction 'the person' lies in. Reachable since the
                      # data cable was re-routed (pan now -67.4..+69.7).
OBJECT_PAN = -25.0    # == the pan S5b was holding when the finding fired
LEAN_DEG = -22.0      # the epistemic lean -- OBJECT LEG ONLY. A lean into a
                      # PERSON's space is the social lean, which FORM_DECISION
                      # rules out. WAS -12 = S5b's watching posture exactly; see
                      # the long note in generate_s7_found.py. Deepening it also
                      # widens the two poles of the alternation, which is the
                      # thing this loop is made of.
USER_NOD = 0.0        # no lean on the user leg, so no compensation: level = eyes
OBJECT_ELEV = -10.0
OBJECT_NOD = OBJECT_ELEV - LEAN_DEG   # cancel the lean's pitch, THEN aim

# ---- the toss (USER leg only) ----
# !! NOT EXPORTED YET (2026-08-05). motion/clips/S7b.csv is still the v4 build:
#    tilt runs -25..+8, i.e. the OLD backward toss, and the gaze reaches +18 deg
#    at the top of the summons. Everything from here down describes what this
#    file WOULD produce, not what the robot currently plays. Deliberate -- the
#    change is cosmetic and the session was spent verifying behaviour -- but a
#    generator that disagrees with its own clip is exactly the kind of silent
#    mismatch this project keeps paying for, so it says so out loud.
#    Re-run in S7b.blend and export to clear this note.
THROW_NOD = 14.0      # head flicks UP this far (positive = up in Blender).
                      # LOCKED to -THROW_TILT by the gaze guard below; the two
                      # are one gesture and cannot be tuned apart.
THROW_TILT = -14.0    # v5: the neck now ROCKS FORWARD under the lifting head
                      # instead of straightening up with it. WAS +8.
                      #
                      # WHY, and why this is not the social lean the object leg
                      # is careful to keep for itself. FORM_DECISION rules out
                      # leaning into a person's space as a HELD POSTURE. This is
                      # a stroke: five frames out, eleven back, and the user
                      # leg's held pose is still tilt 0 / nod 0. A gesture that
                      # returns is not a posture, and -22 remains the only
                      # sustained lean in the vocabulary.
                      #
                      # The up-toss was the AUTHORED "come here" and it is not
                      # what the body reads as. Lifting the head while the neck
                      # also straightens moves the whole creature AWAY from the
                      # person it is summoning; the accent lands, but it lands
                      # in retreat. Rocking forward puts the body behind the
                      # summons.
                      #
                      # -10 pairs with THROW_NOD +10 so the gaze angle
                      # (tilt + nod) is 0 at rest, 0 at the top of the toss, and
                      # 0 on the way back: THE ROBOT ROCKS TOWARD YOU WITHOUT
                      # EVER TAKING ITS EYES OFF YOUR FACE. Eye contact is what
                      # makes a beckon a summons rather than a twitch.
                      #
                      # HOW DEEP THIS CAN GO IS SET BY RISE_F, NOT BY TASTE. The
                      # stroke has to cross it in RISE_F frames under PEAK_DPS:
                      #
                      #     max|THROW_TILT| = PEAK_DPS * (RISE_F/FPS) / 1.875
                      #                     = 21.3 deg per RISE_F frame @30fps
                      #
                      #     RISE_F  5 -> 10.7    7 -> 14.9    9 -> 19.2
                      #
                      # So a deeper rock is bought with frames, and the frames
                      # come out of the asymmetry that makes this read as "come
                      # here" rather than "yes" -- FALL_F has to grow with it to
                      # hold the ratio near 2.2. v5 takes RISE_F 5 -> 7 and
                      # FALL_F 11 -> 15: ratio 2.14, 14 deg of rock, and 0.2 s
                      # added to the cycle.
                      #
                      # -18 (RISE_F 9) was the next rung and is rejected twice
                      # over: a 0.3 s "flick" is no longer a flick, and -18 sits
                      # close enough to the object leg's -22 that the two poles
                      # of the alternation stop being two poles. THE CONTRAST IS
                      # THE GESTURE; a deeper user lean past this point buys
                      # emphasis by spending the thing being emphasised.

# ---- rhythm: the asymmetry IS the gesture ----
RISE_F = 7            # sharp. This is the stroke that carries the meaning.
                      # WAS 5. Raised only to buy depth for THROW_TILT -- see the
                      # ceiling formula there. It is the shortest beat that
                      # reaches -14 without passing PEAK_DPS.
TOP_F = 5             # hang at the top -- the "well? come on" beat
FALL_F = 15           # slow, eased return. Must be clearly longer than RISE_F.
                      # WAS 11, with RISE_F 5, for a ratio of 2.2. Grown with
                      # RISE_F to keep 2.14: the ratio is what says "come here"
                      # instead of "yes", so it is not free to drift when the
                      # rise changes.
HOLD_USER_F = 6       # settle on the face after the toss, before turning back
HOLD_OBJ_F = 14       # the held fixation. Must be >= S7a's hold-out, or the
                      # handover reads as the robot losing interest the moment
                      # the loop starts. Asserted below.
SETTLE_OBJ_S = 0.20   # the arrival settle, mirroring S7a's direct beat
TILT_OVERSHOOT = 3.0
LEAD = 4.0            # how far the head leads its own final aim
COUNTER = OBJECT_NOD + LEAD   # the head leads while the neck dives, then settles
                      # DOWN onto the object. WAS the absolute 6.0 -- see the
                      # note in generate_s7_found.py for why that silently
                      # inverts when the lean deepens.
CYCLES = 2            # alternations per loop; any number loops cleanly

# ---- LED: green, same as S7a -- one event, one colour ----
# Two accents per cycle, and they are the two things the loop is connecting:
# the top of the toss ("come") and the arrival back on the finding ("there").
# Dark across both transits so those two read as events rather than as a glow
# that happens to be brighter sometimes.
LED_HOLD = 4.0        # 127/255 -- lit on the finding, but not an event.
                      # MUST equal S7a's closing value or the handover blinks.
LED_TRANSIT = 2.0     # dark through a crossing
LED_CALL = 8.0        # 255 -- the summons, at the top of the toss
LED_THERE = 8.0       # 255 -- the arrival back on the finding
LED_EDGE_S = 0.13
LED_COLOR = (0.00, 1.00, 0.16, 1.0)

# ---- speed ----
PEAK_DPS = 120.0      # see generate_s7_found.py for why 120 and not 200
PEAK_FACTOR = 1.875   # minjerk. WAS 1.5, which matches no curve.
EASE_MODE = "minjerk"  # Flash & Hogan 1985 -- see generate_s2_listen.py
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
print("[s7b] " + reach.summary())

S7A_HOLD_OUT_F = 14   # generate_s7_found.py HOLD_OUT_S * FPS
if HOLD_OBJ_F < S7A_HOLD_OUT_F:
    raise RuntimeError(
        f"HOLD_OBJ_F {HOLD_OBJ_F} is shorter than S7a's closing hold "
        f"{S7A_HOLD_OUT_F}. The loop would speed up the moment it is entered, "
        f"which reads as the robot losing interest in what it just showed you.")
if FALL_F <= RISE_F:
    raise RuntimeError("FALL_F must be longer than RISE_F, or the accent lands "
                       "on the way down and this reads as a nod, not a beckon.")
if abs((LEAN_DEG + OBJECT_NOD) - OBJECT_ELEV) > 0.01:
    raise RuntimeError(
        f"the object leg does not aim at the object: gaze "
        f"{LEAN_DEG + OBJECT_NOD:+.1f} vs OBJECT_ELEV {OBJECT_ELEV:+.1f}.")
if abs(THROW_TILT + THROW_NOD) > 0.01:
    raise RuntimeError(
        f"the toss does not hold the gaze: tilt+nod at the top is "
        f"{THROW_TILT + THROW_NOD:+.1f}, not 0. The user leg rests level, so a "
        f"non-zero peak means the robot looks away from the face at the exact "
        f"moment it is summoning -- which is the difference between a beckon "
        f"and a flinch. Set THROW_NOD = -THROW_TILT.")
if THROW_TILT > 0:
    raise RuntimeError(
        f"THROW_TILT is {THROW_TILT:+.1f}: the neck rocks BACK on the toss, "
        f"moving the robot away from the person it is calling. v5 made this "
        f"forward on purpose -- see the note at the constant.")
if LEAN_DEG != -22.0:
    raise RuntimeError(
        f"LEAN_DEG is {LEAN_DEG:+.1f} here but generate_s7_found.py authors "
        f"-22.0. S7b opens on S7a's last frame; if these differ the loop jumps "
        f"the moment it is entered.")

CROSS_DEG = abs(USER_PAN - OBJECT_PAN)


def frames_for(deg, floor=4):
    """Frames needed to travel `deg` with the MINJERK peak under PEAK_DPS."""
    return max(floor, int(math.ceil(abs(deg) * PEAK_FACTOR / PEAK_DPS * FPS)))


# The transit also has to carry the lean in and out. If the neck move is the
# slower of the two it -- not pan -- sets the transit length, or the lean would
# arrive after the head and the two would read as separate events.
CROSS_F = max(frames_for(CROSS_DEG), frames_for(LEAN_DEG - TILT_OVERSHOOT))
SETTLE_OBJ_F = int(round(SETTLE_OBJ_S * FPS))
LED_EDGE_F = int(round(LED_EDGE_S * FPS))
if CROSS_F <= 2 * LED_EDGE_F:
    raise RuntimeError(
        f"the transit ({CROSS_F} f) is too short to go dark in {LED_EDGE_F} f "
        f"and come back -- the accent would ramp across the travel instead of "
        f"marking the arrival.")

CYCLE_F = (HOLD_OBJ_F + CROSS_F + RISE_F + TOP_F + FALL_F + HOLD_USER_F
           + CROSS_F + SETTLE_OBJ_F)
END_F = 1 + CYCLES * CYCLE_F

for _j, _v, _w in (("pan", OBJECT_PAN, "object leg"),
                   ("pan", USER_PAN, "user leg"),
                   ("tilt", LEAN_DEG, "crane"),
                   ("tilt", LEAN_DEG - TILT_OVERSHOOT, "crane overshoot"),
                   ("tilt", THROW_TILT, "toss, neck"),
                   ("nod", OBJECT_NOD, "aimed at the finding"),
                   ("nod", COUNTER, "head leads"),
                   ("nod", USER_NOD + THROW_NOD, "toss, head")):
    reach.check(_j, _v, _w)
# NOTE ON THE FLOOR CHECK, because one excursion here looks like a violation and
# is not. On the transit the nod moves only OBJECT_NOD -> 0, i.e. 2.0 deg, under
# the 3.52 deg nod floor. But that 2 deg is not a movement in its own right: it
# is one component of levelling the gaze as the lean releases, and the gaze
# itself travels 10 deg. THE FLOOR APPLIES TO A JOINT'S EXCURSION WHEN THAT
# EXCURSION IS THE WHOLE MOVE; when a joint is one term of a coordinated pose
# change, check the pose change. So the gaze is checked, not the nod.
for _j, _d, _w in (("pan", CROSS_DEG, "the crossing"),
                   ("tilt", abs(LEAN_DEG), "lean releases for the user"),
                   ("tilt", THROW_TILT, "toss, neck"),
                   ("nod", THROW_NOD, "toss, head -- the accent"),
                   ("nod", COUNTER - OBJECT_NOD, "settle onto the object"),
                   ("tilt", TILT_OVERSHOOT, "crane settle")):
    reach.check_floor(_j, _d, _w)
reach.check_floor("tilt", abs(OBJECT_ELEV), "gaze levels on the transit")

_FAC = math.pi / 2.0 if EASE_MODE == "cosine" else 1.875
for _w, _deg, _f in (("the crossing", CROSS_DEG, CROSS_F),
                     ("lean release", LEAN_DEG, CROSS_F),
                     ("toss, head", THROW_NOD, RISE_F),
                     ("toss, neck", THROW_TILT, RISE_F),
                     ("head down onto the object", COUNTER - OBJECT_NOD,
                      SETTLE_OBJ_F)):
    _pk = abs(_deg) / (_f / float(FPS)) * _FAC
    # WAS 200, which left a hole exactly where this file needs a guard.
    # `frames_for()` SIZES a move so its peak stays under PEAK_DPS (120), but the
    # rhythm constants -- RISE_F, TOP_F, FALL_F, SETTLE_OBJ_S -- are hand-set and
    # never went through it. So a hand-set beat was only ever checked against
    # 200, and anything between 120 and 200 passed while quietly breaking the
    # policy the computed moves obey. THROW_TILT = -12 is 135 and is precisely
    # that case. Every current value is at or under 119.5, so this costs nothing
    # today and catches the next edit.
    if _pk > PEAK_DPS:
        raise RuntimeError(
            f"{_w} peaks at {_pk:.0f} deg/s, over PEAK_DPS {PEAK_DPS:.0f}. "
            f"Either slow the beat or shorten the excursion -- the servo will "
            f"not refuse, it will simply arrive late and short, which reads as "
            f"a weak gesture rather than as an error.")

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


PAN, TILT, NOD, LED = [], [], [], []
for i in range(CYCLES):
    f0 = 1 + i * CYCLE_F                 # on the finding, settled
    f_hold = f0 + HOLD_OBJ_F             # the crossing to you starts
    f_user = f_hold + CROSS_F            # arrives on your face, level
    f_up = f_user + RISE_F               # the accent: "come"
    f_hang = f_up + TOP_F                # hold the question open
    f_down = f_hang + FALL_F             # eased, unhurried return
    f_user_end = f_down + HOLD_USER_F    # settle on the face, then turn back
    f_obj = f_user_end + CROSS_F         # arrives back on the finding: "there"
    f_next = f_obj + SETTLE_OBJ_F        # craned and aimed = next cycle's f0

    PAN += [(f0, OBJECT_PAN), (f_hold, OBJECT_PAN),
            (f_user, USER_PAN), (f_up, USER_PAN), (f_hang, USER_PAN),
            (f_down, USER_PAN), (f_user_end, USER_PAN),
            (f_obj, OBJECT_PAN), (f_next, OBJECT_PAN)]

    TILT += [(f0, LEAN_DEG), (f_hold, LEAN_DEG),
             (f_user, 0.0),                        # the lean belongs to the
                                                   # object, never to a person
             (f_up, THROW_TILT), (f_hang, THROW_TILT),
             (f_down, 0.0), (f_user_end, 0.0),
             (f_obj, LEAN_DEG - TILT_OVERSHOOT),   # overshoot, then settle
             (f_next, LEAN_DEG)]

    NOD += [(f0, OBJECT_NOD), (f_hold, OBJECT_NOD),
            (f_user, USER_NOD),                    # level = eyes
            (f_up, USER_NOD + THROW_NOD), (f_hang, USER_NOD + THROW_NOD),
            (f_down, USER_NOD), (f_user_end, USER_NOD),
            (f_obj, COUNTER),                      # head leads the dive
            (f_next, OBJECT_NOD)]                  # then aims

    LED += [(f0, LED_HOLD), (f_hold, LED_HOLD),
            (f_hold + LED_EDGE_F, LED_TRANSIT),
            (f_user - LED_EDGE_F, LED_TRANSIT), (f_user, LED_TRANSIT),
            (f_up, LED_CALL), (f_hang, LED_CALL),  # lit through the hang
            (f_down, LED_TRANSIT), (f_user_end, LED_TRANSIT),
            (f_obj - LED_EDGE_F, LED_TRANSIT), (f_obj, LED_THERE),
            (f_next, LED_HOLD)]                    # eases back while it dwells

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

_pk_cross = CROSS_DEG / (CROSS_F / float(FPS)) * _FAC
_pk_toss = THROW_NOD / (RISE_F / float(FPS)) * _FAC
msg = (f"S7b ensure v4: hold OBJECT {OBJECT_PAN:+.0f} ({HOLD_OBJ_F}f) <-> USER "
       f"{USER_PAN:+.0f} toss +{THROW_NOD:.0f} -> arrive + settle; "
       f"{CYCLES}x{CYCLE_F}f = {END_F}f ({END_F / FPS:.2f}s), "
       f"peaks {_pk_cross:.0f}/{_pk_toss:.0f} deg/s")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text=f"OPENS+CLOSES on OBJECT: pan {OBJECT_PAN:.0f} / "
                           f"tilt {LEAN_DEG:.0f} / nod {OBJECT_NOD:.0f}")
    self.layout.label(text="= S7a's last frame. Keep both files in sync.")
    self.layout.label(text="! runtime two-point remap still NOT implemented")
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(draw, title="S7b ensure v4", icon='INFO')
