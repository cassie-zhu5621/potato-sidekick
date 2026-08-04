"""
The state table. Declarative on purpose: the communication cycle IS the design
contribution, so it should be readable as a table rather than inferred from
control flow scattered through a loop.

  clip  : CSV basename in the export folder
  loop  : replay until something else is requested
  then  : where a one-shot goes when it finishes (None = hold the last pose)
  hue   : LED colour intent, handed to the CoreS3. Colour says WHAT KIND of
          state this is; the clip's `led` column says how bright, moment to
          moment. Palette:
            warm  - present, not attending          (S1)
            cool  - attending, nothing to report    (S2-S5)
            red   - negation, "not that one"        (S6)
            green - a result worth your attention   (S7)
            alarm - stuck, needs a human            (S8)
          Colour is REDUNDANT with the motion, never the only carrier: S6 is
          also a fast horizontal shake and S7 is also a turn-crane-toss, so a
          red/green-deficient participant still reads them apart. That
          redundancy is deliberate -- roughly 8% of men could not use the
          palette on its own.
  sfx   : the state's sound, or None for silence. Silence is a choice, and there
          are four of them: S1 and S5 must not compete for attention, S7b would
          be talking over S7a, and S2's turn is already legible without help.
          The vocabulary mirrors the motion rather than decorating it --
          `ack` falls, like the nod it accompanies; `excited` rises; `lost` uses
          intervals that never resolve.
  sfx_at : WHERE in the clip the sound fires, as a fraction of its duration.
          0.0 = the instant the state is entered. Anything else is scheduled on
          the clip's own clock, so it lands on the motion's accent rather than
          at frame 1.
          This exists because "sound on state entry" was audibly wrong: S3's
          affirmation nod bottoms out at 0.21 of the clip, so a sound at frame 1
          arrived before the gesture it was meant to belong to. A sound that
          precedes its accent does not read as emphasis, it reads as a separate
          event -- which is what made S2 and S3 sound like two unrelated noises
          rather than one exchange.
  sfx_flash : name of a sound fired on each LED FLASH inside the clip, derived
          from the led column's rising edge. S4's shutter click has to land on
          the same frame as the flash, and deriving one from the other is the
          only way they cannot drift apart. None for every other state.
  sfx_loop : replay `sfx` on every pass of a looping clip. True only for S8:
          an error nobody has come to fix should keep asking.
  sfx_every : with sfx_loop, replay only every Nth pass. Default 1. "Keep
          asking" is a rate, not a boolean -- on S8's 4 s loop, every pass is 15
          cries a minute with no exit but STOP, which is an alarm rather than a
          request. Absent on every other state, none of which loop with a sound.
  screen: name of the CoreS3 screen this state normally shows -- see SCREENS.
          Screens are their own vocabulary rather than a field of the state,
          because there is one MORE screen than there are states: S2 shows
          `recording` while the button is held and `waiting` after it is
          released, without the motion state changing.
  enter : what causes this state to begin. "auto" = the previous state's `then`.
  exit  : what ends it, beyond STOP (which works from everywhere, always).
  note  : what this state claims, in one line. If a state cannot be described in
          one line it is probably two states.

See INTERACTION_SPEC.md for the reasoning, and for the three decisions still
open (transcript source, who owns the noticed counter, STOP = cancel or pause).
"""

STATES = {
    "S1_IDLE": dict(
        clip="S1_IDLE", loop=True, then=None, hue="warm",
        sfx=None, sfx_at=0.0, sfx_flash=None, sfx_loop=False,
        screen="idle", enter="stop, or S7 timeout", exit="PTT pressed",
        note="present, not attending. The resting state between everything."),

    "S2_LISTEN": dict(
        # then=None: after the turn, S2 HOLDS -- facing the person, screen on
        # `waiting` -- until the transcript is confirmed. S3 is not entered early.
        # The nod and the words "I heard you." are one act of acknowledgement, so
        # performing the nod before the words exist would be claiming to have
        # understood something not yet read. (Earlier version gated inside S3 and
        # nodded first; this is the correction.)
        clip="S2_LISTEN", loop=False, then=None, hue="cool",
        sfx=None, sfx_at=0.0, sfx_flash=None, sfx_loop=False,
        screen="recording", enter="PTT pressed",
        exit="PTT released -> `waiting`; then a usable transcript -> S3, "
             "unusable or timeout -> S8",
        note="turning toward you because you spoke; taking the request in."),

    "S3_ACK": dict(
        # Entered only once there IS a transcript, so nothing here waits: the nod
        # and the words land together.
        clip="S3_ACK", loop=False, then="S4_PLAN", hue="cool",
        sfx="ack", sfx_at=0.24, sfx_flash=None, sfx_loop=False,
        # 0.24 = the bottom of the FIRST cycle (f12 of 50). Recomputed: the old
        # 0.21 was measured against a 35-frame clip that no longer exists. The
        # nod is now two cycles, and the sound belongs to the accented one --
        # under declination the accent is cycle 1, and a sound on the second
        # would mark the weaker beat as the affirmation.
        screen="heard", enter="a usable transcript exists", exit="auto",
        note="a nod of assent -- got it. The downward accent is the affirmation."),

    "S4_PLAN": dict(
        # S4 ONLY ACQUIRES. It sweeps, captures, and ends LEVEL at the chosen
        # pan; arriving at the thing is S5A_SETTLE, and S5A ONLY RUNS IF THE
        # TARGET CHANGED. The periodic sweep is additive, so it often changes
        # nothing, and a robot that performed "I have chosen" every five minutes
        # about the object it was already watching would be making a movement
        # whose result did not change. That limit case is expressed by NOT
        # ENTERING A STATE -- when the target is unchanged S4 hands straight to
        # S5B and the re-crane is an ordinary transition.
        # `then` MUST be a state name -- it is what the player requests. This
        # field held the prose "S5A_SETTLE if the target changed, else
        # S5B_TRACK", which made validate() fail and ClipPlayer refuse to
        # construct at all. The conditional now lives where the information is:
        # the planner knows whether the target changed, so it calls
        # player.arm_next("S5A_SETTLE") and the arm is consumed here. Unarmed,
        # S4 falls through to S5B and the re-crane is an ordinary transition --
        # which is exactly the semantics the prose described.
        clip="S4_PLAN", loop=False, then="S5B_TRACK", hue="cool",
        sfx=None, sfx_at=0.0, sfx_flash="shutter", sfx_loop=False,
        screen="planning", enter="auto", exit="auto -- NO human confirm; S6 is the correction path",
        note="scanning the forward 180 deg, one capture per station, then "
             "committing to the richest one and craning in to look."),

    "S5A_SETTLE": dict(
        # The arrival: crane onto the thing, head lift trailing. Entered ONLY
        # when S4 chose a different target.
        #
        # A BEAT THAT IS LOSSLESS IF MISSED. It fires autonomously, potentially
        # every few minutes, and a state that demanded attention on that schedule
        # would be intolerable. Whatever S5a says, S5b's held pose says too --
        # the direction it settles into is the direction it then holds. So no LED
        # accent and no overshoot: available, not addressed. Compare S7, which
        # exists precisely to demand attention.
        clip="S5A_SETTLE", loop=False, then="S5B_TRACK", hue="cool",
        sfx=None, sfx_at=0.0, sfx_flash=None, sfx_loop=False,
        screen="tracking", enter="S4 chose a NEW target", exit="auto",
        note="epistemic lean -- toward a thing in order to see it, not into a "
             "person's space."),

    "S5B_TRACK": dict(
        # Clip renamed S5_TRACK -> S5B_TRACK. It was the last file whose name
        # differed from its state's, and that difference is exactly what three
        # comparisons got wrong today -- `snap["state"] in ("S5_TRACK", ...)` is
        # merely never true. Every clip is now named after its state.
        clip="S5B_TRACK", loop=True, then=None, hue="cool",
        sfx=None, sfx_at=0.0, sfx_flash=None, sfx_loop=False,
        screen="tracking", enter="auto", exit="body tap -> S6, or a finding -> S7",
        note="watching the chosen thing. Deliberately motionless -- the light "
             "carries 'still here', motion is saved for events."),

    "S6_FINETUNE": dict(
        # then=None: S6 HOLDS after the shake, waiting for a direction. Letting it
        # auto-advance made the re-aim a race -- the clip is 2.3 s and a researcher
        # clicking a button is not, so half the time the correction arrived after
        # the robot had already gone back to watching the same wrong thing.
        clip="S6_FINETUNE", loop=False, then=None, hue="red",
        sfx="puzzled", sfx_at=0.46, sfx_flash=None, sfx_loop=False,
        # 0.46 = the FIRST shake extreme, the moment the refusal becomes
        # legible. The old 0.20 was authored against a droop that has since
        # moved; it now fires during the hold-in, before anything happens.
        screen="notthat", enter="body tap",
        # The two exits go to DIFFERENT states, by S5a's own rule -- the crane is
        # an authored beat when the target changed and a bare transition when it
        # did not:
        #   a direction is given -> S5A_SETTLE  (arm_pan_deg first; the turn to it
        #                                        is the answer, at REAIM_DPS)
        #   REAIM_TIMEOUT_S, none -> S5B_TRACK  (same aim as before, so there is
        #                                        no arrival to announce)
        # Which also makes giving up visibly quieter than being answered. Nothing
        # was decided, so nothing is performed.
        exit="a direction -> S5A_SETTLE; or REAIM_TIMEOUT_S with none -> S5B_TRACK",
        note="horizontal shake = 'not that one'. The only negation in the "
             "grammar; nothing else shakes horizontally."),

    "S7a": dict(
        clip="S7a", loop=False, then="S7b", hue="green",
        sfx="excited", sfx_at=0.0, sfx_flash=None, sfx_loop=False,
        # 0.0: this one IS the announcement -- it should arrive with the
        # turn, calling you before the robot has finished arriving
        screen="noticed", enter="a confirmed finding", exit="auto -> S7b",
        note="found it: attention-get (turn to you) then DIRECT (turn to the "
             "finding, crane toward it, hold). Ends on the object."),

    "S7b": dict(
        clip="S7b", loop=True, then=None, hue="green",
        sfx=None, sfx_at=0.0, sfx_flash=None, sfx_loop=False,
        screen="noticed", enter="auto", exit="OK pressed, or 30 s ignored -> S5",
        note="ensure -- alternating you <-> the finding: 'come' (toss at you) / "
             "'there' (hold on it). The alternation is the confirmation."),

    "S8_ERROR": dict(
        clip="S8_ERROR", loop=True, then=None, hue="alarm",
        sfx="lost", sfx_at=0.16, sfx_flash=None, sfx_loop=True, sfx_every=4,
        # 0.16 = the first swing extreme, where the LED also peaks. The old 0.0
        # pointed at frame 1, which since v2 holds the droop and is not a beat.
        # sfx_every=4: one cry per four passes, i.e. every ~16 s rather than
        # every 4. The exit here is STOP only, so this can run for minutes.
        screen="error", enter="anything unrecoverable", exit="STOP only",
        note="confused / cannot proceed. Loops until a human intervenes."),
}

# The designed cycle, for reference and for the runner's --demo sweep.
CYCLE = ["S1_IDLE", "S2_LISTEN", "S3_ACK", "S4_PLAN", "S5A_SETTLE",
         "S5B_TRACK", "S6_FINETUNE", "S7a", "S7b"]

# Keyboard shortcuts for the researcher taking over mid-session.
KEYS = {"1": "S1_IDLE", "2": "S2_LISTEN", "3": "S3_ACK", "4": "S4_PLAN",
        "5": "S5B_TRACK", "0": "S5A_SETTLE", "6": "S6_FINETUNE", "7": "S7a",
        "8": "S8_ERROR"}

# States a participant-visible run should never sit in silently for long.
NEEDS_ATTENTION = {"S8_ERROR"}

# ---------------------------------------------------------------------------
# Two sets that code outside this file branches on. They live here because they
# are STATE names, and three places had been comparing against CLIP names
# instead -- `S5_TRACK` is the clip; the state is `S5B_TRACK`. Every one of those
# comparisons was silently false forever:
#
#   * the pan override was cleared on entering the watching state, so the re-aim
#     and S4's "take over at the richest angle" handover never applied;
#   * the in-clip override never fired either, so even a fixed entry would have
#     been overwritten frame by frame with the clip's template;
#   * `watching` excluded S5B, so CV NEVER RAN IN THE STATE WHOSE ENTIRE JOB IS
#     WATCHING.
#
# Nothing raised, because "state name that is not a state" is a string that is
# merely never equal to anything. validate() now checks both sets, so the same
# mistake fails loudly at construction.

# CV perceives here. Not S4 -- during the sweep the VLM is the only eye, because
# annotating a frame before the model reads it feeds our guesses back as its
# judgement.
WATCHING = ("S5B_TRACK", "S6_FINETUNE", "S7a", "S7b")

# Clips whose pan channel is a CONSTANT, so the angle can simply be replaced: a
# constant has no authored timing to damage. S4 is deliberately absent -- its
# return is an eased curve authored for one distance, and stretching it would
# rewrite the very thing the clip exists to carry.
PAN_RETARGETABLE = ("S5B_TRACK", "S5A_SETTLE")

# S7 returns to watching if nobody responds. Being ignored is a NORMAL outcome --
# the person is busy, which is the premise of notice delegation. The finding is
# already in the feed, so the robot goes back to watching instead of escalating.
# That is the difference between a colleague and an alarm.
S7_IGNORED_TIMEOUT_S = 30.0

# How long the loop waits for Whisper before giving up and showing S8. Generous:
# a wrong transcript is recoverable (S6 exists), a hang in S3 is not -- the screen
# is already promising the participant that it heard them.
STT_TIMEOUT_S = 15.0

# --- re-planning, and why there is no confirmation step ---------------------
#
# The participant never sees the watch-spec, so a confirm gate over it would
# produce a rubber stamp rather than consent: declining requires information they
# do not have. Correction therefore happens DURING tracking, by body tap -> S6,
# and is concrete ("wrong thing") rather than abstract ("approve this spec").
# See robot_motion/S4_S5_DESIGN.md sec 1.
#
# S4 re-fires on two conditions:
REPLAN_IDLE_S = 30.0     # tracking has seen nothing. The detection is real:
                         # *there is nothing here*.
REPLAN_PERIOD_S = 300.0  # structural, not precautionary. An object that entered
                         # the room after the last plan has never been detected,
                         # is not in the candidate set, and can never be chosen.
                         # The camera sees 58 deg on a body that pans ~115: it
                         # cannot know what is outside the frame without moving.
                         # A sweep is EPISTEMIC movement -- it is not reporting a
                         # detection, it IS the detecting.

# A new focused object simply becomes the target; the robot does not arbitrate
# between it and a user correction. Deliberately simple: a new object will most
# likely trigger a finding anyway, and the user decides AT THAT MOMENT -- via the
# one gesture they ever need, "look somewhere else". One lever, one meaning, and
# a study that measures one thing.
# The ceiling a RUNNING transcription cannot push past. `stt_busy` pauses the
# deadline above, so that a recogniser which is slow but working does not lose
# the request it is in the middle of getting right -- but "busy" must not mean
# "wait forever", or a wedged worker leaves a participant staring at a robot that
# will never answer. Past this, S8 regardless.
STT_HARD_TIMEOUT_S = 30.0

# How far the re-chosen aim must move before it counts as a DIFFERENT target,
# and therefore before S5a announces the arrival. Not zero: the VLM's chosen
# station is quantised to S4's 5 stations but the score can wobble, and a robot
# that performed "I have chosen!" over a two-degree change would be making a
# movement whose result did not change. One station step is 30 deg, so half of
# one is comfortably inside the noise and outside a real re-choice.
AIM_CHANGED_DEG = 15.0

# How long S6 waits for a direction before giving up and watching again. It has to
# give up: "not that!?" is a question, and a robot still asking it a minute later
# has stopped being a colleague and become a stuck appliance. Going back to the
# same aim is the honest fallback -- it was told the choice was wrong, not where
# to look instead.
REAIM_TIMEOUT_S = 15.0

# STOP is a CANCEL, not a pause: the watch-spec is discarded and PTT starts a
# fresh request. Worth being explicit, because "returns to idle" alone leaves it
# ambiguous, and a participant's model of what they just did depends on it.
STOP_DISCARDS_TASK = True

# Buttons the CoreS3 may show. Fixed positions: green affirms, red stops, and
# nothing else ever uses those slots -- so a participant never has to read a
# button to know what it does.
BUTTONS = {
    "PTT":  "hold to speak -- press enters S2, release starts transcription",
    "OK":   "I saw what you shared -- returns S7 to S5",
    "STOP": "end the task, return to S1. Available in every state.",
}

# The screen vocabulary. (text, buttons). One more entry than there are states:
# `waiting` belongs to S2 after the button is released.
SCREENS = {
    "idle":      ("idle",          ("PTT", "STOP")),
    "recording": (None,            ()),      # a level bar, no words: the person is
                                             # mid-sentence and holding a button;
                                             # text would ask them to read while
                                             # they talk
    "waiting":   ("waiting...",    ("STOP",)),
    "heard":     ("I heard you.",  ("STOP",)),
    "planning":  ("planning...",   ("STOP",)),
    "tracking":  ("tracking...",   ("STOP",)),
    "notthat":   ("not that!?",    ("STOP",)),
    "noticed":   ("{n} noticed",   ("OK", "STOP")),
    "error":     ("error",         ("STOP",)),
}

# What counts as an unusable request -> S2 goes to S8 rather than to S3.
#
# Getting this wrong in either direction is bad in a different way. Too strict and
# a real request gets rejected, which the researcher then has to rescue by typing
# it -- recoverable. Too loose and the robot confidently plans against nonsense,
# which is NOT recoverable and burns a session. So the bar is "is there a request
# here at all", not "is it a good one".
#
# A misread is a different case entirely and must NOT come here: the researcher
# corrects it in the web UI. Only absence and nonsense reach S8.
STT_REJECT = dict(
    min_chars=4,            # "uh" or a single click is not a request
    min_words=2,            # imperatives in scope are at least verb + object
    max_no_speech_prob=0.6, # faster-whisper: high = it heard no speech
    min_avg_logprob=-1.0,   # very low = decoding was guessing, i.e. hallucinated
    require_letters=True,   # all-punctuation output is a known Whisper artefact
)


SFX_NAMES = {"curious", "ack", "shutter", "puzzled", "excited", "lost"}


def validate(available):
    """Cross-check the table against the clips actually on disk."""
    problems = []
    for name, s in STATES.items():
        for field in ("sfx", "sfx_flash"):
            v = s.get(field)
            if v is not None and v not in SFX_NAMES:
                problems.append(f"{name}: {field}={v!r} is not a known sound "
                                f"({sorted(SFX_NAMES)}) -- the firmware would "
                                f"silently ignore it")
    for name, s in STATES.items():
        if s["clip"] not in available:
            problems.append(f"{name}: clip {s['clip']}.csv not found")
        if s["then"] and s["then"] not in STATES:
            problems.append(f"{name}: then={s['then']} is not a state")
        if s["loop"] and s["then"]:
            problems.append(f"{name}: loop states cannot also have then=")
        if s.get("sfx_every", 1) != 1 and not s.get("sfx_loop"):
            problems.append(f"{name}: sfx_every without sfx_loop does nothing")
    for setname, names in (("WATCHING", WATCHING),
                           ("PAN_RETARGETABLE", PAN_RETARGETABLE),
                           ("NEEDS_ATTENTION", NEEDS_ATTENTION),
                           ("CYCLE", CYCLE), ("KEYS", tuple(KEYS.values()))):
        for n in names:
            if n not in STATES:
                problems.append(f"{setname} names {n!r}, which is not a state "
                                f"-- most likely a CLIP name; the clip for "
                                f"S5B_TRACK is S5_TRACK, and they are not the "
                                f"same string")
    return problems


def bad_state_literals(root=None):
    """Every "Sn..." string literal in the repo that is not a state name.

    This exists because a wrong state name is not an error, it is a string that
    is merely never equal to anything -- and there were TWENTY-SIX of them. Most
    were the CLIP name S5-underscore-TRACK standing in for the state
    S5B_TRACK. The
    damage was silent in three different ways: comparisons that were false
    forever (CV never ran while watching), an override cleared on entry (the
    re-aim never applied), and a _go to the clip name, which would have raised
    KeyError on four of the study's most-used exits -- OK, re-aim, and both
    timeouts.

    Grepping is crude and it is exactly right here: the failure is textual.
    """
    import os, re
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pat = re.compile(r'"(S\d[A-Za-z_]*)"')
    out = []
    for d, _, fs in os.walk(root):
        if any(x in d for x in (".git", "__pycache__", "blender")):
            continue
        for f in sorted(fs):
            if not f.endswith(".py"):
                continue
            path = os.path.join(d, f)
            for i, line in enumerate(open(path, errors="ignore"), 1):
                if line.strip().startswith("#"):
                    continue
                for m in pat.finditer(line):
                    if m.group(1) not in STATES:
                        out.append(f"{os.path.relpath(path, root)}:{i}: "
                                   f"{m.group(1)!r} is not a state")
    return out


def _selfcheck():
    """`python3 -m robot.states` -- run the table's own checks with no hardware.

    This exists because validate() was correct and simply never executed: it is
    called from ClipPlayer.__init__, which needs a servo bus, so a broken table
    could only be discovered by plugging the robot in. A table this declarative
    should be checkable from a laptop on a train.
    """
    import os
    clips = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "motion", "clips")
    available = {f[:-4] for f in os.listdir(clips) if f.endswith(".csv")}
    problems = validate(available) + bad_state_literals()
    for p in problems:
        print("  !!", p)
    print(f"{len(STATES)} states, {len(available)} clips, "
          f"no stray state literals -- "
          + ("OK" if not problems else f"{len(problems)} PROBLEM(S)"))
    return 1 if problems else 0


if __name__ == "__main__":
    import sys as _s
    _s.exit(_selfcheck())
