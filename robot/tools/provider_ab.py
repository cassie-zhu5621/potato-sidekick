#!/usr/bin/env python3
"""Replay a saved sweep through a provider, and compare the two.

    python3 robot/tools/provider_ab.py                  # newest audited sweep
    python3 robot/tools/provider_ab.py --provider both  # run each, side by side
    python3 robot/tools/provider_ab.py --dir session_feed/e2e_.../llm/planner_...

WHY REPLAY RATHER THAN RUN THE ROBOT. Every planner call already writes its five
JPEGs and its result next to each other under `session_feed/*/llm/`. Those frames
are a real room, at real stations, with the real request -- so the same five
images can be sent to a second provider and the two answers compared directly.
No servos, no camera, no participant, and the comparison is against something
that actually happened rather than a synthetic prompt.

WHAT TO LOOK AT, in order of how much it matters:

  1. latency          the reason for looking at a second provider at all
  2. violations       an empty list means the spec passed the SAME validator
  3. watch entries    the actual output: what it decided to watch for
  4. boxes            most likely to differ. The coordinate convention is
                      described in the prompt, not enforced by the schema, so a
                      model that reads it differently produces plausible numbers
                      that are wrong -- exactly the kind of failure that survives
                      a green test suite
"""
import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


def newest_planner_dir():
    hits = sorted(glob.glob("session_feed/*/llm/planner_*"))
    return hits[-1] if hits else None


def load(d):
    frames = []
    for i in range(16):
        p = os.path.join(d, f"frame_{i}.jpg")
        if not os.path.exists(p):
            break
        frames.append(open(p, "rb").read())
    rec = {}
    rp = os.path.join(d, "result.json")
    if os.path.exists(rp):
        rec = json.load(open(rp))
    return frames, rec


def summarise(tag, spec, violations, secs, raw_len):
    print(f"\n--- {tag} ---")
    print(f"  latency      {secs:.1f}s")
    print(f"  violations   {violations or '[] (passed the validator)'}")
    if not spec:
        print("  spec         None")
        return
    watch = spec.get("watch") or []
    print(f"  watch        {len(watch)} entr{'y' if len(watch)==1 else 'ies'}")
    for w in watch[:4]:
        print(f"                 {w.get('label')!r} on={w.get('on')} "
              f"all={w.get('all')} within={w.get('within_s')}s")
    print(f"  seen         {', '.join((spec.get('seen') or [])[:8])}")
    print(f"  focus        {', '.join(spec.get('focus') or [])}")
    boxes = spec.get("boxes") or []
    print(f"  boxes        {len(boxes)}   (ALL of them -- see the score below)")
    for b in sorted(boxes, key=lambda b: (b.get("view_index", 99),
                                          b.get("tier") != "focus")):
        box = b.get("box") or []
        # Printed with 2 decimals on purpose: a model that has misread the
        # convention tends to return values that are individually plausible and
        # collectively impossible -- a width of 9e-05, a box outside 0..1.
        pretty = "[" + ", ".join(f"{v:.2f}" for v in box) + "]"
        flag = "" if len(box) == 4 and all(0.0 <= v <= 1.0 for v in box) \
               else "   <-- OUTSIDE 0..1"
        print(f"                 view {b.get('view_index')}  "
              f"{str(b.get('tier')):<7} {str(b.get('label'))[:22]:<22} "
              f"{pretty}{flag}")

    # THE NUMBER THAT MOVES THE ROBOT.
    #
    # sweep_plan.py scores each station by its boxes -- focus 3, context 1 --
    # and turns to the winner. So the aim is decided by HOW MANY boxes land on
    # each view and WHAT TIER they are; the coordinates only affect the drawn
    # overlay. A spurious context box is therefore not cosmetic: it is a vote.
    # Two providers can agree on every object in the room and still aim the
    # robot at different walls.
    score: dict[int, int] = {}
    for b in boxes:
        i = b.get("view_index")
        if isinstance(i, int):
            score[i] = score.get(i, 0) + (3 if b.get("tier") == "focus" else 1)
    if score:
        best = max(score, key=lambda i: score[i])
        bar = "  ".join(f"v{i}:{score.get(i, 0)}" for i in range(max(score) + 1))
        print(f"  station      {bar}")
        print(f"  RICHEST      view {best}  <- the pan the robot turns to")

    # A label in `detect` that was never boxed anywhere is a phantom: the
    # detector is told to look for it and the planner never saw one.
    boxed = {str(b.get("label", "")).lower() for b in boxes}
    never = [d for d in (spec.get("detect") or []) if str(d).lower() not in boxed]
    if never:
        print(f"  phantom      in detect, boxed nowhere: {never}")
    print(f"  raw          {raw_len} chars")


def run(provider, request, frames):
    os.environ["NOTICEBOT_LLM_PROVIDER"] = provider
    from planning import provider as prov
    prov._CACHE.clear()
    for m in ("planning.planner",):
        sys.modules.pop(m, None)
    from planning.planner import plan
    t0 = time.time()
    r = plan(request, frames)
    return r, time.time() - t0


def check(names):
    """Is the key there, and does the model answer? Run before a session.

    `warm()` deliberately never raises -- a failed warm-up must not be a failed
    session. The cost of that is a missing or expired key staying quiet until the
    first real call, which happens with a participant already in the room. This
    is the same check, made loud, at a time when being wrong is free.
    """
    import time as _t
    bad = 0
    for name in names:
        os.environ["NOTICEBOT_LLM_PROVIDER"] = name
        from planning import provider as prov
        prov._CACHE.clear()
        prov._ENV_LOADED = False
        key = {"anthropic": "ANTHROPIC_API_KEY",
               "gemini": "GEMINI_API_KEY"}[name]
        prov.provider_name()                      # loads .env
        present = bool(os.environ.get(key, "").strip())
        print(f"\n{name}")
        print(f"  {key:<20} {'set' if present else 'MISSING'}")
        if not present:
            print(f"  -> put it in .env, or leave this provider unselected")
            bad += 1
            continue
        try:
            print(f"  model                {prov.model_name()}")
            t0 = _t.time()
            prov._impl().call_json(
                "Call the tool with ok set to true.",
                {"type": "object", "properties": {"ok": {"type": "boolean"}},
                 "required": ["ok"]}, max_output_tokens=64)
            print(f"  answered in          {_t.time() - t0:.1f}s   OK")
        except Exception as e:
            print(f"  FAILED               {type(e).__name__}: "
                  f"{str(e)[:160]}")
            bad += 1
    print(f"\n{'all good' if not bad else f'{bad} provider(s) not usable'}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="an audited planner_* directory")
    ap.add_argument("--provider", default="anthropic",
                    choices=["anthropic", "gemini", "both"])
    ap.add_argument("--check", action="store_true",
                    help="key present + model answers, no sweep needed")
    ap.add_argument("--media-res", nargs="*", metavar="RES",
                    help="A/B the Gemini image resolution on ONE recorded sweep: "
                         "same five frames, same prompt, only this changes. "
                         "Bare flag tries: unset low medium high")
    a = ap.parse_args()

    if a.check:
        names = ["gemini", "anthropic"] if a.provider == "both" else [a.provider]
        sys.exit(1 if check(names) else 0)

    d = a.dir or newest_planner_dir()
    if not d:
        sys.exit("no audited planner call found under session_feed/*/llm/ -- "
                 "run the loop once first, even if the plan failed")
    frames, rec = load(d)
    request = rec.get("request") or "watch for anything interesting"
    print(f"replaying {d}")
    print(f"  request      {request!r}")
    print(f"  frames       {len(frames)}")

    # The recorded answer IS the baseline. Printing it through the same
    # formatter costs nothing and spends no quota -- re-running Gemini to
    # compare would pay 60s for a number already on disk.
    r = rec.get("response") or {}
    if r:
        summarise(f"gemini  (on disk, {rec.get('generation')}/{rec.get('attempt')})",
                  r.get("spec"), r.get("violations"), rec.get("latency_s") or 0.0,
                  len(r.get("raw") or ""))

    if a.media_res is not None:
        # ONE VARIABLE AT A TIME. The 114 s judge call on 2026-08-05 was blamed
        # in turn on the service tier, the token budget and the prompt length;
        # none of those could be ruled out because every run also differed in
        # its images and its request. Replaying one recorded sweep fixes both,
        # so whatever moves is the resolution.
        #
        # "unset" means the field is not sent at all and the service picks --
        # which is what the code now does by default, and is NOT the same as
        # "low". That distinction is the thing being measured.
        levels = a.media_res or ["unset", "low", "medium", "high"]
        os.environ["NOTICEBOT_LLM_PROVIDER"] = "gemini"
        print(f"\nsame {len(frames)} frames, same prompt, gemini only\n")
        print(f"  {'media_resolution':18} {'latency':>9}  {'raw':>6}  outcome")
        for lv in levels:
            if lv in ("unset", "none", "-"):
                os.environ.pop("NOTICEBOT_GEMINI_MEDIA_RESOLUTION", None)
            else:
                os.environ["NOTICEBOT_GEMINI_MEDIA_RESOLUTION"] = lv
            try:
                r, secs = run("gemini", request, frames)
                spec, viol = r.get("spec"), r.get("violations")
                ok = "OK" if (spec is not None and not viol) else f"FAIL {viol}"
                nb = len((spec or {}).get("boxes") or [])
                print(f"  {lv:18} {secs:8.1f}s  {len(r.get('raw') or ''):6}  "
                      f"{ok}   {nb} boxes")
            except Exception as e:
                print(f"  {lv:18} {'--':>9}  {'--':>6}  {type(e).__name__}: "
                      f"{str(e)[:60]}")
        print("\n  boxes matter as much as latency: they decide richest_pan,")
        print("  so a cheaper image that loses them is not cheaper.")
        raise SystemExit(0)

    for p in (["gemini", "anthropic"] if a.provider == "both" else [a.provider]):
        try:
            r, secs = run(p, request, frames)
            summarise(p, r.get("spec"), r.get("violations"), secs,
                      len(r.get("raw") or ""))
        except Exception as e:
            print(f"\n--- {p} ---\n  FAILED  {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
