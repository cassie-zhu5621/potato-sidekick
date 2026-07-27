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
        sfx="ack", sfx_at=0.21, sfx_flash=None, sfx_loop=False,
        # 0.21 = the bottom of the nod (measured f7 of 35)
        screen="heard", enter="a usable transcript exists", exit="auto",
        note="a nod of assent -- got it. The downward accent is the affirmation."),

    "S4_PLAN": dict(
        clip="S4_PLAN", loop=False, then="S5_TRACK", hue="cool",
        sfx=None, sfx_at=0.0, sfx_flash="shutter", sfx_loop=False,
        screen="planning", enter="auto", exit="auto -- NO human confirm; S6 is the correction path",
        note="scanning the forward 180 deg, one capture per station, then "
             "committing to the richest one and craning in to look."),

    "S5_TRACK": dict(
        clip="S5_TRACK", loop=True, then=None, hue="cool",
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
        sfx="puzzled", sfx_at=0.20, sfx_flash=None, sfx_loop=False,
        # 0.20 = the bottom of the abashed droop, before the shake
        screen="notthat", enter="body tap",
        exit="a re-aim -> S5; or REAIM_TIMEOUT_S with no direction -> S5 anyway",
        note="horizontal shake = 'not that one'. The only negation in the "
             "grammar; nothing else shakes horizontally."),

    "S7a": dict(
        clip="S7a", loop=False, then="S7b", hue="green",
        sfx="excited", sfx_at=0.0, sfx_flash=None, sfx_loop=False,
        # 0.0: this one IS the announcement -- it should arrive with the
        # turn, calling you before the robot has finished arriving
        screen="noticed", enter="a confirmed finding", exit="auto -> S7b",
        note="found it: turn to you, crane forward, head comes up to meet "
             "your eyes."),

    "S7b": dict(
        clip="S7b", loop=True, then=None, hue="green",
        sfx=None, sfx_at=0.0, sfx_flash=None, sfx_loop=False,
        screen="noticed", enter="auto", exit="OK pressed, or 30 s ignored -> S5",
        note="beckoning -- head tossing UP on the accent, come and look. "
             "The upward accent is what separates this from a nod."),

    "S8_ERROR": dict(
        clip="S8_ERROR", loop=True, then=None, hue="alarm",
        sfx="lost", sfx_at=0.0, sfx_flash=None, sfx_loop=True,
        screen="error", enter="anything unrecoverable", exit="STOP only",
        note="confused / cannot proceed. Loops until a human intervenes."),
}

# The designed cycle, for reference and for the runner's --demo sweep.
CYCLE = ["S1_IDLE", "S2_LISTEN", "S3_ACK", "S4_PLAN", "S5_TRACK",
         "S6_FINETUNE", "S7a", "S7b"]

# Keyboard shortcuts for the researcher taking over mid-session.
KEYS = {"1": "S1_IDLE", "2": "S2_LISTEN", "3": "S3_ACK", "4": "S4_PLAN",
        "5": "S5_TRACK", "6": "S6_FINETUNE", "7": "S7a", "8": "S8_ERROR"}

# States a participant-visible run should never sit in silently for long.
NEEDS_ATTENTION = {"S8_ERROR"}

# S7 returns to watching if nobody responds. Being ignored is a NORMAL outcome --
# the person is busy, which is the premise of notice delegation. The finding is
# already in the feed, so the robot goes back to watching instead of escalating.
# That is the difference between a colleague and an alarm.
S7_IGNORED_TIMEOUT_S = 30.0

# How long the loop waits for Whisper before giving up and showing S8. Generous:
# a wrong transcript is recoverable (S6 exists), a hang in S3 is not -- the screen
# is already promising the participant that it heard them.
STT_TIMEOUT_S = 15.0
# The ceiling a RUNNING transcription cannot push past. `stt_busy` pauses the
# deadline above, so that a recogniser which is slow but working does not lose
# the request it is in the middle of getting right -- but "busy" must not mean
# "wait forever", or a wedged worker leaves a participant staring at a robot that
# will never answer. Past this, S8 regardless.
STT_HARD_TIMEOUT_S = 30.0

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
    return problems
