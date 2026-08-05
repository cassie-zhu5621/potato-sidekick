#!/usr/bin/env python3
"""Replay recorded judge calls against the CURRENT prompt and schema.

    python3 robot/tools/judge_ab.py                # the 6 slowest on record
    python3 robot/tools/judge_ab.py --all
    python3 robot/tools/judge_ab.py --slowest 3 --provider anthropic

WHY REPLAY. Every judge call already wrote its five JPEGs, its card entries and
its latency next to each other under `session_feed/*/llm/judge_group_*`. Sending
those exact frames and those exact cards again changes ONE thing -- the prompt
and schema in this checkout -- so a latency difference is attributable.

This exists because a judge ran 40-278 s against a planner at 5-17 s on the SAME
model, the SAME five 1280x720 frames, a LONGER prompt and a DEEPER schema. Model,
image tokens, prompt size, schema depth, thinking_level and connection
contention were each ruled out by inspection or by data. What was left was what
the judge was being ASKED to do, so that is what changed; this measures whether
that was it.

It spends real API calls. Nothing else about the run is touched.
"""
import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


def recorded():
    """-> [{dir, latency, entries, images, request, old_note, old_confirmed}]"""
    out = []
    for d in sorted(glob.glob("session_feed/*/llm/judge_group_*")):
        rp = os.path.join(d, "result.json")
        if not os.path.exists(rp):
            continue
        try:
            r = json.load(open(rp))
        except Exception:
            continue
        imgs = [open(p, "rb").read()
                for p in sorted(glob.glob(os.path.join(d, "frame_*.jpg")))]
        if len(imgs) != 5:
            continue
        res = r.get("result") or {}
        # The request was not recorded on the judge side; take it from the same
        # run's planner audit, which is the request those frames were watched
        # under. Approximate but honest -- and it only affects the prompt's
        # framing, not what is being compared.
        run = d.split("/")[1]
        req = ""
        for pp in sorted(glob.glob(f"session_feed/{run}/llm/planner_*/result.json")):
            try:
                req = json.load(open(pp)).get("request") or req
            except Exception:
                pass
        out.append({
            "dir": d, "latency": r.get("latency_s") or 0.0,
            "entries": r.get("entries") or [], "images": imgs, "request": req,
            "claims": r.get("claims") or [],
            "old_note": (res.get("note") or "")[:70],
            "old_sel": res.get("selected_index"),
            "provider": r.get("provider", "?"),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slowest", type=int, default=6)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--provider", default=None,
                    choices=["gemini", "anthropic"],
                    help="default: whatever .env selects")
    a = ap.parse_args()

    if a.provider:
        os.environ["NOTICEBOT_LLM_PROVIDER"] = a.provider
    from planning.provider import provider_name
    from planning.judge import judge_candidate_group, ReportabilityTaste

    rows = recorded()
    if not rows:
        sys.exit("no judge_group_* audits with five frames under session_feed/")
    rows.sort(key=lambda r: -r["latency"])
    picked = rows if a.all else rows[:a.slowest]

    print(f"replaying {len(picked)} of {len(rows)} recorded judge calls "
          f"through {provider_name()}\n")
    print(f"  {'recorded':>9}  {'now':>9}  {'x':>6}  cards  pass  describe")
    taste = ReportabilityTaste()
    olds, news = [], []
    for r in picked:
        try:
            t0 = time.time()
            out = judge_candidate_group(r["images"], r["entries"], taste,
                                        request=r["request"])
            secs = time.time() - t0
        except Exception as e:
            print(f"  {r['latency']:8.1f}s  {'--':>9}  {'--':>6}  "
                  f"{type(e).__name__}: {str(e)[:50]}")
            continue
        olds.append(r["latency"])
        news.append(secs)
        speed = r["latency"] / secs if secs else 0
        print(f"  {r['latency']:8.1f}s  {secs:8.1f}s  {speed:5.1f}x  "
              f"{len(r['entries']):5}  {str(out['confirmed']):5} "
              f"{out['note'][:52]}")
        print(f"  {'':>9}  {'':>9}  {'':>6}         was:  "
              f"sel={r['old_sel']} {r['old_note'][:48]}")

    if olds:
        import statistics as st
        print(f"\n  median  recorded {st.median(olds):6.1f}s   "
              f"now {st.median(news):6.1f}s   "
              f"{st.median(olds)/max(st.median(news), 1e-9):.1f}x")
        print("  Latency is the point, but read the describe column too: a fast "
              "judge\n  that has stopped saying anything useful is not an "
              "improvement.")


if __name__ == "__main__":
    main()
