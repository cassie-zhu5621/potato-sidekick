# Auto-generates S2 LISTEN (3-DOF + LED). Run inside S2_LISTEN.blend after
# repair_rig.py + add_nod_joint.py. OVERWRITES all keys.
# Design rationale: ../../../robot_motion/S2_DESIGN.md (local, not in this repo).
#
# S2 is the first half of the exchange: the button has been pressed and the
# person is speaking. It is a NEAR state -- a turn in a conversation, read at the
# moment it occurs, from about a metre.
#
# S2 IS A WAKE, NOT A TURN. S1 leaves the robot folded down and asleep, so S2
# does not start from neutral: it starts from a posture that means "not
# attending", and the whole state is the transition out of it. The contrast
# carries the meaning -- arriving upright and facing the speaker, from folded and
# facing the desk, says "I have come to attention" without needing stillness to
# say it.
#
# That also settles the anticipation question. Animation practice would wind up
# opposite the action (Lasseter 1987), while Takayama et al. (2011) found that
# looking toward where the robot is about to go improves readability. Both are
# added beats. Here the wake supplies one for free and causally: the head has to
# come UP before it can look at anyone, so the lift is the anticipation for the
# turn, and it is a real precondition rather than a flourish.

import bpy
import math

# ---- the pose it wakes FROM (must match generate_s1_idle.py) ----
SLEEP_TILT = -8.0
SLEEP_NOD = -42.0

# ---- beat 1: the pose it wakes INTO ----
# Waking is the WHOLE POSTURE coming up, not just the head. The previous version
# moved tilt only -8 -> -6 and that was wrong twice over. It was wrong as a
# design -- a creature that lifts its head while its neck stays folded reads as
# broken, not as awake -- and it was wrong as a command: 2.0 deg of tilt is
# BELOW the measured tilt amplitude floor of 7 units (2.05 deg), so the servo
# acknowledged it and never moved. The neck genuinely did nothing on hardware.
UPRIGHT_TILT = 4.0     # neck comes all the way up, slightly past vertical
UPRIGHT_NOD = 2.0      # gaze +6 deg -- awake and oriented, but not yet on the
                       # face. Waking gets the body up; MEETING THE EYES IS THE
                       # LAST THING THAT HAPPENS, and it belongs to the lean.
                       # See LEAN_NOD_AT_S for why it arrives last without being
                       # cut off from the lean by a pause.

# ---- beat 3: settling into listening ----
# A social lean. The epistemic/social lean distinction (S7_DESIGN sec 2) bans
# leaning into a person's space where the robot should be pointing at the world
# -- but S2 is one of the three states where the human IS the referent, so a
# lean toward the speaker is the correct one here. Same for S3 and S6.
#
# The two pitch joints move in OPPOSITE directions here, and that is the point:
# the neck goes 10 deg forward while the head pitches 8 deg back, so the gaze
# drops only 2 deg. The body arrives, the eyes stay on the face. A single pitch
# joint cannot do this -- leaning would aim the camera at the desk. This is the
# clearest place in the vocabulary where the third DOF earns itself.
LISTEN_TILT = -28.0    # neck leaned toward the speaker: 32 deg forward
LISTEN_NOD = 53.0      # chin up 51 deg, bringing the gaze from +6 to +25 -- ONTO
                       # THE FACE. This is the moment of contact and it is the
                       # last thing in the clip to settle.
                       #
                       # Was +22 (gaze +10) and the lift read as too small on the
                       # model. The larger value is also the better estimate: +10
                       # assumed the speaker sits about 0.9 m away, but a desk
                       # companion is used at more like 0.6 m, and a head ~22 cm
                       # above the desk looking at eyes ~43 cm above it needs
                       # atan(0.21/0.6) = 19 deg. Closer placement means a
                       # STEEPER look up, not a shallower one.
                       #
                       # THE LOOM WAS THE CEILING, AND THE LOOM MOVED. This sat
                       # at 47 for a while because the wiring went tight there --
                       # recorded at the time as "the one limit only the hardware
                       # can report". True, but the conclusion drawn from it was
                       # wrong: it was treated as a property of the build.
                       #
                       # It was a data lead routed in front of the neck. Moving it
                       # behind gave nod +69.7 and tilt's forward lean 46.6 deg
                       # (from 19.9), so the lean went 26 -> 32 deg and the chin
                       # followed to hold the same gaze.
                       #
                       #   A REACHABLE RANGE IS AN ASSEMBLY STATE, NOT A PROPERTY
                       #   OF THE BUILD.
                       #
                       # Which also means it can go back. Re-run reach.py after
                       # any reassembly; the guards read it live, so a regression
                       # fails loudly here instead of clamping on the robot.
                       #
                       # 53 leaves 16.7 deg to the nod rail and needs
                       # set_nod_limit.py at 60 (Blender clamps silently at 48).
                       #
                       # Still an estimate, and it should not stay one. Sit where
                       # a participant sits, jog nod until the camera preview
                       # centres on the face, read the number off, and carry it
                       # to every state where the human is the referent
                       # (S2, S3, S6). Do it before the study films.
                       #
                       # These two are S3_ACK's starting pose -- S3 dips FROM the
                       # raised chin, so the pair is a shared boundary condition,
                       # not a free choice. Change one, change both.
USER_PAN = -30.0        # template. The firmware retargets this at runtime to
                       # wherever the speaker actually is.
                       #
                       # THIS VALUE IS SHARED. The same constant appears in
                       # generate_s3_ack.py, generate_s7_beckon.py and
                       # generate_s7_found.py, and it has to be identical in all
                       # four: it is the single direction "the person" lies in.
                       # If the clips disagree, the states each face a slightly
                       # different person and the exchange stops composing.
                       #
                       # It was 60, then 50, and it is 60 again -- and the round
                       # trip is the interesting part. 60 originally clipped: the
                       # pan rail allowed only 57.4 deg, so the state's turn
                       # arrived somewhere else and the settle vanished, silently.
                       #
                       # THE RAIL WAS NOT THE MACHINE, IT WAS A CABLE. Re-routing
                       # one data lead behind the neck moved every rail outward:
                       # pan to +69.7, tilt's forward lean from 19.9 to 46.6 deg.
                       # 60 now leaves 9.7 deg here and 5.7 under S7a's overshoot.
                       #
                       # Worth remembering as a class of error rather than an
                       # incident: A REACHABLE RANGE IS AN ASSEMBLY STATE, NOT A
                       # PROPERTY OF THE BUILD. It changed once without anyone
                       # touching the design, and it can change again.

# ---- boundaries: every clip opens and closes on a settled hold ----
# A one-shot clip has to do two things that pull against each other: be
# recognisable ALONE, which wants a sharp start and end, and sit comfortably in
# a SEQUENCE, which wants a smooth join. Event segmentation research resolves
# them rather than trading them off: viewers spontaneously parse continuous
# behaviour into units, and they place the boundaries in strikingly similar
# places -- at moments where perceptual features, especially motion, change
# sharply (Newtson 1973; Zacks et al. 2007). A brief moment of STILLNESS is
# therefore a boundary cue, not dead time.
#
# So the same short hold does all three jobs at once: it is the event boundary
# that makes the state segmentable, it is a moving hold rather than a dead one
# (Williams), and it keeps this clip's motion from colliding with the next.
# van Breemen (2004) solved the collision on iCat with a transition filter that
# blends between behaviours; authoring the holds into the clips does the same
# thing without letting a filter reshape the ease curves.
#
# The rule for the whole library: ANTICIPATION LIVES INSIDE THE CLIP THAT NEEDS
# IT, never in the transition. The transition carries no expressive content --
# it only travels between two held poses, and its duration scales with distance.
HOLD_IN_S = 0.20       # long enough to read as a stop rather than a slowdown.
HOLD_OUT_S = 0.20      # Shorter and the boundary blurs; much longer and a
                       # near-state answer starts to feel late.

# ---- timing: three beats, chained by overlap ----
#     RISE   sit up out of the sleeping pose   (tilt + nod together)
#     TURN   look at the speaker               (pan)
#     LEAN   settle in to listen               (tilt forward, nod compensating)
#
# Each beat starts before the previous one finishes, so there is never a dead
# frame between them (Lasseter 1987, overlapping action). Three readable beats,
# one continuous movement. The alternative -- letting each beat stop before the
# next begins -- would insert two event boundaries INSIDE the state and split
# one act into three (Newtson 1973; Zacks et al. 2007).
#
# Fast enough to feel like an answer to the button, slow enough not to startle
# at a metre. The turn is the one that has to land early: the person should find
# the robot facing them before they finish their first sentence, and it
# completes at 0.90 s. The lean arriving after that is follow-through, not lag.
RISE_S = 0.50          # the whole posture comes up
TURN_AT_S = 0.30       # turn starts before the rise finishes -- waking and
TURN_S = 0.80          # orienting read as one act rather than two
                       #
                       # 0.80, not 0.60, AND THE REASON IS THE RIG, not the
                       # timing. The pan axis is in the base; the head sits
                       # 128 mm up the neck. WHEN THE NECK IS VERTICAL THE HEAD
                       # IS ON THE PAN AXIS, so panning spins it in place and
                       # moves it almost nowhere -- 9 mm off-axis at tilt +4.
                       #
                       # With the turn finishing at 0.90 it was over before the
                       # lean had swung the head out, and the whole 50 deg
                       # carried only 26.7 mm of head travel: the SMALLEST
                       # displacement in the clip, against 57.8 mm for the lean.
                       # It did not read as 50 deg because as motion it was not.
                       #
                       # Extending it to 0.80 s lets the turn ride the lean, so
                       # the radius grows underneath it: 58.4 mm, 2.2x, with no
                       # change to any angle. Peak also drops 156 -> 117 deg/s.
                       #
                       # LIBRARY-WIDE: on this form pan produces visible
                       # displacement only while the neck is tilted. Any state
                       # whose meaning is carried by turning should turn while
                       # leaning, or accept that its pan reads as orientation
                       # rather than as movement.
LEAN_AT_S = 0.70       # lean starts before the turn finishes, for the same
LEAN_S = 0.60          # reason. It is also what replaces a settle: the state
                       # comes to rest because a DIFFERENT axis is still
                       # arriving, which is follow-through. No overshoot -- an
                       # overshoot would read as eagerness, and S2's job is to
                       # receive, not to volunteer.

# The head's rise TRAILS the neck's lean -- it starts WITH it and takes longer,
# so it is the last thing in the clip to settle.
#
# Meeting the speaker's eyes should be the last thing that happens -- it is the
# moment of contact, and a state that arrives at it early has nothing left to
# land on. The obvious way to get that is to finish the lean, pause, then lift
# the head. That was rejected: a pause IS an event boundary (Newtson 1973;
# Zacks et al. 2007), so it would split S2 into two perceived units -- "came
# closer", then separately "looked at you" -- and S2 has to be one act. It also
# pushes a near-distance answer past 1.8 s.
#
# Overlapping instead gets the same ordering for free. Successive breaking of
# joints (Lasseter 1987): body parts neither start nor stop together, and the
# trailing part settles last. So the gaze still arrives last, but as the tail of
# the lean rather than as a separate event.
#
# The head starts WITH the neck and takes 0.15 s longer, rather than starting
# late. Both give a late arrival, but a delayed start makes the head look
# separately commanded, while a longer travel makes it look carried. A trailing
# part in animation is one that lags because it is being dragged, not one that
# waits its turn.
#
# It also controls the side effect. While the neck runs ahead of the head the
# gaze DIPS -- the head is being carried down by the body before it catches up --
# and that dip is worth having at 2 deg and not at 6. A delayed start
# (0.80 s / 0.45 s) was tried and produced 6.3 deg, which swings the gaze BELOW
# horizontal onto the desk edge; in a system where pointing is attention that is
# a claim about the wrong referent. A 6 deg downward head movement is also S3's
# morpheme, and S2 should not spell a piece of the next state.
#
# Note that ENLARGING the lift SHRINKS the dip, which is why the head could be
# given both more travel and more time at once. A bigger total excursion climbs
# faster through the early part of the same ease curve, so it catches the neck
# sooner even though it finishes later. Amplitude and trail are not in tension
# here; amplitude and dip trade in the designer's favour.
#
# 0.70 / 0.70 gives a 30 deg lift, a 2.6 deg dip with a floor of +3.4 deg (never
# below horizontal), and the head settling 0.30 s after the neck.
LEAN_NOD_AT_S = 0.70
LEAN_NOD_S = 0.90

# ---- LED ----
# The hue changes warm -> cool at the state change (firmware, EVT HUE), which is
# the palette's "attending" colour. The LEVEL, though, is the point: in S1 the
# light breathes on its own clock, and in S2 it follows the person's voice.
# The SOURCE of the light's motion changes from endogenous to exogenous, and that
# is a legible difference which costs nothing -- the mic level is already
# streamed for the recording UI.
#
# So this clip authors only the RISE into the attending level and then holds it
# flat; the live level takes over for the duration of the hold.
# The light moves TWICE, and the second move is an accent on the eye contact.
#
# One rise to the attending level was the earlier design and it under-sold the
# clip's own climax: the body arrived at 0.7 s and the light was finished, while
# the thing the state is actually about -- the gaze reaching the face -- happened
# a second later in silence on this channel. At room scale the light is the only
# channel that survives, so a state whose defining moment is unmarked in light is
# unmarked, full stop.
#
# IT MUST NOT COLLIDE WITH S3. S3's accent is a SWELL on the nod -- up and back
# down, a transient on a steady level. S2's is a monotone CLIMB TO A NEW HELD
# LEVEL. Different envelope shapes carrying the difference the two states
# actually have: S2 marks a state being ENTERED (eye contact, from here on),
# S3 marks an EVENT that has PASSED (received). Level change versus transient.
LED_SLEEP = 0.30       # = S1's LED_LO, so the clip starts where S1 left off
LED_WAKE = 1.2         # partway up, reached with the RISE -- awake, not yet
                       # attending to anyone in particular. 38/255.
                       #
                       # Kept low because BRIGHTNESS PERCEPTION IS COMPRESSIVE.
                       # Stevens' power law puts the exponent near 0.4, so a PWM
                       # step of 57 -> 96 -- a 68% rise in the number -- is only
                       # about 25% brighter to the eye, which is not an accent.
                       # 38 -> 96 is 2.5x in PWM and roughly 1.5x perceived,
                       # which is. Authoring against the linear number is how a
                       # light accent quietly turns into no accent at all.
                       #
                       # The accent is bought here rather than by raising
                       # LED_ATTEND, because 3.0 is the shared WORKING level
                       # across S2-S5 (LED_PALETTE sec 3) and breaking that
                       # alignment would cost more than the accent is worth.
LED_ATTEND = 3.0       # the attending level (LED_PALETTE), reached with the
                       # HEAD, which means it lands with the gaze, last.
                       # The mic level takes over from here for the hold.

FPS = 30
SAMPLE_F = 1           # this clip is short and all of it is moving, so sample
                       # finely -- the ease curves are the content here
# -----------------------------------------------------------

HOLD_IN_F = int(round(HOLD_IN_S * FPS))
HOLD_OUT_F = int(round(HOLD_OUT_S * FPS))
RISE_F = int(round(RISE_S * FPS))
TURN_AT_F = int(round(TURN_AT_S * FPS))
TURN_F = int(round(TURN_S * FPS))
LEAN_AT_F = int(round(LEAN_AT_S * FPS))
LEAN_F = int(round(LEAN_S * FPS))
LEAN_NOD_AT_F = int(round(LEAN_NOD_AT_S * FPS))
LEAN_NOD_F = int(round(LEAN_NOD_S * FPS))
MOVE_F = max(RISE_F, TURN_AT_F + TURN_F, LEAN_AT_F + LEAN_F,
             LEAN_NOD_AT_F + LEAN_NOD_F)
END_F = HOLD_IN_F + MOVE_F + HOLD_OUT_F

EASE_MODE = "minjerk"      # "minjerk" | "cosine"

# ---- guard rails, read from the live calibration ----
# Checked BEFORE any keys are written, so a bad number fails here rather than
# silently on the robot. Two ways to author something that cannot play:
#   too big  -- the servo stops at the guard rail and the settle disappears
#   too small -- the servo acknowledges and never moves (amplitude floor)
# Neither is visible in Blender or in the exported CSV, so both are checked.
import os
import sys

# Escape hatch. Set this to the absolute path of motion/blender/reach.py if the
# .blend lives outside the repo, so there is nothing to walk up from.
REACH_PATH = ""


def _find_up(rel, starts, levels=8):
    """Walk UP from each start looking for `rel`, checking each level's
    immediate subdirectories too.

    Both halves are needed. Fixed relative candidates did not survive the
    .blend being saved outside motion/src/. A pure upward walk then did not
    survive the real layout either: the .blend lives in the local design folder
    (robot_motion/model/vN/) while the generators live in the repo
    (notice-sidekick-runkit/motion/blender/), so the two are SIBLINGS under a
    common ancestor that holds the repo as a subfolder."""
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
        "  REACH_PATH        = " + repr(REACH_PATH) + "\n"
        "  bpy.data.filepath = " + repr(bpy.data.filepath) + "\n"
        "  cwd               = " + os.getcwd() + "\n"
        "If bpy.data.filepath is empty the .blend has never been saved, so "
        "there is nothing to walk up from -- save it and re-run. If the .blend "
        "lives outside the repo, set REACH_PATH at the top of this block to the "
        "absolute path of reach.py.")

# Loaded by PATH, not by `import`. Blender's Python session is long-lived and
# caches modules, so `import reach` would keep serving a stale copy after
# calibration changes -- exactly when a guard must not be stale.
reach = type(sys)("reach")
reach.__file__ = _rp
with open(_rp) as _fh:
    exec(compile(_fh.read(), _rp, "exec"), reach.__dict__)
print("[s2] " + reach.summary())

# RENDER-ONLY ESCAPE HATCH, for pan and pan only.
#
# USER_PAN is a template that the player retargets at runtime, so the question
# "how far does it turn" is really "where is the person allowed to sit" -- a
# deployment question, not an authoring one. Set this True to AUTHOR a pan the
# current guard rail cannot reach, so the turn can be judged in the viewport
# before deciding whether to widen the rail. The exported CSV WILL clip on
# hardware; put it back to False before exporting anything that gets played.
PAN_RENDER_ONLY = False

if PAN_RENDER_ONLY:
    _lo, _hi = reach.REACH["pan"]
    if not (_lo <= USER_PAN <= _hi):
        print("[s2] !! PAN_RENDER_ONLY: pan %+.1f deg is outside the reachable "
              "%+.1f..%+.1f. This render is NOT playable -- widen the pan guard "
              "rail (currently %s) and turn this off before exporting."
              % (USER_PAN, _lo, _hi, reach.LIMITS["pan"]))
else:
    reach.check("pan", USER_PAN, "speaker")

for _j, _v, _w in (("pan", 0.0, "start"),
                   ("tilt", SLEEP_TILT, "sleep"), ("tilt", UPRIGHT_TILT, "upright"),
                   ("tilt", LISTEN_TILT, "lean"),
                   ("nod", SLEEP_NOD, "sleep"), ("nod", UPRIGHT_NOD, "upright"),
                   ("nod", LISTEN_NOD, "lean")):
    reach.check(_j, _v, _w)
for _j, _a, _b, _w in (("tilt", SLEEP_TILT, UPRIGHT_TILT, "rise"),
                       ("nod", SLEEP_NOD, UPRIGHT_NOD, "rise"),
                       ("tilt", UPRIGHT_TILT, LISTEN_TILT, "lean"),
                       ("nod", UPRIGHT_NOD, LISTEN_NOD, "lean"),
                       ("pan", 0.0, USER_PAN, "turn")):
    reach.check_floor(_j, _b - _a, _w)
for _w, _d, _t in (("rise nod", UPRIGHT_NOD - SLEEP_NOD, RISE_S),
                   ("rise tilt", UPRIGHT_TILT - SLEEP_TILT, RISE_S),
                   ("turn pan", USER_PAN, TURN_S),
                   ("lean tilt", LISTEN_TILT - UPRIGHT_TILT, LEAN_S),
                   ("lean nod", LISTEN_NOD - UPRIGHT_NOD, LEAN_NOD_S)):
    # Peak factor depends on the easing profile: pi/2 for the raised cosine,
    # 1.875 for minimum jerk. Using the wrong one under-reports by 19%, which is
    # exactly the margin a fast beat has left.
    _pk = abs(_d) / _t * (math.pi / 2.0 if EASE_MODE == "cosine" else 1.875)
    if _pk > 200.0:
        raise RuntimeError(f"{_w} peaks at {_pk:.0f} deg/s under EASE_MODE="
                           f"{EASE_MODE!r}, over the 200 deg/s ceiling. "
                           f"Lengthen the beat or shrink the angle.")

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
    led_color.default_value = (0.24, 0.59, 0.90, 1.0)     # cool: attending
    mat.diffuse_color = (0.24, 0.59, 0.90, 1.0)


def key_led(frame, value):
    if led_strength is not None:
        led_strength.default_value = value
        led_strength.keyframe_insert(data_path="default_value", frame=frame)


def ease(t):
    """0..1, zero velocity at both ends. Slow in, slow out.

    TWO PROFILES, AND THE CHOICE IS CITABLE.

    "cosine"  -- 0.5(1 - cos pi t). What this library used first. Grounded only
                 in Lasseter's slow in / slow out, i.e. in animation craft.
                 Velocity is zero at the ends but ACCELERATION IS MAXIMUM there,
                 so every beat begins and ends with an acceleration step.

    "minjerk" -- 10t^3 - 15t^4 + 6t^5, the minimum-jerk trajectory (Flash &
                 Hogan 1985). This is the velocity profile human point-to-point
                 movement actually has -- an empirical model of motor control,
                 not a studio convention -- and its acceleration is ZERO at both
                 ends, so a beat starts and stops without a step.

    Cost of the switch, measured rather than assumed:
      * peak velocity is 19.4% higher for the same distance and duration
        (1.875x average vs 1.571x). Worst beat here goes 138 -> 165 deg/s,
        still clear of the 200 deg/s ceiling.
      * two more frames per beat command a step under one servo unit, at the
        very start and end. That is NOT the stutter this project hit before:
        that failure was a gesture whose WHOLE excursion sat under the
        amplitude floor. Here it is the tails, where the joint is meant to be
        nearly stationary, and zero endpoint acceleration is precisely what
        makes the departure imperceptible. No mid-move frame is under-driven.

    Keep both. Rendering the same clip either way is the cheapest evidence that
    the choice was made rather than inherited -- and see S2_DESIGN.md sec 7.6:
    the one HRI experiment that isolated easing against linear motion found NO
    reliable effect, so this is adopted as a default, not claimed as a benefit.
    """
    t = max(0.0, min(1.0, t))
    if EASE_MODE == "cosine":
        return 0.5 * (1.0 - math.cos(math.pi * t))
    return t * t * t * (10.0 + t * (-15.0 + 6.0 * t))


def lerp(a, b, t):
    return a + (b - a) * t


for f in range(1, END_F + 1, SAMPLE_F):
    # i is measured from the END of the opening hold, so the holds fall out of
    # the clamping in ease() rather than needing their own branches.
    i = (f - 1) - HOLD_IN_F
    t_rise = ease(i / float(RISE_F))
    # Two lean schedules, not one: the neck leads, the head trails.
    t_lean = ease((i - LEAN_AT_F) / float(LEAN_F)) if i > LEAN_AT_F else 0.0
    t_lean_nod = (ease((i - LEAN_NOD_AT_F) / float(LEAN_NOD_F))
                  if i > LEAN_NOD_AT_F else 0.0)

    # Both pitch joints run the same two-stage chain: sleeping -> upright ->
    # listening. Nesting the lerps rather than branching keeps the ease curves
    # continuous across the handover, and because the rise finishes (0.50 s)
    # before either lean begins (0.70 / 0.80 s) the two stages never fight.
    tilt_deg = lerp(lerp(SLEEP_TILT, UPRIGHT_TILT, t_rise), LISTEN_TILT, t_lean)
    nod_deg = lerp(lerp(SLEEP_NOD, UPRIGHT_NOD, t_rise), LISTEN_NOD, t_lean_nod)

    # The turn, overlapping the tail of the rise and the head of the lean.
    t_turn = ease((i - TURN_AT_F) / float(TURN_F)) if i > TURN_AT_F else 0.0
    pan_deg = lerp(0.0, USER_PAN, t_turn)

    key(pan, "z", f, pan_deg)
    key(tilt, "x", f, tilt_deg)
    key(nod, "x", f, nod_deg)
    # The light rides the HEAD's two curves -- the same t_rise and t_lean_nod the
    # nod uses -- rather than carrying an envelope of its own. So the accent
    # cannot drift out of sync with the gaze: it is not timed to the eye contact,
    # it is DRIVEN BY it, and any later change to the head's schedule moves the
    # light with it for free.
    #
    # Nothing is keyed to the turn. Panning is where the body goes, not what the
    # robot knows, and the light reports the latter.
    key_led(f, lerp(lerp(LED_SLEEP, LED_WAKE, t_rise), LED_ATTEND, t_lean_nod))

# Land exactly on the pose S3 expects. This is a one-shot clip whose last frame
# is a handover, not a loop seam -- if it does not match S3's opening the player
# has to insert an unauthored bridge, and the two states stop reading as one
# exchange.
key(pan, "z", END_F, USER_PAN)
key(tilt, "x", END_F, LISTEN_TILT)
key(nod, "x", END_F, LISTEN_NOD)
key_led(END_F, LED_ATTEND)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = END_F

peak_nod = math.pi * abs(UPRIGHT_NOD - SLEEP_NOD) / (2.0 * RISE_S)
peak_tilt = math.pi * abs(UPRIGHT_TILT - SLEEP_TILT) / (2.0 * RISE_S)
peak_pan = math.pi * USER_PAN / (2.0 * TURN_S)
gaze_drop = (UPRIGHT_TILT + UPRIGHT_NOD) - (LISTEN_TILT + LISTEN_NOD)
msg = (f"S2 listen, 3 beats: RISE tilt {SLEEP_TILT:+.0f}->{UPRIGHT_TILT:+.0f} "
       f"nod {SLEEP_NOD:+.0f}->{UPRIGHT_NOD:+.0f} in {RISE_S:.2f}s | "
       f"TURN {USER_PAN:.0f} deg from {TURN_AT_S:.2f}s over {TURN_S:.2f}s | "
       f"LEAN tilt ->{LISTEN_TILT:+.0f} nod ->{LISTEN_NOD:+.0f} from "
       f"{LEAN_AT_S:.2f}s over {LEAN_S:.2f}s; "
       f"{END_F}f ({END_F / FPS:.2f}s), peak nod {peak_nod:.0f} / "
       f"tilt {peak_tilt:.0f} / pan {peak_pan:.0f} deg/s")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(
        text=f"Lean: neck {abs(LISTEN_TILT - UPRIGHT_TILT):.0f} deg forward, head "
             f"{abs(LISTEN_NOD - UPRIGHT_NOD):.0f} deg back -> gaze drops only "
             f"{gaze_drop:.0f} deg. Body arrives, eyes stay.")
    self.layout.label(text="Starts on S1's sleeping pose; ends on S3's opening pose.")
    self.layout.label(text="The state HOLDS here (states.py then=None) until a")
    self.layout.label(text="transcript exists -- the hold is not in this clip.")
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(draw, title="S2 listen", icon='INFO')
