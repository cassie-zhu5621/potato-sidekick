"""
feed.py — the NOTICED feed: one confirmed story -> disk and browser.

The single writer. Everything that records a moment goes through here, so the
count on the robot's screen and the count on the page can never be counting
different events -- which is exactly what happened when the storyboard hung off
the detector while the screen hung off the state machine.

  <feed_dir>/frame_<id>.jpg      the full comic strip
  <feed_dir>/thumb_<id>.jpg      the card thumbnail
  <feed_dir>/attention_log.jsonl one line per record: note, story, truth, worth

Lifted out of attention_demo.py unchanged.
"""
from __future__ import annotations
import json, os

import cv2


def publish(fr, rec, args, UI):
    """One confirmed REPORT -> disk (--save) + web feed (--serve, in-memory thumbs)."""
    H, W = fr.shape[:2]
    if args.save:
        os.makedirs(args.feed_dir, exist_ok=True)
        cv2.imwrite(os.path.join(args.feed_dir, rec["frame"]), fr)
        cv2.imwrite(os.path.join(args.feed_dir, rec["thumb"]),
                    cv2.resize(fr, (192, max(1, int(192 * H / W)))))
        with open(os.path.join(args.feed_dir, "attention_log.jsonl"), "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    if UI is not None:
        tjpg = cv2.imencode(".jpg", cv2.resize(fr, (192, max(1, int(192 * H / W)))))[1].tobytes()
        with UI.LOCK:
            UI.STATE["thumbs"][rec["thumb"]] = tjpg
            for old in list(UI.STATE["thumbs"])[:-60]:
                UI.STATE["thumbs"].pop(old, None)
            # full-resolution frame (the comic strip) viewable in the browser, last ~20 in memory
            UI.STATE.setdefault("frames", {})[rec["frame"]] = cv2.imencode(".jpg", fr)[1].tobytes()
            for old in list(UI.STATE["frames"])[:-20]:
                UI.STATE["frames"].pop(old, None)
            UI.STATE["feed"].append(rec)
            UI.STATE["feed"] = UI.STATE["feed"][-60:]
