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
    def __init__(self, feed_dir="session_feed", ui=None, burst_n=10,
                 interval=4.0, linger=6.0, scene_diff=8.0, offline=False):
        self.feed_dir, self.ui = feed_dir, ui
        self.burst_n, self.interval = burst_n, interval
        self.linger, self.scene_diff = linger, scene_diff
        self.offline = offline or os.environ.get("SECONDATTN_OFFLINE") == "1"
        self.taste = ReportabilityTaste()
        self.bursts = []
        self.count = 0
        os.makedirs(feed_dir, exist_ok=True)

    # ------------------------------------------------------------------ open --
    def open(self, entry, frame, truth, viz, idx):
        """A watch entry fired -> start collecting the story."""
        t = time.time()
        self.bursts.append({
            "label": entry.get("label", "noticed"), "idx": idx,
            "shots": [frame.copy()], "traces": [shot_trace(truth, viz)],
            "last_truth": dict(truth),
            "last_gray": cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
            "next": t + self.interval, "ends_at": t + self.linger,
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
    def step(self, frame, truth, viz, statuses, t=None):
        """Advance every open burst. Call once per perception tick."""
        if frame is None or not self.bursts:
            return
        t = time.time() if t is None else t
        for b in list(self.bursts):
            # While the relation still holds, push the end back: the story is not
            # over just because the linger window started.
            if 0 <= b["idx"] < len(statuses) and statuses[b["idx"]].satisfied:
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

    # -------------------------------------------------------------- finalize --
    def _finalize(self, b):
        """Narrate + publish. Off the main thread: this makes a network call and
        the live loop still owes the LED a heartbeat every 300 ms."""
        n = len(b["shots"])
        strip = make_strip(b["shots"])
        story = " -> ".join(dict.fromkeys(b["traces"]))   # dedup, keep order
        note, worth = f"{b['label']}: {story}", None
        if not self.offline:
            try:
                r = run_judge(cv2.imencode(".jpg", strip)[1].tobytes(), None,
                              self.taste, story=story)
                note, worth = r["note"], r["worth"]
            except Exception as e:
                print(f"[judge] error: {e} -- keeping the grounded trace as the note")
        note = f"{note} ({n}-shot story)"
        print(f"[MOMENT] {b['label']} :: {note}")
        fid = time.strftime("%Y%m%d_%H%M%S_") + f"{int(time.time() * 1000) % 1000:03d}"
        rec = {"time": time.strftime("%H:%M:%S"),
               "worth": worth if worth is not None else "-",
               "why": "watch-spec", "note": note,
               "thumb": f"thumb_{fid}.jpg", "frame": f"frame_{fid}.jpg",
               "label": b["label"], "shots": n, "story": story,
               "truth": b["truth"]}
        import argparse
        publish(strip, rec, argparse.Namespace(save=True, feed_dir=self.feed_dir),
                self.ui)
        self.count += 1
