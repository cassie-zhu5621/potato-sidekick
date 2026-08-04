#!/usr/bin/env python3
"""
plan_view.py — the VLM compiler + CV layer, as the MAIN web page again.

WHY THIS FILE EXISTS. noticebot_loop was written to drive the eight-state motion
machine, and it borrowed attention_ui's STATE slots to show that state machine on
the main page. That was a mistake: it overwrote the one surface that shows the
thing the paper is actually about -- the planner's compiled watch-spec, each entry
lit as its relations become true, the relation ribbon, and the detections. State
monitoring is a debugging need and belongs on its own page (the ROBOT tab).

WHAT IS RESTORED, unchanged in behaviour from attention_system.py:
  * typing in the context box RE-PLANS immediately, from any state
  * THE PLAN panel: context, the VLM's "why", every watch entry with its live
    satisfied/cooling state, and the id chips
  * THE RELEVANCE LAYER, end to end. The VLM enumerates every object in the frame
    (`seen`) and tiers it: `detect` = context, `focus` = may trigger. That tiering
    then DRIVES THE DETECTOR with no rebuild -- an open-vocab model is re-prompted
    through set_vocab, closed COCO YOLO is filtered by the synonym-expanded
    whitelist -- and closes at the far end in `_focus_ok`, which lets a gaze or
    point open a story only when it lands on a focus object. All three tiers are
    published to the panel and drawn on the frame, because the tiering is a
    judgement the VLM made and the point is that it can be argued with.
  * the overlay: detection boxes BY TIER, BlazePose skeletons, gaze rays,
    pointing arrows, joint-attention circles, and the bottom relation ribbon
  * `fired` entries -- which is what replaces the `f` keyboard stand-in

Nothing here is a reimplementation: the engine, the executor, the ribbon and the
spec formatter are IMPORTED from the files that already work. A second copy of
the relation logic would drift from the first, and the drift would show up as two
systems disagreeing about what was noticed -- in the data, not in a traceback.

Only `apply_relevance` is rewritten, because in attention_system it is a closure
over main()'s locals and cannot be imported. It is nine lines and reproduced
exactly; RELEVANCE_THEN_SPEC.md is the spec for it.
"""
from __future__ import annotations
import math, time

import cv2

from perception.gaze import draw_text, draw_arrow, draw_circle, draw_pose_skeleton
from perception.overlay import (draw_relation_ribbon,
                                C_YELLOW, C_CYAN, C_SKEL, C_MAGENTA, C_GREEN)
from perception.perceive import make_detector
from perception.relations import RelationEngine
from perception.watch_exec import WatchExecutor
from planning.spec_utils import _expand, _FilteredDetector, _focus_ok, spec_summary


class PlanView:
    """One frame in, an annotated frame plus any fired entries out.

    Built lazily by the caller: constructing it imports mediapipe and a detector
    checkpoint, which takes seconds and must not happen while a participant is
    waiting.
    """

    def __init__(self, detector="gdino", vocab=("person",), conf=0.3,
                 persist=2, cooldown=15.0, tau_gap=3.0, lean_deg=25.0):
        # live relevance state, updated by each plan. `allow` is the closed-YOLO
        # whitelist (synonym-expanded to match whatever labels the detector
        # emits); `want_classes` is the RAW planner nouns for an open-vocab
        # detector's set_vocab; classes_ver bumps so the detector re-prompts
        # lazily on its next frame rather than being rebuilt.
        self.relevance = {"allow": None, "focus": set(),
                          "want_classes": None, "classes_ver": 0}
        self.engine = RelationEngine(
            _FilteredDetector(make_detector(detector, list(vocab), conf=conf),
                              self.relevance),
            lean_deg=lean_deg)
        self._persist, self._cooldown, self._tau_gap = persist, cooldown, tau_gap

        self.spec = None
        self.executor = None
        self.context = ""
        self.transcript = ""          # what STT heard, shown so it can be checked
        self.truth, self.viz = {}, {"dets": [], "people": [], "rays": []}
        self.statuses = []
        self.suppressed = []          # labels the focus gate refused this frame
        self.plan_error = ""

    # ---------------------------------------------------------------- plan ---
    def apply_relevance(self, spec):
        """Push a plan's detect/focus into the live relevance state."""
        if not spec:
            self.relevance["allow"], self.relevance["focus"] = None, set()
            return
        raw = lambda ls: {str(w).strip().lower() for w in (ls or []) if str(w).strip()}
        self.relevance["allow"] = _expand(spec.get("detect")) or None
        self.relevance["focus"] = _expand(spec.get("focus"))
        # 'person' is pinned in unconditionally: an open-vocab model only finds
        # what it is prompted for, and people drive every social relation.
        self.relevance["want_classes"] = (raw(spec.get("detect"))
                                          | raw(spec.get("focus")) | {"person"})
        self.relevance["classes_ver"] += 1

    def set_plan(self, spec, context):
        """Install a compiled spec. A failed plan leaves the previous one running
        -- losing the current watch-spec because the next VLM call was flaky is a
        worse outcome than watching a slightly stale one."""
        if not spec:
            self.plan_error = "plan failed -- kept the previous spec"
            return False
        self.spec, self.context, self.plan_error = spec, context, ""
        self.apply_relevance(spec)
        self.executor = WatchExecutor(spec, persist=self._persist,
                                      cooldown=self._cooldown,
                                      tau_gap=self._tau_gap)
        print(f"[plan] context: {context}")
        for expr, label in spec_summary(spec):
            print(f"[plan]   watch {expr}   ({label})")
        if spec.get("missing"):
            print(f"[plan]   MISSING (vocabulary gap): {spec['missing']}")
        return True

    # --------------------------------------------------------------- frame ---
    def step(self, frame, t=None):
        """-> list of fired entries, AFTER the focus gate.

        Safe to call before any plan exists: the relations are still computed and
        drawn, there is just nothing watching them yet. That is deliberate -- the
        ribbon is how you check the CV is alive while composing a request.

        THE FOCUS GATE IS THE POINT OF THE RELEVANCE LAYER, so it runs here and
        not in the caller. The VLM enumerates what is in the frame (`seen`), tiers
        it into context (`detect`) and can-trigger (`focus`), and that tiering
        feeds the detector automatically -- the whitelist for closed YOLO, the
        set_vocab prompt for an open-vocab model. `_focus_ok` closes the loop at
        the other end: a gaze or point may only OPEN a story when what it lands on
        is a focus object. Without it, "someone looked at something" fires on every
        chair in the room, which is exactly the 0703 spam.
        """
        t = time.time() if t is None else t
        self.truth, self.viz = self.engine.step(frame, t)
        self.suppressed = []
        if self.executor is None:
            self.statuses = []
            return []
        raw, self.statuses = self.executor.step(self.truth, t)
        fired = []
        for e in raw:
            if _focus_ok(e, self.viz, self.relevance["focus"]):
                fired.append(e)
            else:
                self.suppressed.append(e.get("label", ""))
                print(f"[gate] {e.get('label')} suppressed "
                      f"(gaze/point not on a focus object)")
        return fired

    def entries(self):
        return self.executor.entries if self.executor is not None else []

    # -------------------------------------------------------------- overlay ---
    def draw(self, fr, header=""):
        """The live view, drawn in the same order and colours as before: object
        slots yellow, skeletons green, gaze rays blue (logic), pointing yellow,
        joint attention red, vocabulary ribbon along the bottom."""
        H, W = fr.shape[:2]
        # Boxes are coloured BY TIER, not uniformly: the tiering is a decision the
        # VLM made and the whole point is that it is inspectable. Yellow thick =
        # focus (may open a story), green = context (enriches only), grey thin =
        # seen and ignored. Same colours as attention_system._draw_dets.
        focus = self.relevance.get("focus") or set()
        allow = self.relevance.get("allow")
        for d in self.viz.get("dets", []):
            lab = str(d.label).lower()
            if lab in focus:
                col, th = C_YELLOW, 4
            elif (not allow) or lab in allow or lab == "person":
                col, th = C_GREEN, 2
            else:
                col, th = (150, 150, 150), 1
            x1, y1, x2, y2 = map(int, d.box)
            cv2.rectangle(fr, (x1, y1), (x2, y2), col, th, cv2.LINE_AA)
            draw_text(fr, f"{d.label} {float(getattr(d, 'score', 0)):.2f}",
                      (x1, max(12, y1 - 6)), col, 0.55, 2)
        for p in self.viz.get("people", []):
            if getattr(p, "raw", None):
                draw_pose_skeleton(fr, p.raw, C_SKEL, thick=9)
        for a in self.viz.get("arms", []):
            draw_arrow(fr, a.origin, a.point_at(0.4 * math.hypot(W, H)), C_YELLOW, 12)
        for r0 in self.viz.get("rays", []):
            draw_arrow(fr, r0.origin, r0.point_at(0.5 * math.hypot(W, H)), C_CYAN, 12)
        for c in self.viz.get("joint", []):
            draw_circle(fr, c["point"], 26, C_MAGENTA, 10)
        draw_relation_ribbon(fr, W, H, self.truth, self.entries())
        if header:
            draw_text(fr, header, (16, 30), C_GREEN, 0.6, 2)
        return fr

    # -------------------------------------------------------------- publish ---
    def publish(self, UI, jpg=None, states=None, collecting=None):
        """Fill THE PLAN panel. Same keys attention_system pushed, so the page's
        JS is untouched -- plus `transcript` and `states`, which are additions
        rather than replacements."""
        from webui import server as attention_ui
        s = self.spec or {}
        with UI.LOCK:
            if jpg is not None:
                UI.STATE["jpg"] = jpg
            UI.STATE["context"] = self.context
            UI.STATE["why"] = s.get("why", "") or self.plan_error
            UI.STATE["entries"] = spec_summary(self.spec) if self.spec else []
            UI.STATE["seen"] = list(s.get("seen") or [])
            UI.STATE["detect"] = list(s.get("detect") or [])
            UI.STATE["focus"] = list(s.get("focus") or [])
            UI.STATE["transcript"] = self.transcript
            UI.STATE["suppressed"] = list(self.suppressed)
            UI.STATE["status"] = attention_ui.build_status(
                self.statuses, self.entries(), self.truth)
            if states is not None:
                UI.STATE["states"] = states
            if collecting is not None:
                UI.STATE["collecting"] = collecting
