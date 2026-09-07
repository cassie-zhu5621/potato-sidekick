#!/usr/bin/env python3
"""
storyboard.py — a finding is a STORY, not a screenshot.

WHAT THIS IS. When a watch entry fires, the moment is not over: it is beginning.
The system opens a BURST and keeps shooting while the thing unfolds, then narrates
the panels and publishes one horizontal comic strip to the NOTICED feed. A single
frame of the triggering instant is the least informative moment of the whole
event -- it is the frame before anything has happened yet.

THE KEYFRAME RULE, which is the part worth keeping. A panel is added only when the
scene actually CHANGED since the last one:

    changed = (truth vector differs)  OR  (mean abs image diff > scene_diff)

...and never faster than `interval`. So a long unfolding story earns many panels,
a brief one stays short, and near-duplicate frames -- which is what a fixed-rate
capture mostly produces -- are never stored. The truth vector is checked FIRST
because it changes on things the pixels barely show: a gaze landing, a hand
leaving an object.

THE NARRATION IS GROUNDED. Each panel carries `shot_trace(truth, viz)`, a summary
of what the system actually DETECTED in that frame ("gazing-at laptop, proxemic").
The story is those traces joined in order, and that string is what the VLM is asked
to narrate from -- so the caption describes the sequence the CV really saw, rather
than being re-captioned off a photo. That distinction is the whole reason the notes
are trustworthy enough to put in a paper.

The burst rule, the trace format, the strip and the record layout are ported from
attention_system.py unchanged; `make_strip`, `shot_trace`, `judge` and `publish`
are imported, not copied.
"""
from __future__ import annotations
import os, threading, time

import cv2

from perception.overlay import make_strip, shot_trace
from session.feed import publish
from planning.judge import judge as run_judge, ReportabilityTaste


class Storyboard:
    # HOW FAR THE HEAD MAY HAVE MOVED and still be the same shot.
    #
    # The camera is on the head, so every panel is taken from wherever the neck
    # happens to be. A story opens while S5b watches at tilt -12 and its later
    # panels are taken during S7's rests at tilt -22 -- ten degrees, about a
    # third of the vertical field, so the strip jumps between its first panel
    # and the rest. Reported 2026-08-08: "the storyboard's viewpoint keeps
    # changing, sometimes quite a lot."
    #
    # 3 degrees is under a tenth of the frame in either axis: close enough that
    # consecutive panels read as one continuous shot rather than as cuts.
    POSE_TOL_DEG = 3.0

    # AND THE CEILING ON HOLDING THE CLOCK. S7b loops until OK or
    # S7_IGNORED_TIMEOUT_S (30 s), and the robot only returns to the watching
    # pose after that, so a story can legitimately be paused for half a minute.
    # If it is paused for longer than this, something has gone wrong -- a re-aim
    # to a different angle, a researcher override -- and the story is about a
    # place the robot is no longer looking at. Publish what it has.
    MAX_STORY_S = 45.0

    def __init__(self, feed_dir="session_feed", ui=None, burst_n=10,
                 interval=4.0, linger=6.0, scene_diff=8.0, offline=False):
        self.feed_dir, self.ui = feed_dir, ui
        self.burst_n, self.interval = burst_n, interval
        self.linger, self.scene_diff = linger, scene_diff
        self.offline = offline or os.environ.get("SECONDATTN_OFFLINE") == "1"
        self.taste = ReportabilityTaste()
        self.bursts = []
        self.count = 0
        self.generation = 0
        # WHICH REQUEST THIS FINDING ANSWERS. attention_log.jsonl is ONE file per
        # run, appended, while a run routinely carries several plans -- 9 planner
        # calls in e2e_20260805_132357 alone. Without these two fields a line
        # cannot be traced back to the request that produced it, and a session
        # where the participant asks twice becomes two sets of findings in one
        # undifferentiated list. Set by the loop on every re-plan.
        self.request = ""
        self.plan_generation = 0
        # WHAT THE PRECISION GATE WOULD HAVE SAID. With JUDGE_DEADLINE_S = 0 the
        # group judge no longer decides whether S7 plays -- the CV gate does --
        # but it still runs, and its verdict is stamped here so the false-report
        # rate is a column in attention_log.jsonl rather than a line that
        # scrolled off a terminal. True agreed, False would have rejected this,
        # None it had not answered by the time the story closed.
        #
        # Set by the loop the moment the verdict lands. Read, not consumed: one
        # judge call covers a moment, and a moment is one story.
        self.judge_agreed = None
        # THE SENTENCE THE JUDGE ALREADY WROTE, if it has landed. See _finalize.
        self.describe = ""
        os.makedirs(feed_dir, exist_ok=True)

    def reset(self):
        """STOP begins a fresh task. CLOSE the open stories -- never discard them.

        This used to `bursts.clear()`, and that is how a judge-CONFIRMED finding
        vanished on 2026-08-12: confirmed 17:45:14, STOP pressed ~40 s later to
        give a new instruction, nothing in attention_log.jsonl.

        A STORY ONLY EXISTS AFTER THE ROBOT HAS ALREADY SPOKEN. `open` hangs off
        the flow's `noticed` emission, not off the detector, so by the time there
        is a burst the chirp has sounded and the card is on the board -- the
        participant may already have pressed OK. STOP voids the PLAN, meaning no
        further findings; it cannot un-tell them something. Every burst this
        method used to cancel was one that had already reached somebody.

        Threaded, unlike the teardown flush: STOP is mid-session and `_finalize`
        makes a narration call, so doing it inline would hold the loop for
        several seconds with the LED heartbeat owed every 300 ms.
        """
        self.flush("task stopped", threaded=True)
        self.generation += 1
        self.count = 0
        # The judge's sentence belongs to the finding it was written for. Left
        # standing, the next task's first card would wear the last one's words.
        self.describe = ""
        self.judge_agreed = None

    # ------------------------------------------------------------------ open --
    def open(self, entry, frame, truth, viz, idx, pose=None):
        """A watch entry fired -> start collecting the story.

        `pose` is the head's angles at this instant. Every later panel is
        required to match it -- see POSE_TOL_DEG.
        """
        t = time.time()
        self.bursts.append({
            "label": entry.get("label", "noticed"), "idx": idx,
            # THE ENTRY'S OWN RELATION IDS, so `step` can ask whether the event
            # is happening NOW. Empty for a researcher-forced finding, which has
            # no spec entry behind it -- those close on `linger` alone.
            "ids": sorted(set(entry.get("all") or []) | set(entry.get("any") or [])
                          | set(entry.get("then") or [])),
            "generation": self.generation,
            "shots": [frame.copy()], "traces": [shot_trace(truth, viz)],
            "last_truth": dict(truth),
            "last_gray": cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
            "next": t + self.interval, "ends_at": t + self.linger,
            # THE SHOT. Panels are only taken from here, so the strip is one
            # viewpoint rather than a cut every time the neck moves.
            "pose": dict(pose) if pose else None,
            "hard_until": t + self.MAX_STORY_S,
            "truth": {k: int(v) for k, v in truth.items()},
        })
        print(f"[story] opened: {entry.get('label')}")

    def collecting(self):
        """Open bursts, for the page. A story takes `linger` seconds to close, so
        without this the NOTICED feed looks broken for the six seconds when it is
        in fact working -- and "nothing appeared" is indistinguishable from
        "nothing was recorded"."""
        return [{"label": b["label"], "panels": len(b["shots"]),
                 "max": self.burst_n} for b in self.bursts]

    # ------------------------------------------------------------------ step --
    @staticmethod
    def _same_shot(a, b, tol):
        """Is the head where it was when the story opened?"""
        if not a or not b:
            return True             # no pose data: behave as before
        return all(abs(float(a.get(j, 0.0)) - float(b.get(j, 0.0))) <= tol
                   for j in ("pan", "tilt", "nod"))

    def step(self, frame, truth, viz, statuses, t=None, pose=None):
        """Advance every open burst. Call once per perception tick."""
        if frame is None or not self.bursts:
            return
        t = time.time() if t is None else t
        for b in list(self.bursts):
            # OFF THE SHOT: TAKE NOTHING, AND AGE NOTHING.
            #
            # While S7 performs, the neck is ten degrees below the watching
            # pose and the camera is no longer framing what the story is about.
            # Skipping the panels alone would leave the story to expire during
            # the performance, so a long event would be recorded as its opening
            # frame and nothing else. Holding the clock too means the follow-
            # through -- the sitting down, the walking away -- is still there to
            # be photographed when the robot returns to watching.
            #
            # The cap below is what stops that becoming forever.
            if t > b.get("hard_until", t):
                self.bursts.remove(b)
                threading.Thread(target=self._finalize, args=(b,),
                                 daemon=True).start()
                continue
            if not self._same_shot(b.get("pose"), pose, self.POSE_TOL_DEG):
                b["ends_at"] = max(b["ends_at"], t + self.linger)
                b["next"] = max(b["next"], t)
                continue
            # While the relation still holds, push the end back: the story is not
            # over just because the linger window started.
            #
            # ASK THE TRUTH VECTOR, NOT `satisfied`. EntryStatus.satisfied means
            # "this entry's condition was met within the last `within_s`
            # seconds" -- the COMPOSITION window, which exists so that `all:
            # [1, 9]` need not have both relations true on the same frame. It is
            # latched by design, and reading it as "the event is still
            # happening" kept every story alive for within_s after the room went
            # quiet.
            #
            # With the planner's usual within_s=5 and linger=6 that put a FLOOR
            # of 11 s on every story -- panels at 0, 4 and 8 s -- so a hand
            # placed and immediately withdrawn produced the same three panels as
            # a minute of activity. Reported 2026-08-08: "I tried taking my hand
            # away straight away and it is still a 3-shot story."
            live = any(truth.get(i) for i in b["ids"]) if b["ids"] else False
            if live:
                b["ends_at"] = t + self.linger
            if t >= b["next"] and len(b["shots"]) < self.burst_n:
                g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                changed = dict(truth) != b["last_truth"]
                if not changed and b["last_gray"].shape == g.shape:
                    changed = float(cv2.absdiff(g, b["last_gray"]).mean()) > self.scene_diff
                if changed:
                    b["shots"].append(frame.copy())
                    b["traces"].append(shot_trace(truth, viz))
                    b["last_truth"], b["last_gray"] = dict(truth), g
                b["next"] = t + self.interval
            # Capturing continues THROUGH the linger window, so the follow-through
            # ("...and then they moved to the group") is on record too, not only
            # the instant that tripped the gate.
            if len(b["shots"]) >= self.burst_n or t > b["ends_at"]:
                self.bursts.remove(b)
                threading.Thread(target=self._finalize, args=(b,),
                                 daemon=True).start()

    def flush(self, why="run ended", threaded=False):
        """Close every open burst with the shots it has. -> how many were written.

        A story stays open through `linger` so the follow-through is on record,
        which means Ctrl-C during those seconds threw the whole finding away --
        the strip, the sentence and the log line. Observed 2026-08-12: a finding
        the judge had CONFIRMED never reached attention_log.jsonl because the run
        was stopped a few seconds later.

        `threaded=False` for the teardown: the process is on its way out and a
        daemon thread would be killed halfway through writing the jpeg. STOP
        passes True -- see `reset`.

        THE PROVENANCE IS SNAPSHOT HERE, not read at finalize time. `_finalize`
        takes the request, the judge's sentence and its verdict off `self`, and
        the two callers of this method both wipe those fields microseconds later
        (`reset`) or exit the process. A threaded finalize would find them empty
        and publish a card with no sentence and no request attached.
        """
        pending, self.bursts = list(self.bursts), []
        for b in pending:
            b["truncated"] = True
            b.setdefault("describe", self.describe)
            b.setdefault("judge_agreed", self.judge_agreed)
            b.setdefault("request", self.request)
            b.setdefault("plan_generation", self.plan_generation)
        for b in pending:
            if threaded:
                threading.Thread(target=self._finalize, args=(b,),
                                 daemon=True).start()
                continue
            try:
                self._finalize(b)
            except Exception as exc:            # one bad strip must not eat the rest
                print(f"[story] could not close {b.get('label','?')}: {str(exc)[:80]}")
        if pending:
            print(f"[story] {len(pending)} open story(ies) closed early ({why}) -- "
                  f"written with the shots they had")
        return len(pending)

    # -------------------------------------------------------------- finalize --
    def _finalize(self, b):
        """Narrate + publish. Off the main thread: this makes a network call and
        the live loop still owes the LED a heartbeat every 300 ms."""
        # NO STORY IS EVER CANCELLED. Two `generation != self.generation` guards
        # used to sit here -- one at entry, one after the narration call -- and
        # they dropped the record of a finding the robot had already announced.
        # See `reset`. The generation is kept as PROVENANCE (`story_generation`
        # in the record below), which is all it was ever entitled to be: a note
        # of which task the finding belonged to, not a licence to delete it.
        n = len(b["shots"])
        strip = make_strip(b["shots"])
        story = " -> ".join(dict.fromkeys(b["traces"]))   # dedup, keep order
        note, worth = f"{b['label']}: {story}", None
        # THE BURST'S OWN COPY WHERE IT HAS ONE. `flush` takes these four off
        # `self` at close time because its callers wipe them immediately after --
        # a threaded finalize would otherwise narrate with an empty opening and
        # publish a card belonging to no request. Normal closes have no snapshot
        # and fall through to `self`, which is still current for them.
        describe = b.get("describe", self.describe)
        # ONE PANEL, ONE SENTENCE -- AND IT IS ALREADY WRITTEN.
        #
        # The group judge read five separate frames at the instant the gate
        # fired, with the participant's request in hand, and produced a sentence
        # about exactly that. For a story that never developed past its opening
        # there is nothing to add, so there is nothing to ask.
        #
        # MORE PANELS MEANS THE EVENT WENT ON, and the arc is the point: "came
        # in" is not the same finding as "came in and sat down". So the panels
        # are narrated -- but as SEPARATE FRAMES, not as the composited strip.
        #
        # That distinction is the whole of the 2026-08-08 "one person described
        # as two people" bug. The strip is ONE WIDE IMAGE containing the same
        # person at several positions; asked how many people are in it, there is
        # no right answer. Sent as N images the question does not arise, and the
        # model is told which one is the trigger and that the rest are what
        # followed. The strip is still built and still saved -- it is what a
        # person opens from the feed -- it is simply not what the model reads.
        if n <= 1:
            note = describe or note
        elif not self.offline:
            try:
                shots = [cv2.imencode(".jpg", f)[1].tobytes() for f in b["shots"]]
                r = run_judge(shots, None, self.taste, story=story, panels=n,
                              opening=describe)
                note, worth = r["note"], r["worth"]
            except Exception as e:
                print(f"[judge] error: {e} -- keeping the opening sentence")
                note = describe or note
        else:
            note = describe or note
        note = f"{note} ({n}-shot story)"
        # STOP can arrive while Gemini is narrating, and this is where the second
        # guard threw the finished card away. A card landing on the page after
        # STOP is correct: it is stamped with the request it answers and the page
        # groups by request, so it slots under the old brief rather than
        # pretending to belong to the new one.
        print(f"[MOMENT] {b['label']} :: {note}")
        fid = time.strftime("%Y%m%d_%H%M%S_") + f"{int(time.time() * 1000) % 1000:03d}"
        rec = {"time": time.strftime("%H:%M:%S"),
               "worth": worth if worth is not None else "-",
               "why": "watch-spec", "note": note,
               "thumb": f"thumb_{fid}.jpg", "frame": f"frame_{fid}.jpg",
               "label": b["label"], "shots": n, "story": story,
               # THE STRIP ENDED BECAUSE THE RUN DID, not because the event did.
               # Its length is when Ctrl-C was pressed, so it must not be pooled
               # with strips whose length is the event's own. Internal: the
               # review page never shows it (session/review.py).
               "truncated": bool(b.get("truncated")),
               "truth": b["truth"],
               # provenance -- see __init__
               "request": b.get("request", self.request),
               "plan_generation": b.get("plan_generation", self.plan_generation),
               # THE BURST'S OWN, not the board's current one. They were always
               # equal before -- the cancel guard enforced it -- and reading it
               # off `self` became a race the moment reset() started flushing:
               # the thread may publish before or after `generation += 1`, so
               # the same story logged 0 or 1 depending on scheduling.
               "story_generation": b.get("generation", self.generation),
               "judge_agreed": b.get("judge_agreed", self.judge_agreed)}
        import argparse
        publish(strip, rec, argparse.Namespace(save=True, feed_dir=self.feed_dir),
                self.ui)
        self.count += 1
