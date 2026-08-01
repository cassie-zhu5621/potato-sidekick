# Auto-generates the S7b ENSURE loop (3-DOF). Run inside S7b.blend after
# repair_rig.py + add_nod_joint.py. Overwrites pan/tilt/nod/LED keyframes.
#
# One cycle, starting and ending on the object hold so the loop is seamless:
#
#   [hold on OBJECT] --transit--> [USER: beckon toss] --transit--> [hold on OBJECT]
#         "there"                        "come"                        "there"
#
# v3 (2026-07-29). The loop now ALTERNATES instead of staring.
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
#   undifferentiated alarm. Invisible Strings P8, economy of movement: spend only
#   the motion needed, so what the eye is drawn to is what you meant.
#
#   WHICH STROKE IS FAST is still what decides how the toss reads: between the
#   same two positions, a quick stroke up with a slow return says "come here",
#   a quick stroke down with a slow return says "yes". The eye assigns the
#   meaning to the accented stroke. Rise is short and sharp, there is a beat of
#   hang at the top, the return is more than twice as long and eased.
#
#   The toss is shared between nod and tilt: the head flicks up while the neck
#   straightens slightly under it. That reads as the whole creature lifting
#   rather than a head hinging, and it keeps nod clear of its ceiling.
#
# Loop-safe: every channel starts and ends on the OBJECT hold, which is also
# S7a's final pose. Keep OBJECT_* in sync with generate_s7_found.py or the loop
# will jump the moment it is entered.
#
# See docs/S7_DESIGN.md for the reference behind every element.

import bpy
import math

# ---- keep in sync with generate_s7_found.py ----
# Both pans are TEMPLATES, retargeted at runtime -- see S7_DESIGN.md Sec.5.
USER_PAN = 60.0
OBJECT_PAN = -25.0
LEAN_DEG = -12.0      # the epistemic lean -- OBJECT LEG ONLY
USER_NOD = 0.0        # no lean on the user leg, so no compensation: level = eyes
OBJECT_ELEV = -10.0
OBJECT_NOD = OBJECT_ELEV - LEAN_DEG   # cancel the lean's pitch, THEN aim
HOLD_OBJ_F = 14       # the held fixation. Must be >= S7a's, or the handover reads
                      # as the robot losing interest the moment the loop starts
# ---- the toss (USER leg only) ----
THROW_NOD = 10.0    # head flicks UP this far (positive = up in Blender)
THROW_TILT = 8.0    # neck straightens up under it, same direction
# ---- rhythm: the asymmetry IS the gesture ----
RISE_F = 5          # sharp. This is the stroke that carries the meaning.
TOP_F = 5           # hang at the top -- the "well? come on" beat
FALL_F = 11         # slow, eased return. Must be clearly longer than RISE_F.
HOLD_USER_F = 6     # settle on the face after the toss, before turning back
CYCLES = 2          # alternations per loop; any number loops cleanly

# ---- speed ----
PEAK_DPS = 120.0
PEAK_FACTOR = 1.5
FPS = 30

# Reachable Blender angles, from hardware/calibration.py LIMITS.
REACH = {"pan": (-76.4, 70.2), "tilt": (-38.3, 29.2), "nod": (-47.1, 26.2)}
# ------------------------------------------------

LED_COLOR = (0.00, 1.00, 0.16, 1.0)   # GREEN, same as S7a -- one event, one colour

pan = bpy.data.objects["pan_pivot"]
tilt = bpy.data.objects["tilt_pivot"]
nod = bpy.data.objects.get("nod_pivot")
if not nod:
    raise RuntimeError("Run add_nod_joint.py first — this clip needs the 3rd DOF.")

for obj in (pan, tilt, nod):
    if obj.animation_data and obj.animation_data.action:
        obj.animation_data_clear()


def check(name, deg):
    lo, hi = REACH[name]
    if not (lo <= deg <= hi):
        raise RuntimeError(f"{name} {deg:+.1f} deg is outside what this build "
                           f"can reach ({lo:+.1f}..{hi:+.1f}). Reduce THROW_* or "
                           f"OBJECT_NOD -- do not let LIMITS clamp it silently.")
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


top_nod = check("nod", USER_NOD + THROW_NOD)
top_tilt = check("tilt", THROW_TILT)
check("pan", USER_PAN)
check("pan", OBJECT_PAN)
check("nod", OBJECT_NOD)
check("tilt", LEAN_DEG)
if FALL_F <= RISE_F:
    raise RuntimeError("FALL_F must be longer than RISE_F, or the accent lands "
                       "on the way down and this reads as a nod, not a beckon.")

cross_deg = abs(USER_PAN - OBJECT_PAN)
f_cross = frames_for(cross_deg)
# Transit also has to carry the lean in and out. If the neck move is the slower
# of the two, it -- not pan -- sets the transit length, or the lean would arrive
# after the head and the two would read as separate events.
f_cross = max(f_cross, frames_for(LEAN_DEG))

cycle_f = HOLD_OBJ_F + f_cross + RISE_F + TOP_F + FALL_F + HOLD_USER_F + f_cross
end = 1 + CYCLES * cycle_f

for i in range(CYCLES):
    f0 = 1 + i * cycle_f                    # start of the object hold

    # --- "there": held fixation on the finding ---------------------------------
    # Still on purpose. Naendrup-Poell & Onnasch 2025 recommend keeping a
    # directional cue statically fixated on the target; Invisible Strings P3 says
    # stillness is what reads as focus.
    f_hold_end = f0 + HOLD_OBJ_F
    for fr in (f0, f_hold_end):
        key(pan, "z", fr, OBJECT_PAN)
        key(tilt, "x", fr, LEAN_DEG)
        key(nod, "x", fr, OBJECT_NOD)

    # --- transit to you: the neck releases as the head comes round -------------
    f_user = f_hold_end + f_cross
    key(pan, "z", f_user, USER_PAN)
    key(tilt, "x", f_user, 0.0)             # lean releases -- it belongs to the
    key(nod, "x", f_user, USER_NOD)         # object, never to a person's space

    # --- "come": the beckon toss ----------------------------------------------
    f_up = f_user + RISE_F
    f_hang = f_up + TOP_F
    f_down = f_hang + FALL_F
    f_user_end = f_down + HOLD_USER_F

    key(nod, "x", f_up, top_nod)            # the accent
    key(tilt, "x", f_up, top_tilt)
    key(nod, "x", f_hang, top_nod)          # hold the question open
    key(tilt, "x", f_hang, top_tilt)
    key(nod, "x", f_down, USER_NOD)         # eased, unhurried return
    key(tilt, "x", f_down, 0.0)
    key(pan, "z", f_user_end, USER_PAN)     # settle on the face
    key(nod, "x", f_user_end, USER_NOD)
    key(tilt, "x", f_user_end, 0.0)

    # --- transit back to the finding: the lean re-engages ----------------------
    f_next = f0 + cycle_f
    key(pan, "z", f_next, OBJECT_PAN)
    key(tilt, "x", f_next, LEAN_DEG)
    key(nod, "x", f_next, OBJECT_NOD)

    # LED peaks on both accents -- the arrival at the finding and the top of the
    # toss. Those are the two things the loop is connecting.
    key_led(f0, 6.0)                        # lit through "there"
    key_led(f_hold_end, 6.0)
    key_led(f_user, 2.0)
    key_led(f_up, 8.0)                      # the summons
    key_led(f_hang, 6.0)
    key_led(f_down, 2.0)
    key_led(f_next, 6.0)

bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = end

peak = max(PEAK_FACTOR * max(THROW_NOD, THROW_TILT) / (RISE_F / FPS),
           PEAK_FACTOR * cross_deg / (f_cross / FPS))
msg = (f"S7b ensure v3: hold OBJECT {OBJECT_PAN:.0f} ({HOLD_OBJ_F}f) <-> USER "
       f"{USER_PAN:.0f} toss +{THROW_NOD:.0f}, {CYCLES}x{cycle_f}f = {end}f "
       f"({end / FPS:.1f}s), est peak {peak:.0f} deg/s")
print(msg)


def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text=f"OPENS+CLOSES on OBJECT: pan {OBJECT_PAN:.0f} / "
                           f"tilt {LEAN_DEG:.0f} / nod {OBJECT_NOD:.0f}")
    self.layout.label(text="= S7a's last frame. Keep both files in sync.")
    self.layout.label(text="Then: save, run export_clip.py")


bpy.context.window_manager.popup_menu(draw, title="S7b ensure v3", icon='INFO')
