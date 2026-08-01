# Auto-generates S7a found (one-shot intro, 3-DOF). Run inside S7a_found.blend
# after repair_rig.py + add_nod_joint.py. Overwrites pan/tilt/nod/LED keys.
#
# Sequence: notice crouch -> ATTENTION-GET (turn to the user, eye contact, no
# lean) -> DIRECT (turn to the found object, the neck cranes toward IT) -> HOLD.
#
# v3 (2026-07-29). One structural change, and it is the important one:
#
#   THE CLIP NOW LOOKS AT THE THING IT FOUND.
#
#   v2 turned to the user, leaned at the user, made eye contact, and stopped.
#   It never oriented to the finding at all -- which meant the clip was
#   bit-identical no matter what was found or where it was. That fails
#   movement <=> result: a movement must be the signature of a real detection
#   that changes the captured result, and a clip invariant to the finding is
#   theatre. Apply the remove-it test to v2 -- delete the object and the robot
#   performs exactly the same sequence.
#
#   It was also only beat 1 of the three the design calls for. FIND_AND_SHARE.md
#   Sec.3 specifies attention-get -> direct -> ensure (Mundy's initiating joint
#   attention; Huang & Thomaz 2011 on Simon). v2 was attention-get, then S7b
#   looped on attention-get. This clip now carries beats 1 and 2; S7b carries 3.
#
#   Consequence for the lean: it MOVES TO THE OBJECT LEG. form_interaction_design
#   distinguishes an EPISTEMIC lean (toward a thing/region = curious looker, the
#   novel move) from a SOCIAL lean (into a person's space = conversational
#   participant, to be avoided). v2 leaned at the user, i.e. the banned one.
#
#   And OBJECT_NOD is not a levelling term. v2's END_NOD existed to cancel the
#   lean's downward pitch so the gaze came back to horizontal. Here the head must
#   end up aimed AT THE OBJECT, so the nod cancels the lean AND adds the object's
#   elevation. Levelling and aiming are different jobs.
#
# The final pose IS S7b's frame 1. Both files hold the object leg; keep them in
# sync or the loop will jump on entry.
#
# SIGN CONVENTION, two frames that run opposite:
#   BLENDER, what you author here:   positive nod = head UP
#   SERVO UNITS, what the bus sees:  higher unit  = head DOWN
# INVERT in hardware/calibration.py reconciles them. Always author against the
# render; the servo frame is an implementation detail INVERT absorbs.
#
# See docs/S7_DESIGN.md for the reference behind every beat.

import bpy
import math

# ---- keep in sync with generate_s7_beckon.py ----
# Both pans are TEMPLATES. The player retargets them to the real user and object
# directions at runtime -- see S7_DESIGN.md Sec.5. The object leg's pan IS the
# detection, which is what makes this clip satisfy movement <=> result.
USER_PAN = 60.0
OBJECT_PAN = -25.0    # signed opposite the user so the authored transit is a
                      # real crossing rather than a nudge
LEAN_DEG = -12.0      # the epistemic lean -- OBJECT LEG ONLY
USER_NOD = 0.0        # no lean on this leg, so nothing to cancel: level = eyes
OBJECT_ELEV = -10.0   # where the finding sits relative to level (desk = below)
OBJECT_NOD = OBJECT_ELEV - LEAN_DEG   # cancel the lean's pitch, THEN aim
HOLD_OBJ_F = 14       # the held fixation that ends this clip
# ---- shape ----
CROUCH = -8.0      # anticipation dip before the turn (tilt)
OVERSHOOT = 4.0    # pan overshoot past the user, settled back
COUNTER = 6.0      # head counter-tilt while the neck dives, so the camera gets
                   # onto the object before the neck has finished travelling

# ---- speed ----
PEAK_DPS = 120.0   # ceiling on PEAK joint speed. The authoring limit is 200,
                   # but that is a no-load figure at 6 V; this is the clip that
                   # sits closest to it, and it is both loaded and under-volted.
                   # Being conservative costs a fraction of a second and buys
                   # the difference between a turn and a lurch.
PEAK_FACTOR = 1.5  # Bezier easing peaks above a segment's average by roughly
                   # this much, so budget the average at PEAK_DPS / PEAK_FACTOR.
FPS = 30

# Reachable Blender angles, derived from hardware/calibration.py LIMITS. Used to
# fail loudly here rather than let the runtime clamp quietly eat the motion.
REACH = {"pan": (-76.4, 70.2), "tilt": (-38.3, 29.2), "nod": (-47.1, 26.2)}
# -------------------------------------------------

LED_COLOR = (0.00, 1.00, 0.16, 1.0)   # GREEN = a result worth your attention

pan = bpy.data.objects["pan_pivot"]
tilt = bpy.data.objects["tilt_pivot"]
nod = bpy.data.objects.get("nod_pivot")
if not nod:
    raise RuntimeError("Run add_nod_joint.py first.")

for obj in (pan, tilt, nod):
    if obj.animation_data and obj.animation_data.action:
        obj.animation_data_clear()


def check(name, deg):
    lo, hi = REACH[name]
    if not (lo <= deg <= hi):
        raise RuntimeError(f"{name} {deg:+.1f} deg is outside what this build "
                           f"can reach ({lo:+.1f}..{hi:+.1f}). Re-author, or "
                           f"re-measure -- do not let LIMITS clamp it silently.")
    return deg


def frames_for(deg):
    """Frames needed to travel `deg` without the peak exceeding PEAK_DPS."""
    return max(4, math.ceil(abs(deg) / (PEAK_DPS / PEAK_FACTOR) * FPS))


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
# the robot agree. The render is the design reference -- direction and timing get
# judged against renders/statemachine_full_demo.mp4, and a render whose light
# says something different from the hardware quietly invalidates that comparison.
if led_color is not None:
    led_color.default_value = LED_COLOR
    mat.diffuse_color = LED_COLOR


def key_led(frame, value):
    if led_strength is not None:
        led_strength.default_value = value
        led_strength.keyframe_insert(data_path="default_value", frame=frame)


for nm, v in (("pan", USER_PAN + OVERSHOOT), ("pan", OBJECT_PAN),
              ("tilt", LEAN_DEG), ("tilt", CROUCH),
              ("nod", USER_NOD), ("nod", OBJECT_NOD), ("nod", COUNTER)):
    check(nm, v)

turn_deg = USER_PAN + OVERSHOOT
cross_deg = abs(USER_PAN - OBJECT_PAN)
f_crouch = frames_for(CROUCH)
f_turn = frames_for(turn_deg)
f_cross = frames_for(cross_deg)
f_lean = frames_for(LEAN_DEG - 3)

# --- beat 1: notice (anticipation crouch) -------------------------------------
# The head is still on the watched region -- S5 left it there. This dip is the
# detection registering before the body acts on it: Invisible Strings P4, the
# puppeteers' "see, feel, react". Acting without first showing you saw reads as
# distracted.
key(pan, "z", 1, 0); key(tilt, "x", 1, 0); key(nod, "x", 1, 0)
f = 1 + f_crouch
key(pan, "z", f, 0)
key(tilt, "x", f, CROUCH)
key(nod, "x", f, 3)                          # head tucks slightly opposite
f += 2                                        # a beat of stillness before it goes
key(pan, "z", f, 0)

# --- beat 2: ATTENTION-GET (turn to the user, meet their eyes) ----------------
# Huang & Thomaz 2011 phase 1. NO LEAN HERE -- a lean into a person's space is the
# social lean form_interaction_design rules out. The neck stays neutral; only the
# head turns.
f_user = f + f_turn
key(pan, "z", f_user, turn_deg)              # arrive with overshoot
key(pan, "z", f_user + 5, USER_PAN)          # settle back onto them
key(tilt, "x", f_user, 0)                    # neck returns to neutral in transit
key(nod, "x", f_user, USER_NOD)
f = f_user + 5
key(tilt, "x", f, 0)
key(nod, "x", f, USER_NOD)
f += 6                                        # hold the eye contact for a beat --
key(pan, "z", f, USER_PAN)                    # long enough to be seen to arrive
key(tilt, "x", f, 0)
key(nod, "x", f, USER_NOD)

# --- beat 3: DIRECT (turn to the finding; the neck cranes toward IT) ----------
# Huang & Thomaz 2011 phase 2. This is the deictic act: with a one-eye head, a
# committed turn-and-hold reads as "that, over there". The lean fires here and
# only here -- toward a thing, which is the epistemic lean, the novel move.
f_obj = f + f_cross
key(pan, "z", f_obj, OBJECT_PAN)
key(tilt, "x", f, 0)
key(tilt, "x", f_obj, LEAN_DEG - 3)          # extend with slight overshoot
key(nod, "x", f, USER_NOD)
key(nod, "x", f_obj, COUNTER)                # head leads while the neck dives
f = f_obj + 6
key(tilt, "x", f, LEAN_DEG)                  # settle into the lean posture
key(nod, "x", f, OBJECT_NOD)                 # and aim: cancels the lean, then
                                             # adds the object's own elevation

# --- beat 4: HOLD -------------------------------------------------------------
# Nothing moves. This is the beat that carries the referential content, and it is
# still on purpose: Naendrup-Poell & Onnasch 2025 recommend keeping a directional
# cue statically fixated on the target (their ANIMATED naturalistic gaze pattern
# reduced legibility), and Invisible Strings P3 says stillness is what reads as
# focus. Two independent sources, one an eye-tracking experiment and one puppetry
# craft, arriving at the same instruction.
end = f + HOLD_OBJ_F
key(pan, "z", end, OBJECT_PAN)
key(tilt, "x", end, LEAN_DEG)
key(nod, "x", end, OBJECT_NOD)

# LED: bright on the two arrivals -- at you, and at the finding. Dim through the
# transits, so the light marks the two things being connected rather than smearing
# across the whole clip.
key_led(1, 1.0)
key_led(1 + f_crouch, 6.0)
key_led(f_user, 8.0)                          # "Cassie!"
key_led(f_user + 8, 2.0)
key_led(f_obj, 8.0)                           # "...there."
key_led(end, 6.0)                             # stays lit on the hold

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = end

worst = max(turn_deg / (f_turn / FPS),
            cross_deg / (f_cross / FPS),
            abs(CROUCH) / (f_crouch / FPS),
            abs(LEAN_DEG - 3) / (f_lean / FPS)) * PEAK_FACTOR

msg = (f"S7a found v3: crouch -> USER {USER_PAN:.0f} -> OBJECT {OBJECT_PAN:.0f} "
       f"+ lean {LEAN_DEG:.0f} + nod {OBJECT_NOD:.0f} -> hold {HOLD_OBJ_F}f, "
       f"{end}f ({end / FPS:.1f}s), est peak {worst:.0f} deg/s")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text=f"ENDS on the OBJECT leg: pan {OBJECT_PAN:.0f} / "
                           f"tilt {LEAN_DEG:.0f} / nod {OBJECT_NOD:.0f}")
    self.layout.label(text="S7b must OPEN on that exact pose")
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(draw, title="S7a found v3", icon='INFO')
