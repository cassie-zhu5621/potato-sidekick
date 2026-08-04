# Auto-generates S6 FINE-TUNE (3-DOF + LED). Run inside S6_finetune.blend after
# repair_rig.py + add_nod_joint.py. OVERWRITES all keys.
# Design rationale: ../../../robot_motion/S6_DESIGN.md (local, not in this repo).
#
# The user has tapped the body: "not that one." S6 is the answer, and the state
# holds afterwards, waiting for a direction.
#
# IT HAPPENS WHERE THE WRONG THING IS.
#
#   The old clip opened and closed at 0/0/0 while S5b holds 25/-12/+12, so
#   entering it meant an unauthored 25 deg pan swing plus an un-crane -- about
#   0.6 s of travel nobody designed. The actual sequence was:
#
#     tap -> [turn 25 deg away and straighten] -> startle -> droop
#         -> shake "not that" -> perk -> [turn 25 deg back] -> S5b
#
#   THE NEGATION WAS PERFORMED POINTING AT NOTHING. Not at the wrong object, not
#   at the person -- and the robot abandoned the wrong target BEFORE being told
#   anything, then went back to it. "Not THAT one" needs its referent still in
#   view, so this clip now opens and closes on S5b's held pose and shakes there.
#
#   Same class of error as S3's absolute NOD_DIP: a pose written as an absolute
#   target loses its meaning the moment a neighbouring state moves.
#
# THE APOLOGY STAYS, AND THE ARGUMENT FOR CUTTING IT WAS WRONG.
#
#   A draft of this file removed the abashed droop, on the reasoning that a body
#   tap is the user's ONLY lever (S4/S5 has no confirmation step) and a lever
#   pulled repeatedly must stay cheap -- so an apologetic robot would make people
#   correct it less. That argument was invented, and the evidence points the
#   other way.
#
#   Lee, Kiesler, Forlizzi, Srinivasa & Rybski (2010), "Gracefully Mitigating
#   Breakdowns in Robotic Services", HRI'10, compared mitigation strategies after
#   a robot service failure. An apology scored higher on politeness, COMPETENCE,
#   trust, likeability, closeness -- and on INTENTION TO USE THE ROBOT AGAIN,
#   which is precisely the quantity the deleted-apology argument claimed to be
#   protecting.
#
#   The same paper gives the structure this clip should have. Two strategies, two
#   different gains:
#
#     identify the error + state the intention to fix it  -> more CAPABLE
#     apologise                                           -> more LIKEABLE, and
#                                                            more likely to be
#                                                            used again
#
#   The three-beat shape already had both -- droop ("my bad"), shake ("not that
#   one"), perk ("ok, show me" = the intention to rectify). Cutting the droop
#   threw away half of a design that was already right.
#
#   ONE CONCERN SURVIVES, AS AN OPEN QUESTION RATHER THAN A DECISION. Lee et al.
#   measured a ONE-OFF service breakdown. S6 fires every time the user steers,
#   possibly several times a session, and nobody has tested whether a repeated
#   apology keeps helping or starts to cost. Worth a line in the limitations, and
#   a candidate manipulation: droop depth is a single constant here.
#
# THE NECK COMES OUT OF THE LEAN FIRST, AND THAT IS THE POINT.
#
#   S5b's forward lean is EPISTEMIC -- it is not a posture, it is the act of
#   attending to that object (S7_DESIGN sec 2). Refusing the object therefore has
#   to begin by LEAVING THAT POSTURE. Shaking while still craned at the thing is
#   a contradiction between posture and gesture: the body says "I am studying
#   this" while the head says "not this".
#
#   pan and tilt separate cleanly, which is what makes it work:
#
#     pan  HOLDS at the target   -> the referent stays indicated: "not THAT one"
#     tilt COMES UP and back     -> disengaged from it, no longer peering
#
#   And it settles the ending. S6 closes UPRIGHT, not back on S5b's craned hold.
#   Re-craning onto the object it has just refused would contradict the refusal;
#   upright-and-level is the posture of waiting to be told, which is exactly what
#   the state does (states.py then=None, waiting for a direction). If no direction
#   arrives, REAIM_TIMEOUT_S sends it back to S5b and the re-crane is an ordinary
#   transition -- "nobody told me, back to what I was doing."
#
# THE SHAKE IS ON PAN, WHICH BREAKS THE TWO-LAYER AXIS ON PURPOSE.
#
#   pan and tilt are the ambient/body layer; nod is the communication layer.
#   Negation is pure communication -- and it has to be spoken with the body
#   anyway, because THE HEAD HAS ONLY PITCH. There is no horizontal axis above
#   the neck, so a head-shake is mechanically impossible and the whole body must
#   perform it.
#
#   That is the single structural exception in the grammar, and it explains why
#   the negation needs three channels rather than one: the motion is speaking in
#   the wrong layer, so colour (red) and rhythm (a flash per extreme) carry the
#   load the gesture cannot carry alone.
#
# SIGN CONVENTION: BLENDER positive nod = head UP; on the bus a higher unit is
# DOWN. INVERT in robot/calibration.py reconciles them. Author against the render.

import bpy
import math

# ---- the pose it inherits AND returns to (match generate_s5b_track.py) ----
# S6 is entered from S5b and exits back to it, so both ends are S5b's hold. The
# shake is performed AT the wrong target, which is what makes it "not THAT one".
HOLD_PAN = 25.0
HOLD_TILT = -12.0
HOLD_NOD = 12.0        # exactly cancels the lean: the held gaze is level

# ---- the pose it ENDS on: upright, level, still facing the target ----
END_TILT = 0.0         # out of the epistemic lean -- no longer studying it
END_NOD = 0.0          # gaze level: 0 + 0 = 0. Attentive, waiting to be told.

# ---- 1. release the lean, and droop ----
# One movement, not two. Coming out of the forward lean IS the first half of the
# abashed posture: the neck pulls BACK past vertical while the chin drops, which
# is contraction rather than extension. Kip1 (Hoffman et al., HRI'15) found the
# same polarity in its animation studies -- rising and stretching read as
# confidence and curiosity, and its scared state "retracts to a fully cowering
# state". Abashed lives at the same end of the range as calm, not the curious end.
DROOP_TILT = 4.0       # neck slightly BACK of vertical: retracted, not reaching
DROOP_NOD = -24.0      # chin well down. Gaze +4 - 24 = -20, looking at the floor
                       # rather than at the object or the person.
RELEASE_S = 0.55       # lean -> droop. 36 deg of nod, peaking at 123 deg/s.
DROOP_HOLD_S = 0.25    # the beat that makes it read as "...my bad" rather than
                       # as a transit. Half the old version's, which held long
                       # enough to become the subject of the clip.

# ---- 2. the shake: "not that one" ----
SHAKE_DEG = 7.0        # amplitude from the held aim, so 14 deg peak-to-peak.
                       # Well clear of pan's 1.76 deg floor, and small enough to
                       # stay a gesture rather than a re-aim -- the wrong target
                       # never leaves the frame while the robot refuses it.
SHAKE_HZ = 2.4         # one full left-right cycle per 0.42 s. Fast enough to
                       # read as a shake rather than a look-around, and the same
                       # band as S3's nod (2.5 Hz): affirmation and negation at
                       # the same tempo in opposite axes, which is what makes
                       # them a pair rather than two unrelated gestures.
N_SHAKE = 2            # Hadar, Steiner & Clifford Rose (1985): the feature that
                       # marks 'yes'/'no' is CYCLICITY -- symmetrical and
                       # repeated, as against the linear movements that do
                       # turn-taking and entrainment.
SHAKE_RATIO = 0.545    # the second cycle at 55% of the first.
                       #
                       # EXTRAPOLATED, and it must be flagged as such. This is
                       # Kimura & Jokinen et al.'s declination x final-lowering
                       # ratio and their corpus is NODS; nobody has fitted it to
                       # head-shakes. It is used because the REASON transfers
                       # even where the data does not: equal repeats read as
                       # several twitches, a declining pair reads as one gesture.
                       # Set it to 1.0 for the equal-amplitude variant.

# ---- 3. perk up: "ok, show me" ----
# NOT a decorative recovery. In Lee et al.'s terms this is the SECOND mitigation
# strategy -- the intention to rectify -- and it is the beat that earns the
# "capable" rating the apology alone does not. It is also functionally true: the
# state now waits for a direction, and coming up to attentive is what waiting
# looks like.
#
# It gets no LED accent. The refusal was the event; giving the recovery its own
# flash would put the brightest moment of the clip on "I'm fine now" while the
# meaning is "not that one" -- which is exactly the mistake v1 made.
PERK_S = 0.40

# ---- boundaries ----
HOLD_IN_S = 0.20
HOLD_OUT_S = 0.20

# ---- LED ----
# The negation runs on THREE channels at once, all on the same beat: colour
# (red), motion (the shake), and rhythm (a flash per extreme). Any one can fail
# -- red/green deficiency, a participant looking away, peripheral vision that
# misses a small shake -- and the meaning survives.
#
# v1 had this backwards in a way that is easy to miss: the light sat flat through
# the shake and then double-blinked during the perk-up, so the brightest moment
# of the clip meant "I'm fine now" while the head was saying "no".
LED_BASE = 3.0         # attentive, at the start and after the perk
LED_DROOP = 1.0        # deflates WITH the droop and stays down through the
                       # shake, so the flashes have something dark to punch out
                       # of. v1 sat flat here and blinked during the perk-up
                       # instead -- putting the brightest moment of the clip on
                       # "I'm fine now" while the head was saying "no".
LED_FLASH = 6.0        # on each shake extreme. This is the "no".

EASE_MODE = "minjerk"  # Flash & Hogan 1985 -- see generate_s2_listen.py
FPS = 30
SAMPLE_F = 1           # EVERY frame. At SAMPLE_F=2 the shake's extremes fell on
                       # odd offsets and were never sampled: the pan amplitude
                       # exported as 6.1 deg instead of 7.0, and the LED peaked at
                       # 141/255 -- under the firmware's 150 flash threshold, so
                       # the accent that carries the negation would have been a
                       # bump. A periodic signal has to be sampled where its
                       # extremes are, and the cheapest guarantee is every frame.
# -----------------------------------------------------------

HOLD_IN_F = int(round(HOLD_IN_S * FPS))
HOLD_OUT_F = int(round(HOLD_OUT_S * FPS))
RELEASE_F = int(round(RELEASE_S * FPS))
DROOP_HOLD_F = int(round(DROOP_HOLD_S * FPS))
CYCLE_F = int(round(FPS / SHAKE_HZ))
SHAKE_F = N_SHAKE * CYCLE_F
PERK_F = int(round(PERK_S * FPS))
MOVE_F = RELEASE_F + DROOP_HOLD_F + SHAKE_F + PERK_F
END_F = HOLD_IN_F + MOVE_F + HOLD_OUT_F
SHAKE2 = SHAKE_DEG * SHAKE_RATIO

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
print("[s6] " + reach.summary())

if abs(HOLD_TILT + HOLD_NOD) > 0.01:
    raise RuntimeError(f"the inherited gaze is not level: tilt+nod = "
                       f"{HOLD_TILT + HOLD_NOD:+.1f}. S6 opens and closes on "
                       f"S5b's hold, so these must match generate_s5b_track.py.")
for _j, _v, _w in (("pan", HOLD_PAN + SHAKE_DEG, "shake +"),
                   ("pan", HOLD_PAN - SHAKE_DEG, "shake -"),
                   ("tilt", HOLD_TILT, "inherited lean"),
                   ("tilt", DROOP_TILT, "retracted"), ("tilt", END_TILT, "upright"),
                   ("nod", HOLD_NOD, "inherited counter"),
                   ("nod", DROOP_NOD, "chin down"), ("nod", END_NOD, "level")):
    reach.check(_j, _v, _w)
for _j, _d, _w in (("pan", SHAKE_DEG, "shake cycle 1"),
                   ("pan", SHAKE2, "shake cycle 2"),
                   ("tilt", DROOP_TILT - HOLD_TILT, "release"),
                   ("nod", HOLD_NOD - DROOP_NOD, "chin drop"),
                   ("tilt", DROOP_TILT - END_TILT, "perk, neck"),
                   ("nod", END_NOD - DROOP_NOD, "perk, chin")):
    reach.check_floor(_j, _d, _w)
_FAC = math.pi / 2.0 if EASE_MODE == "cosine" else 1.875
_shake_pk = 2.0 * math.pi * SHAKE_HZ * SHAKE_DEG          # sine, exact
for _w, _pk in (("shake", _shake_pk),
                ("release nod", abs(DROOP_NOD - HOLD_NOD) / RELEASE_S * _FAC),
                ("perk nod", abs(END_NOD - DROOP_NOD) / PERK_S * _FAC)):
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
    led_color.default_value = (1.00, 0.16, 0.12, 1.0)   # RED = "not that one"
    mat.diffuse_color = (1.00, 0.16, 0.12, 1.0)


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


def shake_offset(i):
    """Pan offset from the held aim. Declining cycles sampled from a sine, so the
    extremes are turning points rather than stops -- a shake is continuous, unlike
    the nod, whose stroke and recovery are separate phases (Kendon)."""
    if i < 0 or i >= SHAKE_F:
        return 0.0
    c = int(i // CYCLE_F)
    return SHAKE_DEG * (SHAKE_RATIO ** c) * math.sin(
        2.0 * math.pi * (i % CYCLE_F) / float(CYCLE_F))


SHAKE_AT = RELEASE_F + DROOP_HOLD_F        # the shake happens IN the droop
PERK_AT = SHAKE_AT + SHAKE_F

for f in range(1, END_F + 1, SAMPLE_F):
    i = (f - 1) - HOLD_IN_F

    # 1. release the lean into the droop, then hold it a beat, then 3. perk up.
    #    One pitch schedule for the whole clip: lean -> droop -> upright.
    if i < 0:
        t_rel, t_perk = 0.0, 0.0
    else:
        t_rel = ease(i / float(RELEASE_F))
        t_perk = ease((i - PERK_AT) / float(PERK_F)) if i > PERK_AT else 0.0
    tilt_deg = lerp(lerp(HOLD_TILT, DROOP_TILT, t_rel), END_TILT, t_perk)
    nod_deg = lerp(lerp(HOLD_NOD, DROOP_NOD, t_rel), END_NOD, t_perk)

    # 2. the shake, performed IN the drooped posture and ON the target's bearing.
    j = i - SHAKE_AT
    key(pan, "z", f, HOLD_PAN + shake_offset(j))
    key(tilt, "x", f, tilt_deg)
    key(nod, "x", f, nod_deg)

    if 0 <= j < SHAKE_F:
        phase = (j % CYCLE_F) / float(CYCLE_F)
        # |sin| peaks at each extreme; cubed so the flash is short and the gap
        # between extremes goes properly dark.
        sh = abs(math.sin(2.0 * math.pi * phase)) ** 3
        key_led(f, lerp(LED_DROOP, LED_FLASH, sh))
    elif i < 0 or i > PERK_AT:
        # attentive at both ends; the light comes back up with the perk
        key_led(f, lerp(LED_DROOP, LED_BASE, t_perk) if i > PERK_AT else LED_BASE)
    else:
        # deflating with the droop, and staying down through it
        key_led(f, lerp(LED_BASE, LED_DROOP, t_rel))

# Land UPRIGHT on the target's bearing: refused, and waiting to be told.
key(pan, "z", END_F, HOLD_PAN)
key(tilt, "x", END_F, END_TILT)
key(nod, "x", END_F, END_NOD)
key_led(END_F, LED_BASE)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = END_F

# states.py fires the 'puzzled' sound. It belongs to the FIRST shake extreme --
# the moment the refusal becomes legible -- not to the acknowledgement dip, which
# is where the old 0.20 put it.
SFX_AT = (HOLD_IN_F + SHAKE_AT + CYCLE_F / 4.0) / float(END_F)
msg = (f"S6: release the lean into a droop (tilt {HOLD_TILT:+.0f}->{DROOP_TILT:+.0f}, "
       f"nod {HOLD_NOD:+.0f}->{DROOP_NOD:+.0f}, gaze {DROOP_TILT + DROOP_NOD:+.0f}) -> "
       f"{N_SHAKE} shake cycles {SHAKE_DEG:.0f}/{SHAKE2:.1f} deg at {SHAKE_HZ:.1f} Hz "
       f"-> perk UPRIGHT; {END_F}f ({END_F / FPS:.2f}s), shake peak {_shake_pk:.0f} deg/s")
print(msg)
print(f"[s6] set states.py S6_FINETUNE sfx_at = {SFX_AT:.2f}")


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text="Opens AND closes on S5b's hold, so the shake happens")
    self.layout.label(text="AT the wrong target: 'not THAT one'.")
    self.layout.label(text="Droop = apology (Lee et al. 2010: raises trust AND")
    self.layout.label(text="intention to use). Perk = intention to rectify.")
    self.layout.label(text="Ends UPRIGHT: refused, waiting to be told.")
    self.layout.label(text=f"SET states.py S6_FINETUNE sfx_at = {SFX_AT:.2f}")


bpy.context.window_manager.popup_menu(draw, title="S6 fine-tune", icon='INFO')
