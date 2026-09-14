#!/usr/bin/env python3
"""Write every tablet screen to a standalone .html, with no robot attached.

WHY THIS EXISTS. Every layout bug this page has had was found by Cassie running
the whole system -- camera, serial, Whisper, the VLM -- and looking at an iPad.
That is a five-minute loop for a one-character CSS mistake, and it is why the
invalid `font:` shorthand survived two rounds of "the text is still small".
These files open in any browser, instantly, and they freeze the poll: the state
is injected once and `render()` runs against it, so what you see is what the
real page draws for that state.

    python3 tools/booth_preview.py                  -> /tmp/booth_preview/
    python3 tools/booth_preview.py -o ~/Desktop/bp

Then open `index.html` and click through. Resize the window to an iPad's aspect
(4:3-ish, landscape) -- the sizes are clamp()ed off the viewport, so a desktop
window at the wrong shape will lie to you about exactly the thing you came to
check.

NOT A SUBSTITUTE FOR THE TESTS. tests/test_booth_listen.py asserts the
properties that broke before -- the publish outside the detector-failed branch,
one #app rule, the stream outside the re-rendered markup. This is for the
question a test cannot answer, which is whether it looks right.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webui import booth


# A sweep that never happened. Five stations at the pans the grid expects; the
# files do not resolve, so the cells draw their frames and their tags and the
# pictures come up broken -- which is the correct amount of lying for a layout
# check. The point is the grid, the red frame and the rule, not the photographs.
FAKE_SWEEP = {
    "dir": "preview",
    "richest_pan": 30,
    "shots": [{"pan": p, "file": "s%d.jpg" % i,
               "dets": [{"tier": "focus"}] * (i % 3)}
              for i, p in enumerate([-60, -30, 0, 30, 60])],
}

FAKE_WATCH = [{
    "label": "hands-on the bag", "onobj": "backpack",
    "all": [9], "any": [], "not": [], "then": [],
    "sat": False, "cool": False, "on": {"9": False},
}]

FAKE_STORY = {
    "frame": "preview/story.jpg", "thumb": "preview/story.jpg", "shots": 4,
    "note": "Someone reached across the desk and picked up the backpack, "
            "looked inside it, and put it back where it was.",
    "time": "14:32",
}

SCREENS = [
    ("1-sleep", "idle, nothing asked of the tablet",
     {"flow_state": "S1_IDLE"}, None),
    ("2-listen-waiting", "button held, nothing said yet",
     {"flow_state": "S2_LISTEN", "heard": "", "heard_ok": True}, None),
    ("3-listen-heard", "Whisper returned a usable sentence",
     {"flow_state": "S2_LISTEN", "heard_ok": True,
      "heard": "tell me if someone touches my bag"}, None),
    ("4-listen-misread", "rejected -- shown, so it can be said again",
     {"flow_state": "S2_LISTEN", "heard": "beep beep beep",
      "heard_ok": False}, None),
    ("5-scanning", "five directions taken, the VLM still out",
     {"flow_state": "S4_PLAN", "plan_pending": True,
      "request": "tell me if someone touches my bag"}, "half"),
    ("6-watching", "the plan landed; the red cell is the live camera",
     {"flow_state": "S5B_TRACK", "aimed_pan": 30, "status": FAKE_WATCH,
      "request": "tell me if someone touches my bag"}, "full"),
    ("7-corrected", "after a head tap -- the red frame moved with it",
     {"flow_state": "S6_FINETUNE", "aimed_pan": -60, "status": FAKE_WATCH,
      "request": "tell me if someone touches my bag"}, "full"),
    ("8-notice", "the prompt, on both screens at once",
     {"flow_state": "S7b", "aimed_pan": 30, "status": FAKE_WATCH,
      "request": "tell me if someone touches my bag",
      "describe": "Someone reached across the desk and picked up your bag."},
     "full"),
    ("9-report-waiting", "after OK: the robot went back, the tablet waits",
     {"flow_state": "S5B_TRACK", "aimed_pan": 30,
      "request": "tell me if someone touches my bag"}, "full"),
    ("10-report", "the story, at full height, swiped sideways",
     {"flow_state": "S5B_TRACK", "aimed_pan": 30,
      "request": "tell me if someone touches my bag"}, "full"),
    ("11-wall", "the session's stories",
     {"flow_state": "S1_IDLE"}, None),
]

# MODE is the page's own override -- report and wall are not flow states, they
# are where the tablet goes on its own after OK. Forced here the same way the
# page forces them, so these two previews exercise the real branch.
_MODE = {"9-report-waiting": ("report", 9), "10-report": ("report", 0),
         "11-wall": ("wall", 0)}


def build(name, state, sweep):
    recs = [FAKE_STORY] * (3 if name in ("10-report", "11-wall") else 0)
    sw = None
    if sweep == "full":
        sw = FAKE_SWEEP
    elif sweep == "half":
        sw = dict(FAKE_SWEEP, shots=FAKE_SWEEP["shots"][:3],
                  richest_pan=None)
    d = booth.booth_state(state, recs, sw)
    if sweep == "half":
        d["chosen_pan"] = None          # mid-sweep: no plan yet
    boot = "S=%s;" % json.dumps(d)
    mode, wait_from = _MODE.get(name, (None, 0))
    if mode:
        boot += "MODE=%s;WAIT_FROM=%d;" % (json.dumps(mode), wait_from)
    boot += "render();place();"
    if state.get("flow_state") in ("S7a", "S7b"):
        boot += ("document.getElementById('pd').textContent=S.describe||'';"
                 "document.getElementById('veil').classList.add('on');")
    return booth.PAGE.replace("setInterval(poll,200); poll();", boot)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default="/tmp/booth_preview")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)

    links = []
    for name, why, state, sweep in SCREENS:
        with open(os.path.join(a.out, name + ".html"), "w") as f:
            f.write(build(name, state, sweep))
        links.append(f'<li><a href="{name}.html">{name}</a> <span>{why}</span></li>')

    with open(os.path.join(a.out, "index.html"), "w") as f:
        f.write("""<!doctype html><meta charset=utf-8><title>booth preview</title>
<style>body{background:#141414;color:#f2f0ea;font:16px/1.7 -apple-system,
sans-serif;padding:40px;max-width:760px;margin:auto}
h1{font-size:22px;font-weight:600}p{color:#8a867d}
ul{list-style:none;padding:0}li{padding:9px 0;border-bottom:1px solid #262622}
a{color:#cfe33a;text-decoration:none;font-weight:600}
span{color:#6f6c65;font-size:14px;margin-left:10px}</style>
<h1>NoticeBot &mdash; the tablet, every screen</h1>
<p>Frozen states, no robot attached. Resize to an iPad's shape before judging
any size: everything is clamp()ed off the viewport.</p>
<p>The sweep photographs will not load. That is on purpose &mdash; this is a
layout check, and the grid, the red frame and the rule are what it checks.</p>
<ul>""" + "\n".join(links) + "</ul>")

    print(f"{len(SCREENS)} screens -> {a.out}")
    print(f"open {os.path.join(a.out, 'index.html')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
