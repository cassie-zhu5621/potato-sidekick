"""
overlay.py — everything drawn on a frame, in one place.

Colours are shared with the web UI's
vocabulary chips on purpose: a relation that is yellow-green in the video must be
yellow-green in the panel, or the two views cannot be read together.
"""
from __future__ import annotations

import cv2
import numpy as np

# VOCAB is the 11-row relation table (docs/relation_table.md). shot_trace names
# rows from it, which is what keeps a story's caption in the same vocabulary the
# planner compiled against.
from planning.planner import VOCAB
from perception.gaze import (draw_text, draw_box, draw_arrow, draw_circle,
                             draw_panel, C_GREEN, C_RED, C_YELLOW, C_ORANGE,
                             C_MAGENTA, C_CYAN, C_WHITE)



C_SKEL = (90, 255, 30)

LIVE_ABBR = {1: "gaze", 2: "joint", 3: "eye", 4: "point", 5: "prox", 6: "F-form",
             7: "appr", 8: "lean", 9: "hands", 10: "group", 11: "turn"}

def draw_relation_ribbon(fr, W, H, truth, entries):
    """Bottom-of-frame vocabulary strip: rows 1–11 as 'N abbr', lit yellow-green when
    detected THIS frame; watch-entry groups (≥2 ids) bracketed above with an AND/OR/THEN
    label that lights blue when its condition holds. Detection-only — no watch/cooldown."""
    import cv2
    n = 11
    margin = 34
    step = (W - 2 * margin) / (n - 1)
    xs = {i: int(margin + (i - 1) * step) for i in range(1, n + 1)}
    base_y = H - 18                                    # the label row
    for i in range(1, n + 1):                          # 1) the vocabulary labels
        on = bool(truth.get(i, False))
        col = C_YELLOW if on else (165, 165, 165)
        txt = f"{i} {LIVE_ABBR.get(i, '')}"
        (tw, _), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        draw_text(fr, txt, (xs[i] - tw // 2, base_y), col, 0.5, 2 if on else 1)

    level = [0]                                        # 2) the conjunction brackets, stacked up
    def connect(ids, label, lit):
        ids = [i for i in ids if i in xs]
        if len(ids) < 2:
            return
        y = base_y - 34 - level[0] * 28
        lo = min(ids, key=lambda i: xs[i]); hi = max(ids, key=lambda i: xs[i])
        col = C_CYAN if lit else (110, 110, 110)
        th = 3 if lit else 1
        cv2.line(fr, (xs[lo], y), (xs[hi], y), col, th, cv2.LINE_AA)        # the bar
        for i in ids:                                                       # down-ticks to each
            cv2.line(fr, (xs[i], y), (xs[i], base_y - 14), col, th, cv2.LINE_AA)
        mx = (xs[lo] + xs[hi]) // 2
        (tw, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        draw_text(fr, label, (mx - tw // 2, y - 7), col, 0.6, 2)
        level[0] += 1

    for e in entries:
        on = lambda i: bool(truth.get(i, False))
        if len(e.get("then", [])) >= 2:
            connect(e["then"], "THEN", all(on(i) for i in e["then"]))
        if len(e.get("all", [])) >= 2:
            connect(e["all"], "AND", all(on(i) for i in e["all"]))
        if len(e.get("any", [])) >= 2:
            connect(e["any"], "OR", any(on(i) for i in e["any"]))

def make_strip(shots, height=None):
    """The comic strip: N shots -> one horizontal story image.

    `height=None` keeps the camera's own resolution. It used to default to 300,
    which threw away four fifths of every panel on the way to disk: the shots
    arrive at 1280x720 and were saved 533x300. Nothing needed them small -- the
    card thumbnail is resized separately in `feed.publish`, and the browser
    scales with CSS -- so the loss showed up only later, when someone tried to
    enlarge a panel during the review phase and found there was nothing behind
    the pixels.

    A 5-panel strip is about 400 KB at native size against 90 KB before. The
    in-memory cache in `feed.publish` holds twenty of them, so roughly 8 MB.
    """
    if height is None:
        height = max(s.shape[0] for s in shots)
    tiles = []
    for s in shots:
        h, w = s.shape[:2]
        tiles.append(s if h == height
                     else cv2.resize(s, (max(1, int(w * height / h)), height)))
    return np.hstack(tiles)

def shot_trace(truth, viz):
    """One panel's GROUNDED detection summary (what the system actually saw this frame), so the
    story description is narrated from the real detected sequence — not re-captioned off a photo."""
    dets = viz.get("dets", [])
    parts, seen = [], set()
    for relname, h in viz.get("hits", []):              # gazing-at / pointing-at (have a target)
        di = h.get("det") if isinstance(h, dict) else None
        tgt = dets[di].label if isinstance(di, int) and 0 <= di < len(dets) else ""
        key = f"{relname} {tgt}".strip()
        if key not in seen:
            seen.add(key); parts.append(key)
    hit_names = {relname for relname, _ in viz.get("hits", [])}
    for i, on in truth.items():                         # truth-only rows (proxemic, approach, hands-on…)
        if on:
            nm = VOCAB.get(i, str(i)).split("—")[0].strip()
            if nm not in hit_names and nm not in seen:
                seen.add(nm); parts.append(nm)
    return ", ".join(parts) if parts else "quiet"