#!/usr/bin/env python3
"""
session_flow.py — the transition rules, with no hardware in them.

Pure logic: events in, state requests and device commands out. Nothing here
opens a serial port, grabs a frame or sleeps. That is the point -- the flow can
be exercised end to end in a unit test, which is the only way to be sure of paths
like "STOP during S4" or "the transcript arrives after the researcher already
corrected it" without standing in front of the robot triggering them by hand.

  events in : "ptt_down" "ptt_up" "ok" "stop" "tap" "transcript:<text>"
              "reject" "finding" "tick"
  out       : a list of (kind, value) -- ("state", "S4_PLAN"), ("ui", "waiting"),
              ("noticed", 3), ("say", "..."), ("log", "...")

The full spec, and why each rule is what it is, is in
robot_motion/INTERACTION_SPEC.md.
"""
from __future__ import annotations
import os, sys, time

from robot import states as ST                    


def transcript_usable(text, no_speech_prob=0.0, avg_logprob=0.0):
    """Is there a REQUEST here at all -- not, is it a good one.

    A misread is recoverable: the researcher retypes it and the run continues. A
    false accept is not: the robot plans confidently against nonsense and the
    session is spent. So this errs strict, and only absence/nonsense reaches S8.
    Returns (ok, reason).
    """
    r = ST.STT_REJECT
    t = (text or "").strip()
    if not t:
        return False, "empty"
    if r["require_letters"] and not any(c.isalpha() for c in t):
        return False, "no letters (Whisper punctuation artefact)"
    if len(t) < r["min_chars"]:
        return False, f"too short ({len(t)} < {r['min_chars']} chars)"
    if len([w for w in t.split() if w.strip()]) < r["min_words"]:
        return False, f"fewer than {r['min_words']} words"
    if no_speech_prob > r["max_no_speech_prob"]:
        return False, f"no_speech_prob {no_speech_prob:.2f}"
    if avg_logprob < r["min_avg_logprob"]:
        return False, f"avg_logprob {avg_logprob:.2f} -- likely hallucinated"

    # DOES THIS LOOK LIKE LANGUAGE AT ALL. The length and word-count tests above
    # pass things that are plainly not requests -- "beep beep beep" is fourteen
    # characters and three words -- and a participant who makes a noise at the
    # robot should see it fail to understand, not watch it plan confidently
    # against a noise. Three cheap tests, no model:
    words = [w.strip(".,!?;:'\"").lower() for w in t.split()]
    words = [w for w in words if w]

    #   1. one word repeated. "beep beep beep", "la la la", "test test".
    if len(words) >= 2 and len(set(words)) == 1:
        return False, f"the same word {len(words)} times -- not a request"

    #   2. a consonant run no English word has. Threshold 6 because real words
    #      reach 5 ("strengths" -> ngths); keyboard mash reaches far more.
    vowels = set("aeiouy")
    for w in words:
        run = best = 0
        for ch in w:
            if ch.isalpha() and ch not in vowels:
                run += 1
                best = max(best, run)
            else:
                run = 0
        if best >= 6:
            return False, f"{w!r} has {best} consonants in a row -- keyboard mash"

    #   3. almost no vowels across the whole thing.
    letters = [c for c in t.lower() if c.isalpha()]
    if len(letters) >= 8:
        ratio = sum(1 for c in letters if c in vowels) / len(letters)
        if ratio < 0.15:
            return False, f"vowel ratio {ratio:.2f} -- not words"
    return True, "ok"


class SessionFlow:
    def __init__(self, now=time.monotonic):
        self.now = now
        self.state = "S1_IDLE"
        self.screen = "idle"
        self.noticed = 0
        self.transcript = None
        self._ptt_up_at = None       # waiting for a transcript since (busy resets it)
        self._ptt_up_first_at = None # ...and when the wait FIRST began (busy cannot)
        self._s7_at = None           # in S7b since
        self._reaim_at = None        # in S6 awaiting a direction since
        self.stt_busy = False        # set by the loop while Whisper is running
        self.judge_busy = False      # set by the loop while a candidate is being judged
        self.plan_pending = False    # a VLM call is out; the direction is unknown
        self._plan_at = None         # when it went out, for PLAN_TIMEOUT_S
        self._watch_since = None     # watching this angle since, for REPLAN_IDLE_S
        self._s8_at = None           # in S8 since, for S8_RECOVER_S
        self._planned_at = None      # the last plan LANDED at, for REPLAN_PERIOD_S
        self.out = []

    # ---------------- helpers ----------------
    def _emit(self, kind, val):
        self.out.append((kind, val))

    def _go(self, state, why=""):
        self.state = state
        self.screen = ST.STATES[state]["screen"]
        self._emit("state", state)
        self._emit("ui", self.screen)
        if why:
            self._emit("log", f"{state}: {why}")
        self._s7_at = self.now() if state == "S7b" else None
        # The idle clock is per-ANGLE, not per-session: it asks "has this aim
        # produced anything", so it restarts every time watching is (re-)entered
        # and stops whenever the robot is doing something else.
        self._watch_since = self.now() if state == "S5B_TRACK" else None
        self._s8_at = self.now() if state == "S8_ERROR" else None

    def _replan(self, why):
        """Re-fire S4 on the request already on record.

        NOT via S3_ACK. S3 is "I heard you", and nobody has said anything -- the
        robot would be acknowledging a sentence that was never spoken. This is the
        robot deciding on its own to go and look again, so it goes straight to the
        sweep. `plan` re-arms the sweep loop-side and bumps plan_generation.
        """
        if not self.transcript:
            return                    # nothing has ever been asked; nothing to redo
        self.plan_pending = True
        self._plan_at = self.now()
        self._planned_at = None
        self._emit("plan", self.transcript)
        self._go("S4_PLAN", why)

    def _ui(self, screen):
        self.screen = screen
        self._emit("ui", screen)

    # ---------------- the rules ----------------
    def feed(self, event):
        """One event -> a list of (kind, value) to act on."""
        self.out = []
        ev, _, arg = event.partition(":")

        # STOP is checked FIRST and from every state. A cancel that only works
        # from some states is not a cancel, and this is the one control a
        # participant must be able to trust unconditionally.
        if ev == "stop":
            self.transcript = None       # STOP discards the task (STOP_DISCARDS_TASK)
            self._ptt_up_at = self._ptt_up_first_at = None
            self.plan_pending = False
            # NOTICED is scoped to one participant task, not to the lifetime of
            # the laptop process. Reset through the same output channel that
            # normally increments it so CoreS3 and the browser stay in sync.
            self.noticed = 0
            self._emit("noticed", 0)
            self._go("S1_IDLE", "STOP -- task discarded, waiting for a new request")
            # Discarding the task has to reach PERCEPTION as well, not just the
            # motion. A robot that has visibly stopped while its watch-spec keeps
            # evaluating is not stopped; it is stopped-looking.
            self._emit("idle", True)
            return self.out

        if ev == "ptt_down":
            if self.state != "S1_IDLE":
                self._emit("log", f"PTT ignored in {self.state}")
            else:
                self.transcript = None
                self._go("S2_LISTEN", "PTT pressed")
                self._ui("recording")
                self._emit("rec", "start")
            return self.out

        if ev == "ptt_up":
            if self.state == "S2_LISTEN":
                self._emit("rec", "stop")
                self._ptt_up_at = self._ptt_up_first_at = self.now()
                # The robot holds, facing the person. It does NOT nod yet: the nod
                # and "I heard you." are one act, and nodding before the words
                # exist would claim understanding of something not yet read.
                self._ui("waiting")
            return self.out

        # A request arriving as text. Two sources, ONE acceptance rule and ONE
        # outcome:
        #   transcript: -- from Whisper, i.e. from the participant's own turn
        #   typed:      -- from the researcher's web UI box
        # They now differ only in WHERE they may arrive from. Whisper speaks only
        # during S2; typed text is accepted from every state, including S1_IDLE,
        # and always re-plans. Nonsense from either one goes to S8, because what
        # a participant can see is the robot, not the keyboard.
        if ev in ("transcript", "typed"):
            manual = (ev == "typed")
            # Whisper only speaks during the participant's turn. TYPED text is
            # accepted from EVERY state and always re-plans: that is the wizard
            # channel, it is the researcher's deliberate act, and in the version
            # before the state machine every typed line re-planned on the next
            # frame. Making it conditional on state made the control feel dead --
            # you type, and nothing happens, with the reason buried in a log.
            if not manual and self.state != "S2_LISTEN":
                self._emit("log", f"transcript arrived in {self.state}, ignored")
                return self.out
            ok, why = transcript_usable(arg)
            self._ptt_up_at = self._ptt_up_first_at = None
            if ok:
                self.transcript = arg.strip()
                self.plan_pending = True
                self._plan_at = self.now()
                self._emit("plan", self.transcript)
                self._go("S3_ACK", f"{'typed' if manual else 'heard'} "
                                   f"{self.transcript!r}")
            else:
                # BOTH sources error, whatever the text came in on. An earlier
                # version refused typed nonsense quietly, on the reasoning that a
                # researcher's typo should not make the robot perform a failure.
                # That was the wrong call for a study: the participant is looking
                # at the robot, not at the keyboard, and "it did not understand"
                # has to be legible from where they are sitting. If the words are
                # not a request, the robot says so -- the same way, every time.
                src = "typed" if manual else "heard"
                self._go("S8_ERROR", f"unusable request ({src}): {why}")
            return self.out

        if ev == "tap":
            # "not that one". Only meaningful while watching; a tap during the
            # scan would be rejecting a choice that has not been made yet.
            if self.state == "S5B_TRACK":
                self._go("S6_FINETUNE", "body tap -- wrong direction")
                self._reaim_at = self.now()
                self._emit("await_reaim", True)
            else:
                self._emit("log", f"tap ignored in {self.state}")
            return self.out

        if ev == "reaim":
            # Accepted in S6 (the normal case) AND in S5 (a click that arrived
            # after S6 finished, or a researcher nudging the aim mid-watch). Both
            # are the same act: change where it looks, keep what it is looking
            # for. Restricting it to S6 made a late click do nothing at all, with
            # no feedback about why.
            if self.state in ("S6_FINETUNE", "S5B_TRACK"):
                self._emit("pan", float(arg))
                self._reaim_at = None
                if self.state == "S6_FINETUNE":
                    # S5A, not S5B. The direction CHANGED and a person changed
                    # it, so the arrival is a result and the crane onto it is an
                    # authored beat. The timeout path below goes to S5B instead,
                    # because there the aim did NOT change -- and that makes
                    # giving up visibly quieter than being answered, which is
                    # right: nothing was decided, so nothing is performed.
                    # See S4_S5_DESIGN.md sec 9.4.
                    self._go("S5A_SETTLE", f"re-aimed to pan {arg} -- same "
                                           f"watch-spec, new direction")
                else:
                    self._emit("log", f"re-aimed to pan {arg} while watching")
            else:
                self._emit("log", f"reaim ignored in {self.state}")
            return self.out

        if ev == "finding":
            if self.state in ("S5B_TRACK",):
                self.noticed += 1
                self._emit("noticed", self.noticed)
                self._go("S7a", "a confirmed finding")
            else:
                self._emit("log", f"finding ignored in {self.state}")
            return self.out

        if ev == "ok":
            if self.state == "S8_ERROR":
                # THE ERROR SCREEN'S BUTTON IS OK, NOT STOP (firmware uiLayout).
                # Getting out of S8 is an affirmative act -- "I have seen that it
                # failed" -- and there is nothing to cancel: S8 is reached when a
                # request was unusable or a plan never arrived, so no watch-spec
                # exists to discard. Demanding a hold, the gesture the red button
                # now needs, would be the wrong thing to ask of somebody looking
                # at a robot that has just given up.
                #
                # S8_RECOVER_S still gives up on its own after 16 s. This is the
                # same exit, reachable at once by whoever is watching, and it
                # abandons the same in-flight work for the same reason: a
                # transcript or plan arriving later must not resurrect a turn the
                # person watched end.
                self._s8_at = None
                self.plan_pending = False
                self._plan_at = self._ptt_up_at = self._ptt_up_first_at = None
                self._go("S1_IDLE", "OK -- error acknowledged, back to idle")
                return self.out
            if self.state in ("S7a", "S7b"):
                # THE BOARD'S COUNT IS AN INBOX, NOT A SCORE. It says how much is
                # waiting for the person, so acknowledging clears it for the same
                # reason STOP does: both end the state of having something
                # unseen. Left running, the number only ever climbs, and a
                # counter that cannot go down stops being read -- the participant
                # has no way to tell "one new thing" from "the same seven again".
                #
                # Only the BOARD. The page keeps every card, because it answers a
                # different question -- what has it found for me -- and that
                # answer should not depend on which button was pressed. See
                # session/feed.py.
                self.noticed = 0
                self._emit("noticed", 0)
                self._go("S5B_TRACK", "OK -- seen; back to watching")
            return self.out

        if ev == "planned":
            # The VLM has answered: the direction and the watch-spec now exist.
            self.plan_pending = False
            self._plan_at = None
            self._planned_at = self.now()
            # The idle clock starts when there is something to watch FOR. S5b is
            # usually already entered by now -- S4's clip ends before the VLM
            # does -- so _go's reset happened while the spec was still in flight,
            # and 30 s of "seeing nothing" would have been counted against an
            # angle the robot had no criteria for yet.
            if self.state == "S5B_TRACK":
                self._watch_since = self.now()
                self._ui("tracking")
            return self.out

        if ev == "plan_failed":
            # S4's motion may already have arrived at its S5 hold by the time a
            # remote error comes back. Never leave the participant looking at a
            # permanent "planning..." screen: make the failure explicit.
            self.plan_pending = False
            self._plan_at = None
            self._go("S8_ERROR", f"planner failed: {arg}")
            return self.out

        if ev == "arrived":
            # the player finished a one-shot and its `then` moved it on
            if arg in ST.STATES:
                self.state = arg
                screen = ST.STATES[arg]["screen"]
                # S4's clip ends before the VLM does. The robot is holding at the
                # last angle it swept while the analysis runs, and it does not yet
                # know where to look -- so it must not claim to be tracking. The
                # screen stays "planning..." until `planned` arrives. Saying
                # "tracking..." while the answer is still in flight is a claim
                # about the robot's state that is simply untrue, and a participant
                # has no way to tell it apart from the real thing.
                if arg == "S5B_TRACK" and self.plan_pending:
                    screen = "planning"
                self._ui(screen)
                self._s7_at = self.now() if arg == "S7b" else None
                self._s8_at = self.now() if arg == "S8_ERROR" else None
            return self.out

        if ev == "tick":
            t = self.now()
            # `is not None`, NOT truthiness: a timestamp of 0.0 is falsy, so the
            # truthy version silently disabled both timeouts whenever the first
            # action happened at the clock's origin. It only shows up in a test
            # with a fake clock -- on real hardware monotonic() is never 0.
            hard = (self._ptt_up_first_at is not None
                    and t - self._ptt_up_first_at > ST.STT_HARD_TIMEOUT_S)
            if self.stt_busy and not hard:
                # A transcription that is actually running is not a timeout. The
                # deadline exists to catch a recogniser that never answers, not a
                # slow one; without this a cold model loses the request it is in
                # the middle of successfully transcribing. `hard` is the ceiling:
                # busy may postpone, it may not postpone indefinitely.
                self._ptt_up_at = t
            # A planner that never answers. Checked BEFORE the STT deadline
            # because they cannot both be live: the transcript is what starts the
            # plan, so by the time this is armed the STT one is already cleared.
            if (self.plan_pending and self._plan_at is not None
                    and t - self._plan_at > ST.PLAN_TIMEOUT_S):
                self.plan_pending = False
                self._plan_at = None
                self._go("S8_ERROR", "no plan within "
                                     f"{ST.PLAN_TIMEOUT_S:.0f}s -- the VLM never "
                                     f"answered")
                return self.out
            if self._ptt_up_at is not None and (
                    hard or t - self._ptt_up_at > ST.STT_TIMEOUT_S):
                self._ptt_up_at = self._ptt_up_first_at = None
                self._go("S8_ERROR", "no transcript within "
                                     f"{ST.STT_TIMEOUT_S:.0f}s")
            elif (self._reaim_at is not None and self.state == "S6_FINETUNE"
                    and t - self._reaim_at > ST.REAIM_TIMEOUT_S):
                self._reaim_at = None
                # NOT the same spot. The tap said "wrong direction"; going back to
                # the identical view answers the complaint with a shrug. With no
                # human answer, pick the next-best angle the sweep scored -- the
                # loop owns that data, so it is asked rather than told.
                self._emit("pan_next", True)
                self._go("S5B_TRACK", f"no direction within "
                                     f"{ST.REAIM_TIMEOUT_S:.0f}s -- moving on to "
                                     f"the next-best angle from the sweep")
            elif (self._s7_at is not None and self.state == "S7b"
                    and t - self._s7_at > ST.S7_IGNORED_TIMEOUT_S):
                # Being ignored is a normal outcome, not a failure: the person is
                # busy, which is the premise. The finding is already in the feed,
                # so go back to watching instead of escalating.
                self._go("S5B_TRACK", f"ignored for "
                                     f"{ST.S7_IGNORED_TIMEOUT_S:.0f}s -- "
                                     f"already in the feed, back to watching")
            # ---- S8 gives up on its own. `exit: STOP only` assumed a reader
            # who could press STOP; a participant cannot, and cannot name what
            # broke either. Returning to S1 is not pretending it did not happen
            # -- S1 is "present, not attending", which is the truthful state
            # after a failure the robot has stopped trying to fix. The reason is
            # already in the log and on the researcher's screen.
            elif (self.state == "S8_ERROR"
                    and getattr(ST, "S8_RECOVER_S", 0)
                    and self._s8_at is not None
                    and t - self._s8_at > ST.S8_RECOVER_S):
                self._s8_at = None
                # Everything in flight is abandoned. A transcript or plan that
                # arrives after this must not resurrect a turn the robot has
                # visibly ended -- the person watched it give up.
                self.plan_pending = False
                self._plan_at = self._ptt_up_at = self._ptt_up_first_at = None
                self._go("S1_IDLE", f"gave up after {ST.S8_RECOVER_S:.0f}s in "
                                    f"S8 -- back to idle, ready to be asked again")
            # ---- S4 re-fires. states.py has documented these two since the
            # state table was written and NOTHING READ THEM: the constants were
            # defined, commented, referenced by a Blender preview's comment, and
            # never wired. So the robot watched one angle until someone tapped
            # it, which is exactly what was observed -- 30 s passes, 5 min
            # passes, and it keeps looking at the same wall.
            #
            # Only from S5B_TRACK. S6 is a correction in progress and S7 is a
            # report being delivered; interrupting either to go and sweep would
            # abandon a turn the person is part of. Not while plan_pending
            # either -- one sweep is already out.
            #
            # PERIOD IS CHECKED FIRST. If both are due the structural reason is
            # the stronger one: "the room may have changed" subsumes "this angle
            # is quiet", and logging it as the idle case would misreport why.
            elif self.judge_busy and self.state == "S5B_TRACK":
                # A JUDGE IN FLIGHT IS NOT AN EMPTY ANGLE. The idle clock asks
                # "has this aim produced anything"; a candidate under judgement
                # is something it produced, still waiting on a verdict. Letting
                # the clock run through that re-plans mid-call, bumps
                # plan_generation, and the answer -- when it arrives -- is
                # discarded as stale.
                #
                # Observed on hardware 2026-08-05: a judge started at 15:01:10,
                # the 60 s timer fired while it ran, and the reply at 15:03:04
                # (`selected_index: 0`, both cards confirmed, "The user is
                # holding and reading a book") was thrown away by
                # `[confirm] stale candidate discarded`. A correct finding, a
                # correct judgement, deleted by a clock that was measuring the
                # wrong thing.
                #
                # Postponed, not cancelled -- the same shape as stt_busy above:
                # busy may push the deadline out, it may not remove it.
                self._watch_since = t
            elif (self.state == "S5B_TRACK" and not self.plan_pending
                    and self.transcript):
                per = getattr(ST, "REPLAN_PERIOD_S", 0) or 0
                idle = getattr(ST, "REPLAN_IDLE_S", 0) or 0
                if (per and self._planned_at is not None
                        and t - self._planned_at > per):
                    self._replan(f"{per:.0f}s since the last plan -- sweeping "
                                 f"again; anything that entered the room since "
                                 f"has never been in the candidate set")
                elif (idle and self._watch_since is not None
                        and t - self._watch_since > idle):
                    self._replan(f"nothing at this angle for {idle:.0f}s -- "
                                 f"that is a real detection, not a fault: there "
                                 f"is nothing here, so look elsewhere")
            return self.out

        self._emit("log", f"unknown event {event!r}")
        return self.out
