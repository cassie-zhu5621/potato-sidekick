# STUDY / FILMING RUNBOOK — the English tablet

Branch `study-ipad-en`. Cut from `demo-expo-2026`, so it keeps the tablet the
exhibition build introduced, and it takes the request back from the tablet and
gives it to the robot.

|  | `cassie/tomato` (study) | `demo-expo-2026` (stand) | **this branch** |
|---|---|---|---|
| the request | spoken, PTT | tapped, 3 Japanese cards | **spoken, PTT** |
| language | English | Japanese | **English** |
| participant's screen | a static review page, second process | the whole interface | **the whole interface** |
| head tap | Correct only | Correct **and** wake | **Correct only** |
| `REPLAN_PERIOD_S` | 540 | 0 | **0** (inherited — see §6) |

Use it for Study 2 sessions run with a tablet, and for the teaser shoot.

---

## 1. Before the participant — is the VLM fast today?

```bash
python3 -m robot.tools.net_check
```

Read **FLOOR** (RUNBOOK §2b). 1–2 s means planning lands in 5–8 s and the sweep
screen carries it. 7–10 s means ~20 s, which is survivable in a study and bad on
camera — if you are filming, come back later.

A slow FLOOR is Google queueing and **cannot be fixed from here**: not by the
network, not by the tier, not by sending fewer frames. RTT 0.03 s while FLOOR
swung 1.3 → 20 s, and a hotspot made RTT worse without touching FLOOR.

**Do not change `NOTICEBOT_GEMINI_MODEL`** — it is pinned for the study.

---

## 2. Start it — ONE terminal

```bash
cd ~/Documents/Claude/Projects/potatobot/notice-sidekick-runkit
git checkout study-ipad-en

python3 noticebot_loop.py \
    --cam 0 \
    --cores3 /dev/cu.usbmodem11201 \
    --detector yoloworld \
    --cv-hz 4 \
    --feedback robot \
    --serve --conf 0.2
```

Identical to the study command. It prints two addresses:

```
open  http://localhost:8000                 <- yours
      http://192.168.x.x:8000/booth         <- type THIS on the iPad
```

There is no second terminal. RUNBOOK §4b's file server serves a static page that
must outlive a loop restart; this tablet has to *follow* the loop, so it lives in
the same server.

**One run per session.** A restart opens a new session folder, and the tablet's
story list is one `attention_log.jsonl`.

---

## 3. The iPad, once

Open the printed address in Safari, then:

1. **Share → Add to Home Screen.** Opening from there is full-screen with no
   address bar, so a participant cannot navigate away mid-session.
2. **Settings → Accessibility → Guided Access** on. Triple-click to lock it.
3. **Settings → Display & Brightness → Auto-Lock → Never.**

### Both devices on the same network, and not the building's

University and venue Wi-Fi very often has **client isolation**: two devices on
one SSID cannot reach each other, and there is no fixing it on the day. Use a
travel router or a phone hotspot with the laptop reaching the internet through
the same box.

The test: if `localhost:8000/booth` works on the laptop and the iPad hangs, it
is isolation.

---

## 4. What a session looks like

```
   asleep         (-_-) z z z on both screens, and one line on the tablet:
                  hold the button on the robot and tell it what to watch for.
                  NOTHING on the tablet is tappable here -- the way in is
                  the robot.
1  hold PTT       It lifts its head.                                    (S2)
                  The tablet shows three breathing dots.
2  speak          Their own words. When Whisper returns, the sentence
                  appears LARGE on the tablet.
                  Misheard -> the text goes red-brown and says so. They
                  hold the button and say it again. This is the whole
                  reason the sentence is on screen: a misread caught here
                  costs five seconds, and a misread caught at the sweep
                  costs the turn.
3  nod            Acknowledge. 1.7 s, no page of its own.               (S3)
4  sweep          Five stations appear as they are captured; when the plan
                  lands the boxes are drawn and the chosen one goes red.  (S4)
5  watch          SAME SCREEN. The red cell is now the LIVE camera; the
                  other four stay as sweep stills.                      (S5)
   touch head     -> Correct. It looks elsewhere and the red frame moves
                  with it. THE TAP DOES NOT WAKE IT on this branch.     (S6)
6  call           Chirp + lean. The same prompt on the tablet and on the
                  robot's screen.                                       (S7)
7  OK             Either screen. The robot nods and goes straight back to
                  watching; the TABLET stays behind and waits for the
                  report -- 6-45 s, with a face and a moving bar.
8  the story      The strip at full height, swiped sideways, sentence
                  underneath. `close` goes back to following the robot.
```

### The two extra touches on the room screen

- **Tap the red cell** → the live view fills the screen, everything else dims,
  the rule stays lit. Tap again to go back.
- **The sixth cell is the rule** — relation chips that light as each becomes
  true, the AND/OR/THEN between them, and the object they are bound to.

---

## 4b. Keys, from the terminal the loop was launched in

| key | does |
|---|---|
| `1`…`8`, `0` | force a state (1 Idle · 2 Attend · 3 Acknowledge · 4 Scan · 5 Watch · 0 Settle · 6 Correct · 7 Call · 8 Error) |
| `g` | S2_LISTEN — lifts the head without the button, for testing |
| `f` | force a finding — the keyboard twin of **notice this NOW** |
| `r` | torque off (press a state key to re-engage) |
| `q` | quit |

They work whether or not the preview window has focus.

---

## 5. Checking the tablet without the robot

```bash
python3 tools/booth_preview.py -o ~/Desktop/bp
open ~/Desktop/bp/index.html
```

Eleven frozen screens, no camera, no serial, no cloud. **Resize the window to an
iPad's shape before judging any size** — everything is `clamp()`ed off the
viewport, so a tall desktop window will lie to you about exactly the thing you
opened it to check. The sweep photographs deliberately do not load.

---

## 6. When it does not fire

Both controls are on the **laptop's** page (`http://localhost:8000`, LIVE tab,
**IF IT MISSES THE SCENE**):

- **notice this NOW** — skips the trigger and the confirmation judge. The robot
  performs the notice and collects the story exactly as if it had fired.
- **look around again** — a fresh sweep on the same request.

Give it ten seconds first. For a **study session** prefer waiting: a forced
notice is not the system behaving. For a **teaser take** a forced one is
legitimate insurance, but shoot the real trigger for the shot you actually use —
the record names which it was.

`REPLAN_PERIOD_S` is **0** on this branch, inherited from the stand, where a
self-directed sweep landing mid-visit read as a fault. **If you are running
Study 2 sessions on this branch, set it back to 540** or the design's one
self-directed sweep never happens and the session is not an instance of the
system being studied.

---

## 7. End of a session

Ctrl-C once. The teardown flushes any story still collecting, so the last finding
is on disk rather than lost with the process.

`session_feed/<run>/` is the whole session: every report, every sweep, every LLM
call, and `review.html`.

---

## Troubleshooting

| symptom | first suspect |
|---|---|
| iPad hangs, laptop is fine | client isolation (§3) |
| tablet stuck on the sleep screen | the loop is not publishing — check the terminal for a traceback |
| the sentence never appears on the tablet | `stt.probe()` at startup — a dead input gives a wav of zeros, which is indistinguishable from saying nothing |
| Whisper keeps mishearing one word | it is on screen now; have them rephrase rather than repeat. The acceptance test only asks "is this a request at all" |
| tapping the head does nothing | `touch_test.py` first; then whether it is in a state that accepts it. **It no longer wakes the robot** |
| nod, then sleep, no sweep | an `arm_next` ordering regression — every caller must `request()` first and `arm_next()` second |
| the story list is empty | the loop was restarted; the list is one `attention_log.jsonl` |
| planning takes 20 s | `net_check`. If FLOOR is high it is Google, and nothing here helps |
| the live cell flickers black | the stream got moved inside the markup `render()` rewrites — it must stay outside `#app` |
| a size on the tablet ignores the number in the source | a `font:` shorthand with `inherit` in the family slot. Invalid, so the whole declaration is dropped. `tests/test_booth_listen.py` guards it |

---

## What was NOT touched

The VLM path is byte-identical to `s2-study-build`: `perception/`,
`planning/planner.py`, `planning/judge.py`, `planning/provider.py`,
`planning/gemini_provider.py`, `robot/`, `motion/`. The clip set in
`motion/clips/*.csv` is unchanged and remains the motion authority
(`STUDY_CLIP_AUTHORITY.md`).

Changes are confined to `webui/booth.py`, `webui/server.py` (two routes and two
state fields) and three places in `noticebot_loop.py`.
