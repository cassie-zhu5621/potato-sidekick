# Auto-generates the S7b INSIST loop (3-DOF + LED). Run inside S7b.blend after
# repair_rig.py + add_nod_joint.py. OVERWRITES all keys.
# Design rationale: ../../../robot_motion/S7_DESIGN.md (local, not in this repo).
#
# One cycle. Pan never moves: the robot holds the finding and pushes at it.
#
#   [hold on OBJECT] --push--> --push--> --push--> [hold on OBJECT]
#        "look"                 there  there  there      "look"
#
# v5 (2026-08-08). THE ALTERNATION IS GONE, and this is the one change that
# matters; everything else is the same authoring method as v4.
#
# WHY IT WENT. v2-v4 crossed between the participant and the finding, twice per
# loop, on the strength of Mundy's initiating-joint-attention triple and Huang &
# Thomaz 2011 phase 3: alternate between partner and referent to confirm the
# referent was taken up. The argument is sound and the citations are real. What
# was never true is the PREMISE -- that the robot knows where the partner is.
#
# The study lets the participant place the robot anywhere on the desk, at any
# angle, and nothing measures where they then sit. The user leg was played at an
# authored USER_PAN unless a seat angle had been locked by hand. So on a freely
# placed robot the "ensure" beat turned toward an empty corner every 1.6 s, with
# full confidence, three times a loop. A gesture that requires a fact the system
# does not have does not degrade gracefully -- it degrades into a robot
# addressing the furniture.
#
# WHAT REPLACED IT is limited to what the robot can actually know. It knows
# where the FINDING is, because it was looking at it one clip ago. So it stays
# there and leans at it, repeatedly, and the participant reads the direction off
# the body -- which is the same channel S5b/S7a already use, and the only one
# that survives arbitrary placement.
#
# THE COST, NAMED. This is no longer joint-attention ENSURE, and the paper must
# not claim it is. "Have you got it?" (dyadic, requires the partner) has become
# "it is HERE" (deictic, requires only the referent). Beat 3 of the triple is
# not performed by the robot at all now; uptake is observed only through the OK
# button and S7_IGNORED_TIMEOUT_S. What is kept from the literature:
#
#   - Admoni et al. HRI'13: MULTIPLE SHORT ACCENTS BEAT ONE LONG STARE. The
#     three-push train is still a train of events rather than a static hold --
#     that argument never depended on WHERE the second leg pointed, only on
#     there being repeated onsets. This is the piece of the old rationale that
#     survives the change intact.
#   - "Robot gaze does not reflexively cue human attention" (CogSci 2011):
#     direction can be INFERRED but is not reflexively followed, so a single
#     quiet aim is not enough. Repetition and amplitude are the compensation.
#   - Invisible Strings P8, economy of movement: one moving joint, one meaning.
#     v5 is more economical than v4, not less -- pan is silent for the whole
#     loop, so the lean is the only thing speaking.
#
# WHICH STROKE IS FAST decides how a repeated motion reads, and it is unchanged:
# between the same two poses, a quick stroke OUT with a slow return says "look
# there"; a quick stroke back with a slow return says "yes". The push is short
# and sharp, there is a beat at the bottom, the return is more than twice as
# long and eased. Get this backwards and the insist becomes a nod.
#
# THE GAZE STAYS ON THE FINDING while the body pushes: nod compensates the extra
# lean exactly, so tilt+nod is constant at OBJECT_ELEV. The body moves, the look
# does not. If nod were held instead, the gaze would dip 12 deg into the floor
# in front of the object on every beat -- pointing at the wrong thing three
# times per loop, which is the failure v5 exists to remove.
#
# Runtime: remap_share_pan translates the whole clip so OBJECT_PAN lands on
# wherever S5b actually was. With pan constant this is now a pure offset and
# cannot distort anything -- another thing the alternation made fragile.
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
OBJECT_PAN = -25.0    # == the pan S5b was holding when the finding fired.
                      # A TEMPLATE: remap_share_pan rewrites it at runtime to
                      # wherever S5b actually was.
OBJECT_ELEV = 0.0     # WAS -10, and that number was never measured -- it was
                      # authored as "a desk is below". The finding fires INSIDE
                      # S5b's own view, so it sits at S5b's gaze, which is level.
LEAN_DEG = -12.0      # = S5b's HOLD_TILT, so THE REST POSE OF S7 IS THE WATCHING
                      # POSE. The camera rides on the head; at -22 every panel
                      # the storyboard took during S7's rests sat ten degrees --
                      # a third of the vertical frame -- below its opening
                      # panel, and the strip cut between them. Reported
                      # 2026-08-08.
                      #
                      # The ten degrees WAS the epistemic escalation, "watching"
                      # against "found it". It is not dropped, it has moved into
                      # the PUSH below: a transient toward the finding rather
                      # than a posture held while the camera is trying to record
                      # what was found. Admoni HRI'13 already argued that way for
                      # this loop's train of accents over a single held stare.
                      #
                      # THE COST, NAMED: a still photograph of S7 between pushes
                      # is indistinguishable from S5b watching. The difference
                      # lives in the motion, the LED (summon) and the sound.
                      #
                      # Must equal generate_s7_found's -- S7b opens on S7a's
                      # last frame -- and both must equal S5b's HOLD_TILT.
OBJECT_NOD = OBJECT_ELEV - LEAN_DEG   # cancel the lean's pitch, THEN aim

# ---- the insistence ----
#
# v5: THE ALTERNATION IS GONE. S7b used to cross between a template USER_PAN and
# the object, twice per loop -- Mundy's third beat, alternating between partner
# and referent to confirm the referent was taken up.
#
# It is dropped because it needs a fact this system does not have. The
# participant places the robot wherever they like, and nothing measures where
# they then sit; the user leg was played at an authored +60 unless somebody
# locked a seat angle by hand. A gesture that requires the partner's position
# will, on a robot placed freely, point confidently at the wrong place -- and
# pointing confidently at nobody is worse than not pointing at a person at all.
#
# What replaces it is honest about what the robot knows: it knows where the
# FINDING is, because it was just looking at it. So it stays there and pushes
# toward it, repeatedly. "Have you got it?" becomes "it is HERE, it is HERE".
#
# The cost is named rather than hidden: this is no longer joint-attention
# ENSURE. Nothing in the loop now checks whether the person took the referent
# up, because nothing in the loop can see them. S7's uptake is measured only by
# the OK button and by S7_IGNORED_TIMEOUT_S.
BOB_DEG = 12.0        # how much deeper the neck pushes on each beat, from the
                      # held lean. -22 -> -34, well inside tilt's -46.9 reach.
                      # Ceiling is RISE_F: 12/(7/30)*1.875 = 96 deg/s of 120.
BOB_N = 3             # beats per loop. Three reads as insistence; two reads as
                      # a twitch, and four starts to nag.

# ---- rhythm: the asymmetry IS the gesture ----
RISE_F = 7            # sharp push toward the thing. The accented stroke.
TOP_F = 4             # a beat at the bottom -- "there"
FALL_F = 15           # slow, eased return. Ratio 2.14, the same shape the
                      # beckon used: a quick stroke out and a slow one back is
                      # what separates "look" from a nod.
GAP_F = 8             # between beats, held at the lean
HOLD_OBJ_F = 14       # the dwell that OPENS the loop. Must be >= S7a's closing
                      # hold or the handover reads as the robot losing interest
                      # the moment the loop starts.
REST_F = 88           # ...and the one that CLOSES it, which is a different job
                      # and a much longer one.
                      #
                      # S7b LOOPS until OK or S7_IGNORED_TIMEOUT_S (30 s), so
                      # the cycle length is an insistence RATE, not a duration.
                      # v4 was 8.5 s carrying one accent: 3.5 accents in the 30
                      # s window. Three pushes inside a 4.1 s cycle would be 22
                      # -- six times the rate, at a light that flashes with
                      # every one. The amplitude went down in v5 and the
                      # frequency would have more than made up for it.
                      #
                      # 88 frames makes the cycle 196 f (6.53 s). Because the
                      # clip loops, this rest runs straight into the next
                      # opening hold: 3.4 s of stillness between trains, against
                      # 3.1 s of pushing. Speaking and waiting in about equal
                      # measure -- and the waiting is not padding, it is the
                      # beat in which the person is given room to look up. A
                      # gesture that never stops asking is not asking.
                      # ~4.6 trains in the 30 s window, 14 pushes.
CYCLES = 1            # one pass already contains BOB_N beats

# ---- LED: one accent per push ----
LED_HOLD = 4.0        # 127/255 -- lit on the finding, not an event.
                      # MUST equal S7a's closing value or the handover blinks.
LED_THERE = 8.0       # 255 -- at the bottom of each push
LED_EDGE_S = 0.13
LED_COLOR = (1.00, 0.75, 0.00, 1.0)   # = SUMMON (45/100/100). Render only; the
                                      # robot's colour comes from states.py.

# ---- speed ----
PEAK_DPS = 120.0
PEAK_FACTOR = 1.875   # minjerk (Flash & Hogan 1985)
EASE_MODE = "minjerk"
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
                       "on the way back and this reads as a nod, not a push.")
if BOB_DEG <= 0:
    raise RuntimeError(
        f"BOB_DEG is {BOB_DEG:+.1f}: the push must go FORWARD, deeper into the "
        f"lean and toward the finding. A negative value rocks the robot back "
        f"from the thing it is pointing at.")
if abs((LEAN_DEG + OBJECT_NOD) - OBJECT_ELEV) > 0.01:
    raise RuntimeError(
        f"the clip does not aim at the object: gaze "
        f"{LEAN_DEG + OBJECT_NOD:+.1f} vs OBJECT_ELEV {OBJECT_ELEV:+.1f}.")
# THE ONE THAT REPLACES v4's toss check. There the pair had to cancel so the
# gaze stayed on the FACE during the summons; here it has to cancel so the gaze
# stays on the FINDING during the push. Same arithmetic, opposite subject --
# which is the whole of what changed in v5.
_gaze_bot = (LEAN_DEG - BOB_DEG) + (OBJECT_NOD + BOB_DEG)
if abs(_gaze_bot - OBJECT_ELEV) > 0.01:
    raise RuntimeError(
        f"the push drags the gaze off the finding: at the bottom it looks at "
        f"{_gaze_bot:+.1f}, not OBJECT_ELEV {OBJECT_ELEV:+.1f}. nod must rise "
        f"by exactly the BOB_DEG that tilt falls, or the robot points {abs(_gaze_bot - OBJECT_ELEV):.0f} "
        f"deg past its own finding {BOB_N} times a loop.")
if BOB_N < 2:
    raise RuntimeError(
        "BOB_N < 2 is a single twitch. Admoni HRI'13 is the reason this loop "
        "is a train of accents rather than a hold; one accent is neither.")
if LEAN_DEG != -12.0:
    raise RuntimeError(
        f"LEAN_DEG is {LEAN_DEG:+.1f} here but generate_s7_found.py authors "
        f"-12.0. S7b opens on S7a's last frame; if these differ the loop jumps "
        f"the moment it is entered -- and both must equal S5b's HOLD_TILT, or "
        f"the storyboard cuts between watching and reporting.")


def frames_for(deg, floor=4):
    """Frames needed to travel `deg` with the MINJERK peak under PEAK_DPS."""
    return max(floor, int(math.ceil(abs(deg) * PEAK_FACTOR / PEAK_DPS * FPS)))


LED_EDGE_F = int(round(LED_EDGE_S * FPS))
if RISE_F <= LED_EDGE_F or FALL_F <= LED_EDGE_F:
    raise RuntimeError(
        f"a push leg is shorter than the LED edge ({LED_EDGE_F} f), so the "
        f"light would still be ramping when the neck arrives -- the accent "
        f"smears across the travel instead of marking the bottom.")

if REST_F <= HOLD_OBJ_F:
    raise RuntimeError(
        f"REST_F {REST_F} is not longer than HOLD_OBJ_F {HOLD_OBJ_F}. The "
        f"closing rest is what turns the loop into an utterance followed by a "
        f"wait; if it is as short as the handover dwell, the trains run "
        f"together and the robot reads as vibrating rather than insisting.")
CYCLE_F = (HOLD_OBJ_F + BOB_N * (RISE_F + TOP_F + FALL_F)
           + (BOB_N - 1) * GAP_F + REST_F)
END_F = 1 + CYCLES * CYCLE_F

for _j, _v, _w in (("pan", OBJECT_PAN, "held on the finding, all loop"),
                   ("tilt", LEAN_DEG, "the crane, at rest"),
                   ("tilt", LEAN_DEG - BOB_DEG, "the bottom of a push"),
                   ("nod", OBJECT_NOD, "aimed at the finding"),
                   ("nod", OBJECT_NOD + BOB_DEG, "counter-rotated at the bottom")):
    reach.check(_j, _v, _w)
for _j, _d, _w in (("tilt", BOB_DEG, "the push, neck"),
                   ("nod", BOB_DEG, "the push, head -- the counter-rotation")):
    reach.check_floor(_j, _d, _w)

_FAC = math.pi / 2.0 if EASE_MODE == "cosine" else 1.875
for _w, _deg, _f in (("the push, neck", BOB_DEG, RISE_F),
                     ("the push, head", BOB_DEG, RISE_F),
                     ("the return, neck", BOB_DEG, FALL_F),
                     ("the return, head", BOB_DEG, FALL_F)):
    _pk = abs(_deg) / (_f / float(FPS)) * _FAC
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
    f = 1 + i * CYCLE_F
    PAN += [(f, OBJECT_PAN)]                      # said once; never moves again
    TILT += [(f, LEAN_DEG)]
    NOD += [(f, OBJECT_NOD)]
    LED += [(f, LED_HOLD)]

    f_start = f + HOLD_OBJ_F                      # the opening dwell ends
    TILT += [(f_start, LEAN_DEG)]
    NOD += [(f_start, OBJECT_NOD)]
    LED += [(f_start, LED_HOLD)]

    for k in range(BOB_N):
        f_bot = f_start + RISE_F                  # deepest, aimed at the thing
        f_up = f_bot + TOP_F                      # the beat: "there"
        f_back = f_up + FALL_F                    # eased home
        f_start = f_back + (GAP_F if k < BOB_N - 1 else 0)

        TILT += [(f_bot, LEAN_DEG - BOB_DEG), (f_up, LEAN_DEG - BOB_DEG),
                 (f_back, LEAN_DEG), (f_start, LEAN_DEG)]
        # Equal and opposite, so tilt+nod stays at OBJECT_ELEV for the whole
        # push. The creature leans; the look does not leave the finding.
        NOD += [(f_bot, OBJECT_NOD + BOB_DEG), (f_up, OBJECT_NOD + BOB_DEG),
                (f_back, OBJECT_NOD), (f_start, OBJECT_NOD)]
        # Dark on the way in, full at the bottom, out again on the return: the
        # light marks the ARRIVAL, not the travel. Same edge treatment v4 used
        # on its transits, for the same reason.
        LED += [(f_bot - LED_EDGE_F, LED_HOLD), (f_bot, LED_THERE),
                (f_up, LED_THERE), (f_up + LED_EDGE_F, LED_HOLD),
                (f_back, LED_HOLD), (f_start, LED_HOLD)]

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

_pk = BOB_DEG / (RISE_F / float(FPS)) * _FAC
msg = (f"S7b insist v5: pan HELD at {OBJECT_PAN:+.0f}; {BOB_N} pushes of "
       f"{BOB_DEG:.0f} deg into the lean ({LEAN_DEG:+.0f} -> "
       f"{LEAN_DEG - BOB_DEG:+.0f}), gaze locked at {OBJECT_ELEV:+.0f}; "
       f"{CYCLES}x{CYCLE_F}f = {END_F}f ({END_F / FPS:.2f}s), peak {_pk:.0f} deg/s; "
       f"{30.0 / (CYCLE_F / FPS) * BOB_N:.0f} pushes in the 30 s ignore window")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text=f"PAN NEVER MOVES. Opens+closes on pan {OBJECT_PAN:.0f} / "
                           f"tilt {LEAN_DEG:.0f} / nod {OBJECT_NOD:.0f}")
    self.layout.label(text="= S7a's last frame. Keep both files in sync.")
    self.layout.label(text="v5: no user leg -- placement is arbitrary, so the")
    self.layout.label(text="robot only points at what it can actually locate.")
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(draw, title="S7b insist v5", icon='INFO')
