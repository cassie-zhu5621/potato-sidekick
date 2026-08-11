"""
The state table. Declarative on purpose: the communication cycle IS the design
contribution, so it should be readable as a table rather than inferred from
control flow scattered through a loop.

  clip  : CSV basename in the export folder
  loop  : replay until something else is requested
  then  : where a one-shot goes when it finishes (None = hold the last pose)
  hue   : LED colour intent, handed to the CoreS3. Colour says WHAT KIND of
          state this is; the clip's `led` column says how bright, moment to
          moment. Palette (derivations: robot_motion/LED_COLOR_DESIGN.md):
            warm   - present, not attending, LOW arousal   (S1)
            cool   - attending, nothing to report          (S2-S5)
            red    - negation, "not that one"              (S6)
            summon - a finding, come and look              (S7)
            spent  - stuck, the light going out of it      (S8)
          Colour is REDUNDANT with the motion, never the only carrier. That is
          not caution, it is the measured order of the two channels: colour
          alone classifies at 69%, motion alone at 79%, colour+motion at 92%
          (Loffler et al. HRI'18, n=33). Colour is the WEAKEST signal here, so
          its job is to agree with the clip rather than to carry the state --
          and a colour that CONTRADICTS its motion is worse than none, because
          then the strong channel and the weak one are in competition.

          TWO RENAMES, both of which were real defects:

          green -> summon. Green is the low-arousal positive corner (Song &
            Yamada map green to *happy*), while S7 is the highest-arousal
            moment in the library. It was also COL_GREEN, the OK button, so
            "I found something" and "dismiss it" were the same colour in the
            same visual field.
          alarm -> spent. The NAME was the bug: it made an alarm colour look
            correct for a gesture that is a deflation. S8 droops and sways --
            down, passive, dark -- which operationalises to blue at reduced
            brightness, not to urgent amber at V 0.94.

          The rename also fixes the accessibility case rather than only
          declaring it. The old palette put the two most opposed states, S6
          negation and S7 finding, on RED and GREEN -- precisely the pair that
          ~8% of men cannot separate, so for them the two loudest signals in
          the grammar collapsed into one. Magenta keeps the blue channel, which
          every common deficiency leaves intact.
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
        # S4 ONLY ACQUIRES. It sweeps, captures, and ends LEVEL at the final
        # shutter pan. ClipPlayer trims the authored return to the +25-degree
        # template: that direction has no basis while the VLM is still deciding.
        # Arriving at the selected thing is S5A_SETTLE, and S5A ONLY RUNS IF THE
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
        # the planner knows whether the target changed. Planning runs after this
        # clip has reached its S5 hold, so a changed result explicitly requests
        # S5A; an unchanged result keeps S5B and only restores its runtime pan.
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
        clip="S7a", loop=False, then="S7b", hue="summon",
        sfx="excited", sfx_at=0.0, sfx_flash=None, sfx_loop=False,
        # 0.0: this one IS the announcement -- it should arrive with the
        # turn, calling you before the robot has finished arriving
        screen="noticed", enter="a confirmed finding", exit="auto -> S7b",
        note="found it: attention-get (turn to you) then DIRECT (turn to the "
             "finding, crane toward it, hold). Ends on the object."),

    "S7b": dict(
        clip="S7b", loop=True, then=None, hue="summon",
        sfx=None, sfx_at=0.0, sfx_flash=None, sfx_loop=False,
        screen="noticed", enter="auto", exit="OK pressed, or 30 s ignored -> S5",
        note="ensure -- alternating you <-> the finding: 'come' (toss at you) / "
             "'there' (hold on it). The alternation is the confirmation."),

    "S8_ERROR": dict(
        clip="S8_ERROR", loop=True, then=None, hue="spent",
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

# The pan angles S7a and S7b are AUTHORED at. Both clips are templates: the
# object leg is wherever S5b was actually watching, and the user leg is wherever
# the person actually is. At runtime ClipPlayer maps the authored pair onto the
# real pair -- see ClipPlayer.set_share_pan and S7_DESIGN.md sec 5.
#
# Declared here rather than re-derived from the CSV, because the clip's extremes
# are NOT the plateaus: S7a overshoots to +64 past the user before settling, and
# reading min/max would silently adopt the overshoot as the endpoint and shrink
# every gesture by 4 degrees.
SHARE_TEMPLATE = {"user": 60.0, "object": -25.0}

# Where the person is when nobody has said. S2/S3 are AUTHORED at this angle --
# the participant is seated to the robot's left -- so it is the only defensible
# default: the robot has already turned this way once to listen, in front of the
# person, and a later state that faces somewhere else is contradicting a turn
# they watched it make.
#
# SHARE_TEMPLATE["user"] is NOT this number and must not be confused with it.
# That +60 is a TEMPLATE POSITION INSIDE THE S7 CLIPS, which `remap_share_pan`
# rewrites at runtime; it is a coordinate in the authored file, not a claim about
# the room. This one is a claim about the room.
USER_PAN_AUTHORED = -30.0     # = generate_s2_listen.py / generate_s3_ack.py

# Clips that ADDRESS THE PERSON and must therefore be played facing them, not at
# whatever angle they happen to have been authored at. S6 is the whole list: it
# is the robot being corrected, which is a thing said TO someone.
#
# S6 was authored at +25 -- S5b's template watching angle -- because it was drawn
# as a continuation of watching. It is not: the tap is the person interrupting,
# and answering an interruption while facing 55 degrees away from the person who
# made it reads as the robot shaking its head at the wall.
USER_FACING = ("S6_FINETUNE",)

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
S8_RECOVER_S = 16.0      # S8 gives up and returns to S1_IDLE after this long.
                         # = FOUR PASSES of its own 3.97 s loop, and the loop is
                         # the unit that matters here: the participant is not
                         # counting seconds, they are watching the same droop and
                         # sway happen again. Four is enough to read as "it has
                         # stopped trying" and short enough not to become the
                         # thing they remember about the session.
                         #
                         # It also lands on the sound. `sfx_every = 4` fires
                         # `lost` on pass 1 and would fire it again on pass 5, so
                         # exiting at four means the robot cries ONCE, sways four
                         # times, and gives up. At 45 s (11.3 passes) it cried
                         # three times, which is a machine escalating rather than
                         # one that has run out of ideas.
                         #
                         # WAS 45, and before that STOP-only. STOP-only assumed a
                         # reader who could press it: S8 is entered from a failed
                         # plan, an unusable transcript, a dead camera -- none of
                         # which a PARTICIPANT can clear, or even name. Without a
                         # timeout the robot drooped until someone walked over,
                         # which from the chair is indistinguishable from having
                         # broken it.
                         #
                         # RETURNING TO IDLE IS NOT PRETENDING NOTHING HAPPENED.
                         # S1 is "present, not attending" -- the honest state
                         # after a failure the robot cannot fix: it stopped
                         # trying, and it is still here. The failure stays in the
                         # log and on the researcher's screen.
                         #
                         # Set to 0 or None to restore STOP-only.
# HOW LONG THE ROBOT WAITS FOR THE JUDGE BEFORE REACTING ANYWAY.
#
# The judge answers two things, and only one of them is on the critical path.
# `describe` never reaches the participant: S7 plays a sound effect, not speech,
# and the sentence on the feed card is written LATER by the storyboard's own
# narration call from the strip. So waiting buys exactly one thing -- the
# pass/fail gate -- and pays for it in the only currency this interaction has,
# which is arriving while the moment is still happening.
#
# MEASURED, over the 33 calls made since the judge was cut down on 2026-08-05.
# The distribution is BIMODAL, and the second mode is not a tail:
#
#   3.3 3.5 3.9 4.0 4.0 4.2 4.3 4.7 4.8 4.8 4.9 5.1 5.7 7.2 7.6 8.1     16
#                          -- nothing between 8.1 and 10.6 --
#   10.6 11.4 12.2 12.7 12.9 14.3 14.4 15.8 16.5 17.0 17.2 19.4 19.5
#   21.0 22.4 39.5 59.4                                                 17
#
# Half. Identical images, identical token counts (5863-5869 all day), identical
# model and tier on both sides -- so the split is the service, and nothing about
# the request can move it. Replaying the slowest recorded call afterwards gave
# 2.1 s.
#
# 10 s SITS IN THE EMPTY GAP between the two modes, which is the only place a
# threshold can go without cutting through cases that are alike.
#
# IT WAS 0 FOR AN AFTERNOON, and the dry run put it back. The argument for 0 was
# that a threshold in a bimodal distribution gates about half the findings and
# not the other half, so a session's three events land under two regimes -- true,
# and still true. What it weighed that against was a false-report rate assumed to
# be near zero because the events are acted and the focus tier is narrow. It is
# not near zero, and the reason is structural rather than incidental:
#
#   THERE IS NO DEPTH. `hands_on` is a wrist inside an object's 2D box; the
#   relations are computed on projections. A person standing IN FRONT OF a plant
#   and a person touching one are the same picture. Reported 2026-08-08 from the
#   dry run, and no threshold in perception can separate them, because the
#   information is not in the image the geometry is reading.
#
# A VLM looking at five frames can. So the gate goes back in front, and the
# deadline stays as the thing that stops a congested service from turning a
# report into a report about a moment that ended -- which is what it was for.
#
# WHAT THIS COSTS, both ways, measured:
#
#   typical   1.25 s CV gate + 1.0 s frame window + 2.1 s judge  = ~4.4 s to S7
#   capped    the same, but never worse than ~12.3 s
#   about half the findings still arrive ungated, because half the calls land
#   past 10 s. `judge_agreed` records which, so the rate is in the data.
JUDGE_DEADLINE_S = 10.0

S7_IGNORED_TIMEOUT_S = 30.0

# How long the loop waits for Whisper before giving up and showing S8. Generous:
# a wrong transcript is recoverable (S6 exists), a hang in S3 is not -- the screen
# is already promising the participant that it heard them.
# How long the VLM may take to answer before the robot admits it is stuck.
#
# The planner runs on a daemon thread and the Gemini client has NO request
# timeout, so a call that never returns used to leave the robot holding at the
# last swept angle with `planning...` on the screen -- forever, silently, with no
# error anywhere. Everything downstream was correct: the failure path exists and
# reaches S8. There was simply nothing to notice that the call had gone quiet.
#
# 90 s, and every part of that is measured rather than guessed. A warm call over
# five frames takes ~16 s; the planner retries once, so two attempts is ~35 s;
# and gemini_provider.warm() has already paid the ~60 s cold start at startup, so
# it is not in this budget. 90 leaves better than 2x headroom over the worst
# honest case.
#
# It was 45, chosen before any of that was known, and the first real run went:
# attempt 1 at 76.4 s (cold, and truncated by an output budget nothing read),
# retry at 16.4 s, answer correct -- arriving at 93 s to find the flow had given
# up at 45 and STOP had discarded it. The number was not wrong so much as
# uninformed; a deadline is only meaningful once the thing it bounds is measured.
# A slow-but-working answer must not be cut off -- the same reason stt_busy is
# allowed to postpone the STT deadline. This is the ceiling, not the budget.
PLAN_TIMEOUT_S = 90.0

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
REPLAN_IDLE_S = 0.0      # OFF for the user study. Was 60.
                         #
                         # In a 15-minute session with scripted events at 3, 5
                         # and 10 minutes, QUIET IS THE NORMAL CONDITION -- so a
                         # timer that re-sweeps after every quiet minute fired
                         # about thirteen times, and all three events landed next
                         # to one. Where the robot happened to be pointing when
                         # something finally happened was close to random.
                         #
                         # The cost is real and accepted: something that matches
                         # the request at another angle will now be missed. The
                         # study is testing whether the delegation LOOP works, not
                         # how much of the room is covered, and a robot that turns
                         # by itself is unexplainable in the interview -- the
                         # participant cannot tell 'it re-scanned' from 'it noticed
                         # something', and neither can the transcript.
                         #
                         # Restore to 60 for unattended running. Original note:
                         # tracking has seen nothing. The detection is real:
                         # *there is nothing here*.
                         #
                         # WAS 30, measured on hardware 2026-08-05 and too short.
                         # A sweep costs ~6 s, so 30 meant a re-sweep every ~36 s
                         # -- eight in a five-minute watch. Nothing happening is
                         # this robot's NORMAL condition, not evidence the angle
                         # is wrong, and a machine that re-scans every half minute
                         # reads as agitated rather than attentive.
                         # Set to 0 or None to switch the idle re-plan off.
REPLAN_PERIOD_S = 540.0  # ONE self-directed sweep per session, placed on purpose.
                         #
                         # This is the only moment the robot acts on its own
                         # initiative rather than on a request or a detection --
                         # it goes and re-checks the room for what has appeared
                         # or been missed. A participant who never sees it has no
                         # evidence the thing has any autonomy at all, and the
                         # interview has nothing to ask about.
                         #
                         # 540 s = 9 minutes, and the number is the study
                         # timeline rather than a round figure. Against the
                         # scripted events (E1 3-8 min, E2 5-8 min, E3 10-13 min)
                         # the free windows are 0-3, 8-10 and 13-15:
                         #
                         #     300 s -> 5:00 and 10:00   collides with E2 and E3
                         #     420 s -> 7:00             collides with E2
                         #     540 s -> 9:00             lands in the 8-10 gap
                         #     900 s -> 15:00            too late to be seen
                         #
                         # A sweep costs ~13 s of not watching (6 s of clip, ~7 s
                         # of planner), so landing on an event would eat its
                         # opening. 9:00 sits centred in a two-minute gap, which
                         # is the margin the "~3 min / ~5 min" of the script
                         # needs.
                         #
                         # The clock starts when the PLAN LANDS, which is the top
                         # of work-and-watch, and findings do not reset it -- so
                         # 9:00 is 9:00 whatever else happened.
                         #
                         # Restore to 300 for unattended running. Original note:
                         # structural, not precautionary. An object that entered
                         # the room after the last plan has never been detected,
                         # is not in the candidate set, and can never be chosen.
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
# The antenna while the planner is out. NOT a new colour -- see HUE below: colour
# encodes the KIND of state, and planning and watching are both `cool`
# (attending). Giving "thinking" its own colour would turn a five-word vocabulary
# into one label per state, which is the opposite of what a first-time
# participant needs. The distinction belongs to RHYTHM.
#
# The hold is why this exists at all. S4's clip ends at the last station and the
# head then waits for the VLM -- 6 s on a good evening, 14-62 s during the
# service slowdowns measured 2026-08-08. The firmware falls back to its own
# breath after 500 ms of silence, so the light was not frozen; but that fallback
# was deliberately tuned to BE S1_IDLE's envelope, so the wait read as
# `cool` colour + `idle` rhythm. Attending, said one way; unoccupied, said the
# other.
#
# Placed against the clips' own measured envelopes:
#
#     S1_IDLE     led  10..80   period 1.25 s    slow, dim      unoccupied
#     S5B_TRACK   led  26..96   period 0.90 s    quicker        watching
#     S4_PLAN     led  26..223  period 1.23 s    bright accents a shutter per station
#     -> planning  led 12..70   period 0.70 s    quickest, dimmest
#
# QUICKEST AND DIMMEST, and the dimness was got wrong first. The initial value
# was 30..120, brighter than watching -- but the head is perfectly still through
# this stretch, and a bright fast pulse on a still body reads as agitation rather
# than as thought. Under `watching` in level and roughly twice `idle` in rate:
# the brightness says nothing is being looked at, the speed says something is
# happening anyway. Turned inward.
#
# It stays `cool`. A sixth colour for "thinking" would trade a five-word
# vocabulary of state KINDS for one label per state, and the participant meets
# all of it once, for the first time, in a single session.
PLAN_BREATH = {"period_s": 0.70, "low": 12, "high": 70, "hz": 15}

REAIM_TIMEOUT_S = 2.5    # how long S6 waits for a direction before taking the
                         # sweep's next-best angle itself.
                         #
                         # WAS 15, which assumed the person being asked could
                         # answer. They cannot: during S6 the CoreS3 draws only
                         # STOP, and `reaim` arrives solely from the web UI's pan
                         # buttons or the researcher's z/x/c/v/b -- so the tap is
                         # a one-way channel. The robot shook its head, asked
                         # "which one then?", and stood motionless for 12.6 s
                         # waiting for an answer nobody in the room could give.
                         # Held still that long it does not read as waiting, it
                         # reads as broken.
                         #
                         # 2.5 s is S6's own clip (2.37 s) plus a margin, so the
                         # shake IS the wait: it says "not that one" and acts on
                         # it. Nothing is lost -- `reaim` is also accepted in
                         # S5B_TRACK, so a researcher who clicks a moment later
                         # still steers it, and that path already existed for
                         # exactly this case.
                         #
                         # RAISE THIS AGAIN if the participant is ever given a
                         # way to answer. The number encodes who is being asked.

# STOP is a CANCEL, not a pause: the watch-spec is discarded and PTT starts a
# fresh request. Worth being explicit, because "returns to idle" alone leaves it
# ambiguous, and a participant's model of what they just did depends on it.
STOP_DISCARDS_TASK = True

# Buttons the CoreS3 may show. Fixed positions: green affirms, red stops, and
# nothing else ever uses those slots -- so a participant never has to read a
# button to know what it does.
BUTTONS = {
    "PTT":  "hold to speak -- press enters S2, release starts transcription",
    "OK":   "I saw it. Returns S7 to S5, and S8 to S1. A TAP.",
    "STOP": "end the task, return to S1. A HOLD (>= 800 ms in firmware); a tap "
            "on it sends OK instead, so a brush cannot discard the task. Not "
            "drawn on `noticed` or `error` at all.",
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
    "noticed":   ("{n} noticed",   ("OK",)),        # no cancel on the screen the
                                                    # participant is most likely
                                                    # to touch -- see uiLayout
    "error":     ("error",         ("OK",)),        # leaving S8 is affirmative,
                                                    # and nothing is discarded
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
                    name = m.group(1)
                    if name in STATES:
                        continue
                    # A CONSTANT IN THIS MODULE IS NOT A MISTYPED STATE. The
                    # pattern is "S<digit>..." because that is what a state name
                    # looks like, and the tuning constants share the prefix by
                    # design -- S7_IGNORED_TIMEOUT_S, S8_RECOVER_S. Reaching one
                    # by name (getattr(ST, "S8_RECOVER_S", 0), which is how the
                    # optional ones are read so that deleting a constant turns
                    # its feature off rather than crashing) put a legitimate
                    # string literal in front of a scanner looking for typos.
                    #
                    # Resolving against this module's own globals is the right
                    # test rather than a hardcoded skip-list: it stays true when
                    # constants are added, and it still catches a MISSPELLED one,
                    # which is the failure this whole function exists for.
                    if name in globals():
                        continue
                    out.append(f"{os.path.relpath(path, root)}:{i}: "
                               f"{name!r} is not a state")
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
