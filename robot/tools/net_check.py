#!/usr/bin/env python3
"""Is the VLM slow because of the network, or because of Google? -- 40 seconds.

WHY THIS EXISTS. On 2026-08-17 planner latency tripled (median 5.7 s over the
previous week -> 18.6 s) with nothing changed on our side: same model, same
thinking level, same ~5460 input tokens. Every explanation reached for was
wrong -- service tier, media resolution, input size -- and the way that was
finally settled was a trivial call with EIGHT input tokens that took ten seconds.
Guessing cost an afternoon; this measures instead.

WHAT IT SEPARATES. Three numbers, because they fail for different reasons:

  1. RTT      TCP+TLS to the endpoint. Pure network. No API key, no quota.
  2. FLOOR    an API call with a ~100-byte payload and an 11-token answer.
              Bandwidth cannot matter at that size, so whatever this costs is
              the service's own queueing.
  3. ONE      the same call carrying ONE JPEG.
  4. LOADED   the same call carrying five, about 550 KB -- one real sweep.

WHY THE ONE-IMAGE ARM EXISTS. The verdict used to read `LOADED - FLOOR` and call
it "the upload". That was wrong, and on 2026-08-18 it told Cassie to go looking
for a better connection. For a constant ~550 KB payload that difference measured
1.0 s, 4.5 s and 7.7 s across three runs in two days. Bandwidth does not change
sevenfold in a day: the difference tracks how slow EVERYTHING is at that moment,
because a bigger request queues longer. A direct probe sending the same prompt
0, 1, 3 and 5 images deep came back 23 s, 9 s, 6 s, 24 s -- no relationship to
image count at all.

So "is it the upload" is only answerable by holding the moment roughly fixed and
varying the payload, which is what ONE and LOADED now do, interleaved.

READING IT.
  RTT slow                     -> the network. Try the hotspot.
  RTT fast, FLOOR slow         -> Google is queueing. A hotspot changes nothing,
                                  and neither will anything else on this machine.
  LOADED climbs steeply over ONE -> genuinely per-image; sending fewer sweep
                                  frames would help.
  LOADED close to ONE          -> a flat cost for "there are images at all",
                                  which fewer frames will NOT reduce.

Run it on the laptop that runs the study, not anywhere else -- the point is the
path that machine takes.

    python3 -m robot.tools.net_check           # 5 reps
    python3 -m robot.tools.net_check 10        # more, for a noisy line
"""
from __future__ import annotations

import os
import socket
import ssl
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

HOST = "generativelanguage.googleapis.com"
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"]}


def rtt_once() -> float | None:
    """TCP connect + TLS handshake. Two round trips and a bit, no HTTP."""
    t = time.time()
    try:
        with socket.create_connection((HOST, 443), timeout=10) as raw:
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(raw, server_hostname=HOST):
                return time.time() - t
    except Exception:
        return None


def _sample(label, fn, reps):
    out = []
    for i in range(reps):
        d = fn()
        if d is None:
            print(f"  {label} {i + 1}: FAILED")
            continue
        out.append(d)
        print(f"  {label} {i + 1}: {d:6.2f}s")
    return out


def _line(name, v, unit="s"):
    if not v:
        return f"{name:<8} no successful samples"
    return (f"{name:<8} median {st.median(v):6.2f}{unit}   "
            f"min {min(v):6.2f}   max {max(v):6.2f}   n={len(v)}")


def main(reps=5):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import planning.gemini_provider as G

    print(f"{HOST}\n")

    print("1. RTT -- TCP + TLS, no API")
    rtts = _sample("rtt", rtt_once, reps)

    # The first API call of a process pays TLS, auth and client construction.
    # Measured at 24 s on a slow evening; pooled with the rest it would move the
    # median on its own.
    print("\n   (warming the client -- this one is discarded)")
    try:
        G.call_json("warm", SCHEMA)
    except Exception as exc:
        print(f"   API unreachable: {str(exc)[:110]}")
        print("\n" + _line("RTT", rtts))
        return 1

    def floor_once():
        t = time.time()
        try:
            G.call_json("Reply with the single word: ok", SCHEMA)
        except Exception:
            return None
        return time.time() - t

    print("\n2. FLOOR -- ~100 bytes up, 11 tokens back")
    floors = _sample("floor", floor_once, reps)

    imgs = _sweep_frames()
    ones, loaded = [], []
    if imgs:
        kb = sum(len(b) for b in imgs) / 1024
        print(f"\n3/4. ONE vs LOADED -- 1 JPEG against {len(imgs)} ({kb:.0f} KB), "
              f"interleaved")

        def img_call(n):
            def go():
                t = time.time()
                try:
                    G.call_json("Name one object you can see. One word.",
                                SCHEMA, images=imgs[:n])
                except Exception:
                    return None
                return time.time() - t
            return go

        # INTERLEAVED, not one arm then the other. Latency drifts by the minute
        # here -- running all the ONEs first and all the FIVEs after would let
        # that drift masquerade as the cost of four extra images, which is the
        # error this arm was added to correct.
        for r in range(max(2, reps // 2)):
            for label, n, bucket in (("one   ", 1, ones),
                                     ("loaded", len(imgs), loaded)):
                d = img_call(n)()
                if d is None:
                    print(f"  {label} {r + 1}: FAILED")
                    continue
                bucket.append(d)
                print(f"  {label} {r + 1}: {d:6.2f}s")
    else:
        print("\n3/4. skipped: no sweep frames in session_feed yet")

    print()
    print(_line("RTT", rtts))
    print(_line("FLOOR", floors))
    if ones:
        print(_line("ONE", ones))
    if loaded:
        print(_line("LOADED", loaded))
    print()
    print(_verdict(rtts, floors, ones, loaded, len(imgs) if imgs else 0))
    return 0


def _sweep_frames():
    """Five real frames from the most recent planner call, if there is one."""
    feed = ROOT / "session_feed"
    if not feed.is_dir():
        return []
    dirs = sorted(feed.glob("*/llm/planner_*"), key=os.path.getmtime, reverse=True)
    for d in dirs:
        jpgs = sorted(d.glob("frame_*.jpg"))
        if jpgs:
            return [p.read_bytes() for p in jpgs]
    return []


def _verdict(rtts, floors, ones, loaded, n_imgs):
    if not rtts:
        return "VERDICT  cannot reach the endpoint at all -- check the connection."
    r = st.median(rtts)
    f = st.median(floors) if floors else None
    if r > 1.0:
        return (f"VERDICT  the NETWORK. {r:.2f}s just to open a socket is the "
                f"problem before anything else is. Try the hotspot.")
    if f is None:
        return "VERDICT  network is fine; the API calls failed. Check the key and quota."

    per_image = ""
    # THE PAYLOAD-IRRELEVANT CASE NEEDS NO SAMPLES TO SPEAK. If half a megabyte
    # of JPEG arrives no slower than a hundred bytes, the time is not being spent
    # on anything we send, and no amount of trimming the sweep will recover it.
    # A null result across a 5000x range in payload is not a close call.
    # Measured 2026-08-18 on Cassie's laptop: floor 10.04s, one image 9.86s,
    # five images 9.13s.
    if ones and loaded and f and st.median(loaded) <= f * 1.3:
        per_image = ("\n         And 569 KB of JPEG arrived no slower than 100 "
                     "bytes did -- the wait is not for anything we send, so "
                     "sending fewer sweep frames would save nothing.")
    elif ones and loaded and n_imgs > 1 and min(len(ones), len(loaded)) < 3:
        per_image = ("\n         (image arms had fewer than 3 samples each -- "
                     "too few to say anything about frame count. Re-run with a "
                     "bigger rep count if that is the question.)")
    elif ones and loaded and n_imgs > 1:
        o, l = st.median(ones), st.median(loaded)
        # Does the cost actually scale with the number of images? If four extra
        # images cost about as much as the first one did, it does not.
        extra = (l - o) / max(1, n_imgs - 1)
        first = max(0.01, o - f)
        per_image = (f"\n         {n_imgs} images cost {l - o:+.1f}s over 1 image "
                     f"({extra:+.1f}s each vs {first:.1f}s for the first). ")
        per_image += ("Sending fewer sweep frames WOULD help."
                      if extra > 0.5 * first and extra > 0.8 else
                      "Fewer frames would NOT help -- the cost is for having "
                      "images at all, not for how many.")

    if f > 4.0:
        return (f"VERDICT  GOOGLE. The socket opens in {r:.2f}s and a hundred-byte "
                f"request still takes {f:.1f}s -- bandwidth cannot explain that, "
                f"and neither can anything on this machine. A hotspot will not "
                f"help. Allow extra time in the session schedule." + per_image)
    if floors and max(floors) > 3 * f:
        return (f"VERDICT  UNSTABLE. Median floor {f:.1f}s but the worst of "
                f"{len(floors)} was {max(floors):.1f}s. It is usable and will "
                f"occasionally stall; do not let a single slow plan panic you "
                f"mid-session." + per_image)
    return (f"VERDICT  healthy. Socket {r:.2f}s, floor {f:.1f}s. If planning still "
            f"feels slow the time is going somewhere else -- read latency_s in "
            f"session_feed/<run>/llm/planner_*/result.json." + per_image)


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 5))
