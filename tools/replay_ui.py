#!/usr/bin/env python3
"""Re-open the live web UI on a session that has already finished.

WHY THIS EXISTS. The page is the figure -- THE PLAN panel lit as its relations
become true is the one surface that shows what this system is actually for --
and it only exists while the loop is running. A session that nobody thought to
screenshot leaves no picture of itself, and there is no honest way to draw one
afterwards.

There is an honest way to REBUILD one. Everything the page shows is on disk:
the compiled watch-spec and the relevance tiering in llm/planner_*/result.json,
the verdicts in llm/judge_group_*/result.json, the reports in
attention_log.jsonl, the frames and thumbnails beside them. This loads that
record into the real server, serves the real page, and lets the browser render
it. Nothing here draws a mock-up: if a value was not recorded, the panel is
empty, exactly as it would have been.

    python3 tools/replay_ui.py                       # the most recent session
    python3 tools/replay_ui.py e2e_20260817_180914   # a named one
    python3 tools/replay_ui.py --port 8010

Then open the URL it prints and screenshot it.

FOR A PAPER FIGURE, say what it is. This is a faithful reconstruction from the
run's own artefacts, not a screen capture taken during the session. The two are
not the same claim, and the difference is cheap to state: "rebuilt from the
session log" in the caption costs nothing and is true.

WHAT IS NOT RECOVERABLE. The live camera image is whatever frame the planner
last saw, with the planner's OWN recorded boxes drawn on it -- not the per-frame
CV overlay (skeletons, gaze rays, the relation ribbon), which was never saved.
The relation ribbon along the bottom and the pose overlays will be absent. The
entries' satisfied/cooling state is whatever the recorded truth vector says at
the moment of the report being replayed, not a live clock.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FEED = ROOT / "session_feed"

TIER_BGR = {"focus": (58, 227, 207), "context": (218, 165, 32), "seen": (125, 125, 120)}


def _latest_session() -> Path | None:
    runs = [d for d in FEED.glob("e2e_*") if d.is_dir()]
    return max(runs, key=os.path.getmtime) if runs else None


def _newest(dirpath: Path, pattern: str):
    hits = sorted(dirpath.glob(pattern), key=os.path.getmtime, reverse=True)
    return hits[0] if hits else None


def _load_json(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _planner(session: Path):
    """(spec, request, frames_dir) from the last planner call of the run."""
    llm = session / "llm"
    if not llm.is_dir():
        return None, "", None
    d = _newest(llm, "planner_*")
    if d is None:
        return None, "", None
    rec = _load_json(d / "result.json") or {}
    resp = rec.get("response")
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except Exception:
            resp = {}
    resp = resp or {}
    req = rec.get("request")
    if not isinstance(req, str):
        req = ""
    return resp.get("spec"), req, d


def _transcript(session: Path) -> str:
    """What the participant actually asked. The planner's `request` is the whole
    assembled prompt; the sentence itself is stamped on every report."""
    log = session / "attention_log.jsonl"
    if not log.exists():
        return ""
    for line in reversed(log.read_text().strip().splitlines()):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("request"):
            return str(r["request"])
    return ""


def _judgments(session: Path) -> dict:
    """label -> the verdict panel, from the judge's own audit records."""
    llm = session / "llm"
    out = {}
    if not llm.is_dir():
        return out
    for d in sorted(llm.glob("judge_group_*"), key=os.path.getmtime):
        rec = _load_json(d / "result.json") or {}
        res = rec.get("result") or {}
        for entry, cand in zip(rec.get("entries") or [],
                               res.get("candidate_results") or []):
            label = entry.get("label")
            if not label:
                continue
            out[label] = {
                "status": "confirmed" if cand.get("confirmed") else "rejected",
                "note": cand.get("reason") or res.get("note") or "",
                "generation": rec.get("generation", 0),
            }
    return out


def _live_frame(pdir: Path | None, spec: dict | None):
    """The last frame the planner saw, with the boxes IT recorded drawn on it.

    Not the live CV overlay -- that was never saved. These boxes are the VLM's
    own output, which is the thing the relevance panel is about, so drawing them
    is a rendering of recorded data rather than an invention.
    """
    if pdir is None:
        return None
    import cv2
    import numpy as np
    shots = sorted(pdir.glob("frame_*.jpg"))
    if not shots:
        return None
    boxes = [b for b in ((spec or {}).get("boxes") or []) if isinstance(b, dict)]
    # Prefer the view that actually has focus objects on it -- that is the frame
    # the robot ended up watching, and the one worth showing.
    want = 0
    for b in boxes:
        if b.get("tier") == "focus":
            want = int(b.get("view_index") or 0)
            break
    img = cv2.imread(str(shots[min(want, len(shots) - 1)]))
    if img is None:
        return None
    h, w = img.shape[:2]
    for b in boxes:
        if int(b.get("view_index") or 0) != want:
            continue
        xy = b.get("box") or []
        if len(xy) != 4:
            continue
        x0, y0, x1, y1 = [float(v) for v in xy]
        p0 = (int(x0 * w), int(y0 * h))
        p1 = (int(x1 * w), int(y1 * h))
        col = TIER_BGR.get(str(b.get("tier")), TIER_BGR["seen"])
        cv2.rectangle(img, p0, p1, col, 3 if b.get("tier") == "focus" else 2)
        cv2.putText(img, f"{b.get('label','?')} [{b.get('tier','?')}]",
                    (p0[0] + 4, max(18, p0[1] - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2, cv2.LINE_AA)
    cv2.putText(img, "REPLAY -- rebuilt from the session log, not a live capture",
                (12, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255),
                2, cv2.LINE_AA)
    return cv2.imencode(".jpg", img)[1].tobytes()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session", nargs="?", default=None,
                    help="a directory name under session_feed/ (default: newest)")
    ap.add_argument("--port", type=int, default=8010,
                    help="deliberately not 8000: the live loop may be running")
    ap.add_argument("--no-boxes", action="store_true",
                    help="show the raw frame without the planner's boxes")
    a = ap.parse_args(argv)

    session = (FEED / a.session) if a.session else _latest_session()
    if session is None or not session.is_dir():
        print(f"no such session: {session}")
        return 1

    import webui.server as W
    from perception.watch_exec import WatchExecutor
    import webui.server as attention_ui           # build_status lives here

    spec, _req, pdir = _planner(session)
    if spec is None:
        print(f"{session.name} has no planner record -- nothing to show in THE PLAN")

    W.ARGS = argparse.Namespace(feed_dir=str(session), web_port=a.port)

    with W.LOCK:
        S = W.STATE
        S["context"] = _transcript(session)
        S["transcript"] = S["context"]
        S["why"] = (spec or {}).get("why") or ""
        S["seen"] = list((spec or {}).get("seen") or [])
        boxes = [b for b in ((spec or {}).get("boxes") or []) if isinstance(b, dict)]
        S["detect"] = sorted({str(b.get("label")) for b in boxes
                              if b.get("tier") == "context"})
        S["focus"] = sorted({str(b.get("label")) for b in boxes
                             if b.get("tier") == "focus"})
        S["judgments"] = _judgments(session)

        if spec:
            # THE REAL EXECUTOR, not a hand-built row. The panel's operator
            # chips, the AND/THEN grouping and the `on` binding all come out of
            # the same code the live page reads, so a replay cannot quietly
            # disagree with the thing it is a picture of.
            ex = WatchExecutor(spec, persist=1)
            truth = {}
            log = session / "attention_log.jsonl"
            if log.exists():
                for line in reversed(log.read_text().strip().splitlines()):
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("truth"):
                        truth = {int(k): bool(v) for k, v in rec["truth"].items()}
                        break
            _fired, statuses = ex.step(truth, 0.0)
            # The expression string the panel prints ("9  (touching the
            # plant)") comes from the planner's own formatter, not from a
            # rebuild here -- one definition, one rendering.
            from planning.spec_utils import spec_summary
            S["entries"] = spec_summary(spec)
            S["status"] = attention_ui.build_status(statuses, ex.entries, truth)

        if not a.no_boxes:
            S["jpg"] = _live_frame(pdir, spec)

        try:
            import robot.states as ST
            S["states"] = [{"name": n, "screen": d.get("screen", ""),
                            "note": d.get("note", "")}
                           for n, d in ST.STATES.items()]
        except Exception:
            pass

    n_reports = 0
    log = session / "attention_log.jsonl"
    if log.exists():
        n_reports = len([l for l in log.read_text().splitlines() if l.strip()])

    W.serve(argparse.Namespace(web_port=a.port, feed_dir=str(session)))
    print(f"\nreplaying {session.name}")
    print(f"  request : {S['context'][:70] or '(none recorded)'}")
    print(f"  watching: {len(S['status'])} entr(y/ies)   focus: {S['focus'] or '-'}")
    print(f"  reports : {n_reports}")
    print(f"\n  http://localhost:{a.port}\n")
    print("  Screenshot it from the browser. The frame is labelled REPLAY on")
    print("  purpose -- if that is in the way, pass --no-boxes for a clean one.")
    print("  Ctrl-C to stop.")
    try:
        import time
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
