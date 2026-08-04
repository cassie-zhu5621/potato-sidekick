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
        self.plan_pending = False    # a VLM call is out; the direction is unknown
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
            if self.state in ("S7a", "S7b"):
                self._go("S5B_TRACK", "OK -- seen; back to watching")
            return self.out

        if ev == "planned":
            # The VLM has answered: the direction and the watch-spec now exist.
            self.plan_pending = False
            if self.state == "S5B_TRACK":
                self._ui("tracking")
            return self.out

        if ev == "plan_failed":
            # S4's motion may already have arrived at its S5 hold by the time a
            # remote error comes back. Never leave the participant looking at a
            # permanent "planning..." screen: make the failure explicit.
            self.plan_pending = False
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
            return self.out

        self._emit("log", f"unknown event {event!r}")
        return self.out
