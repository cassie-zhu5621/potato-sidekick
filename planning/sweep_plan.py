#!/usr/bin/env python3
"""
sweep_plan.py — SWEEP-FIRST planning, driven by the S4 clip.

THE ORDER MATTERS, and it is the whole architecture:

  S4 turns the head across its stations and grabs ONE PURE FRAME per station.
  No detector, no skeleton, no ribbon -- the VLM must see the room, not our
  annotations. Drawing on a frame before the VLM reads it is feeding the model
  our own guesses and calling the result its judgement.

  The frames are sent as INDEPENDENT labelled images in ONE Gemini call. A grid
  is still written locally for the researcher, but is never sent to the model.
  Back comes the enumeration (`seen`), the tiering (`detect` = context,
  `focus` = may trigger), per-object boxes, and the watch-spec.

  Only then does CV start: `focus`/`detect` become the detector's vocabulary --
  set_vocab for YOLO-World, the synonym whitelist for closed YOLO -- and the
  relation engine runs at the angle that scored richest.

Ported from attention_system.sweep_and_plan; the grid mapping, tier colours,
coverage check and plan.json layout are the same, so the sweeps the web UI
already knows how to render keep rendering.
"""
from __future__ import annotations
import json, math, os, time

import cv2
import numpy as np

from perception.overlay import C_RED, C_GREEN
from perception.gaze import draw_text

CW, CH = 480, 360          # local researcher-grid cell size (not Gemini input)


class Sweep:
    """Collects one frame per station, then plans from all of them at once."""

    def __init__(self, feed_dir="feed"):
        self.feed_dir = feed_dir
        self.shots = []                 # [(pan_deg, frame)]
        self.active = False
        self.last = None                # meta dict of the most recent sweep

    # ---- during S4 -------------------------------------------------------- #
    def begin(self):
        self.shots, self.active = [], True

    def offer(self, frame, pan_deg, min_gap_deg=8.0):
        """Called on every SETTLED frame while S4 plays. One frame per station:
        stillness is what separates a station from the move into it, and a blurred
        frame would be attributed to an angle the head has already left."""
        if not self.active or frame is None:
            return False
        if any(abs(pan_deg - p) < min_gap_deg for p, _ in self.shots):
            return False
        self.shots.append((float(pan_deg), frame.copy()))
        print(f"[sweep] captured pan {pan_deg:+.0f}deg  ({len(self.shots)} frames)")
        return True

    # ---- at the end of S4 ------------------------------------------------- #
    def plan(self, context, plan_fn, ui=None):
        """-> (spec, meta) . `plan_fn(context, jpegs)` is planner.plan.

        Falls back to a single-frame plan when the sweep caught nothing, because
        a session must not be lost to an empty capture.
        """
        self.active = False
        grabbed = list(self.shots)
        if not grabbed:
            print("[sweep] no frames captured -- single-frame plan")
            return plan_fn(context, None), None

        N = len(grabbed)
        cols = max(1, int(math.ceil(math.sqrt(N))))
        rows = int(math.ceil(N / cols))
        grid = np.zeros((rows * CH, cols * CW, 3), dtype=np.uint8)
        cells = []
        for i, (pan, fr) in enumerate(grabbed):
            r, c = divmod(i, cols)
            grid[r * CH:r * CH + CH, c * CW:c * CW + CW] = cv2.resize(fr, (CW, CH))
            cells.append({"pan": pan, "fr": fr, "x0": c * CW, "y0": r * CH})
        jpegs = [cv2.imencode(".jpg", fr)[1].tobytes() for _, fr in grabbed]
        print(f"[sweep] one Gemini call with {N} independent pure frames")
        res = plan_fn(context, jpegs)
        spec = (res or {}).get("spec")
        if spec is None:
            return res, None

        # Gemini returns a view_index and coordinates normalised within that view.
        ts = time.strftime("%Y%m%d_%H%M%S")
        out = os.path.join(self.feed_dir, "sweeps", ts)
        os.makedirs(out, exist_ok=True)
        per = {i: [] for i in range(N)}
        for b in (spec.get("boxes") or []):
            try:
                x0, y0, x1, y1 = [float(v) for v in list(b.get("box", []))[:4]]
                i = int(b.get("view_index", -1))
            except Exception:
                continue
            if not (0 <= i < N):
                continue
            cell = cells[i]
            fh, fw = cell["fr"].shape[:2]
            lx0 = max(0.0, min(1.0, x0)) * fw
            ly0 = max(0.0, min(1.0, y0)) * fh
            lx1 = max(0.0, min(1.0, x1)) * fw
            ly1 = max(0.0, min(1.0, y1)) * fh
            if lx1 - lx0 >= 2 and ly1 - ly0 >= 2:
                per[i].append((str(b.get("label", "?")),
                               str(b.get("tier", "context")), [lx0, ly0, lx1, ly1]))

        def _draw(vis, boxes):
            for lab, tier, (x0, y0, x1, y1) in boxes:      # focus RED, context GREEN
                col, th = (C_RED, 4) if tier == "focus" else (C_GREEN, 2)
                cv2.rectangle(vis, (int(x0), int(y0)), (int(x1), int(y1)),
                              col, th, cv2.LINE_AA)
                draw_text(vis, lab, (int(x0), max(14, int(y0) - 6)), col, 0.6, 2)
            return vis

        gridvis = grid.copy()
        for i in range(N):
            cell = cells[i]
            fh, fw = cell["fr"].shape[:2]
            for lab, tier, (x0, y0, x1, y1) in per[i]:
                col = C_RED if tier == "focus" else C_GREEN
                cv2.rectangle(gridvis,
                              (int(cell["x0"] + x0 * CW / fw), int(cell["y0"] + y0 * CH / fh)),
                              (int(cell["x0"] + x1 * CW / fw), int(cell["y0"] + y1 * CH / fh)),
                              col, 2, cv2.LINE_AA)
                draw_text(gridvis, lab,
                          (int(cell["x0"] + x0 * CW / fw),
                           max(12, int(cell["y0"] + y0 * CH / fh) - 5)), col, 0.5, 2)
        cv2.imwrite(os.path.join(out, "panorama.jpg"), gridvis)

        best, shots = (-1, 0.0), []
        for i, (pan, fr) in enumerate(grabbed):
            vis = _draw(fr.copy(), per[i])
            fn = f"pan_{int(round(pan)):+04d}.jpg"
            cv2.imwrite(os.path.join(out, fn), vis)
            sc = sum(3 if tier == "focus" else 1 for _, tier, _ in per[i])
            if sc > best[0]:
                best = (sc, pan)
            print(f"[sweep] pan {pan:+.0f}deg: {len(per[i])} VLM boxes (score {sc})")
            shots.append({"pan": int(round(pan)), "file": fn,
                          "dets": [{"label": l, "tier": tr,
                                    "box": [round(v, 1) for v in bx]}
                                   for l, tr, bx in per[i]]})

        # coverage: a planned object the VLM never boxed anywhere is a phantom
        cover = {}
        for lbl in (spec.get("detect") or []):
            ll = str(lbl).lower()
            cover[lbl] = sum(1 for sh in shots for d in sh["dets"]
                             if str(d["label"]).lower() == ll)
        never = [o for o, n in cover.items() if n == 0]
        if never:
            print(f"[sweep] planned but boxed in NO angle: {never}")

        meta = {"time": ts, "context": context, "grid": f"{cols}x{rows}",
                "n_frames": N, "poses": [p for p, _ in grabbed],
                "seen": spec.get("seen"), "detect": spec.get("detect"),
                "focus": spec.get("focus"), "coverage": cover,
                "watch_spec": spec, "shots": shots, "panorama": "panorama.jpg",
                "richest_pan": int(round(best[1])), "richest_score": best[0]}
        with open(os.path.join(out, "plan.json"), "w") as f:
            json.dump(meta, f, indent=2)
        print(f"[sweep] {len(shots)} independent frames + local grid -> {out}   richest pan "
              f"{best[1]:+.0f}deg (score {best[0]})")
        self.last = meta
        self.shots = []
        return res, meta
