# RUNBOOK — bringing the robot up, in order

The order matters. Each step isolates one layer, so that when something breaks
you already know which layer it is in. Skipping to step 3 and debugging from
there is how a servo problem gets mistaken for a CV problem.

`MACHINE_E2E_READINESS.md` is the longer bring-up narrative; this is the short
version you run every time.

---

## 0. Once per machine — dependencies

```bash
conda activate sidekick
cd ~/Documents/Claude/Projects/potatobot/notice-sidekick-runkit

pip3 install -r requirements.txt -r requirements-cv.txt
pip3 install feetech-servo-sdk python-dotenv google-genai ultralytics
```

The second line is the packages whose **pip name differs from their import
name**, or that the requirements files miss. They were each discovered the hard
way — one at a time, mid-run, after the robot had already been woken up:

| pip name | import name | why it is not obvious |
|---|---|---|
| `feetech-servo-sdk` | `scservo_sdk` | `pip install scservo-sdk` installs nothing useful |
| `python-dotenv` | `dotenv` | missing from `requirements.txt` |
| `google-genai` | `google.genai` | `google` is a namespace package, so it "exists" while empty — the error says `cannot import name 'genai'`, not "not installed" |
| `ultralytics` | `ultralytics` | only needed for `--detector yoloworld` |

Also: `.env` must exist (copy `.env.example`, add `GEMINI_API_KEY`). Not needed
for `--offline`.

### Which LLM answers

One variable, read in one place (`planning/provider.py`). Unset means Gemini, so
a fresh clone and the other machine behave exactly as before.

```
NOTICEBOT_LLM_PROVIDER=gemini       # default
NOTICEBOT_LLM_PROVIDER=anthropic    # + ANTHROPIC_API_KEY, + pip3 install anthropic
```

Try a provider **before** a session, on a sweep that already happened, rather
than discovering it with a participant in the room:

```bash
python3 robot/tools/provider_ab.py                    # newest recorded sweep
python3 robot/tools/provider_ab.py --provider both    # re-run both, live
```

The provider also selects the station-scoring rule (`planning/station_score.py`),
because that rule counts boxes and the two models do not box at the same density
— Gemini's is kept verbatim, Anthropic's caps what context boxes can contribute.
`plan.json` records both, and the sweep line prints them.

It prints the recorded answer beside the new one: latency, violations, watch
entries, boxes. **The boxes are the ones to read.** Their coordinate convention
lives in the prompt, not in the schema, so a model that reads it differently
returns numbers that are individually plausible and jointly wrong — a failure
that passes validation, passes the test suite, and shows up as the robot aiming
confidently at nothing.

---

## 1. No hardware — 30 seconds

```bash
python3 -m robot.states              # state table vs clips vs source literals
python3 tests/test_session_flow.py   # pytest CANNOT collect this one
python3 -m pytest -q
```

`test_session_flow.py` is an old-style script with no `test_` functions, so
`pytest` reports "no tests ran" and moves on. It is the only test of the state
machine. **Run it separately or it is silently skipped.**

Run this before touching the robot. It catches a broken state table, a stale
clip, and a mistyped state name — none of which need a servo to find.

---

## 2. Hardware health

```bash
python3 noticebot_loop.py --list-cams        # confirm the head cam's index
python3 robot/tools/preflight.py --cam 0     # bus / calibration / clips / camera
```

Wants `GO`. Notes:

- The head camera is the one that **actually delivers 2560×1440**. The built-in
  returns a square frame (Center Stage). Indices move whenever anything USB is
  replugged, so this is a per-session check, not a number to write down.
- Voltage: `< 5.5` warns, `< 5.0` fails. **Under-voltage does not raise — it
  makes the motion soft, slow and short of its target, which reads exactly like
  a calibration error.** Swap the pack before blaming a curve.

---

## 2b. Is the VLM fast today? — 40 seconds, before the participant arrives

```bash
python3 -m robot.tools.net_check          # 5 reps; pass a number for more
```

Planning latency is **not stable across days and is not ours to control**. On
2026-08-17 the planner's median went from 5.7 s (the previous week) to 18.6 s,
peaking at 49 s, with nothing changed on our side — same model, same thinking
level, same ~5460 input tokens. Knowing that at 09:00 is a schedule decision;
finding out at 14:05 with somebody in the chair is a lost session.

Three numbers, because they fail for different reasons:

| | what it measures | reading |
|---|---|---|
| **RTT** | TCP+TLS to the endpoint. No API key, no quota | `> 1 s` → the network. Try a hotspot |
| **FLOOR** | a call with a ~100-byte payload and an 11-token answer | `> 4 s` → **Google is queueing.** Nothing on this machine helps |
| **LOADED** | the same call carrying five real JPEGs, ~530 KB | `LOADED − FLOOR` big → the uplink or image ingestion |

It prints a verdict. **A slow FLOOR is the common case and it is not fixable** —
bandwidth cannot explain seconds for a hundred bytes. Measured 2026-08-17: Wi-Fi
RTT 0.03 s with FLOOR 7.7 s, and a hotspot made RTT *worse* (0.11 s) without
touching FLOOR. Do not go looking for a better connection.

**What to do when FLOOR is slow**

- Budget ~20 s for brief→watch and **push E1's cue later**, so the actor is not
  playing the scene while the robot is still sweeping.
- Do **not** change `NOTICEBOT_GEMINI_MODEL` mid-study, however tempting. Half of
  S2 is already on `gemini-3.5-flash`; a model change splits the dataset and
  makes the planner's relation choices incomparable across participants. (3.5 is
  two generations behind as of 2026-08-13 — 3.6 shipped 07-21, 3.7 on 08-13 —
  which is the most likely reason it is being served slowly. It is still not
  worth the confound.)
- Check <https://status.cloud.google.com/> — but the 08-17 slowdown was never
  posted there, so an all-green status page is not evidence.

---

## 3. Motion only — no perception, no cloud

```bash
python3 robot/clip_player.py --cores3 /dev/cu.usbmodem11201
```

Walks the whole CYCLE and reports lag and dropped frames.

**Pass: 0 dropped, worst lag under 20 ms.**

This is the step that separates *servo / power / timing* from *perception /
planning*. If it is clean here, no later motion problem is the detector's fault.
Single clips for a faster look:

```bash
python3 robot/clip_player.py S7a    # the most demanding: pan peaks at 121 deg/s
python3 robot/clip_player.py S7b    # holds the deep -22 lean under load
python3 robot/clip_player.py S4_PLAN
```

LED-only, touching no servos — separates "firmware not reflashed" from
everything else:

```bash
python3 robot/clip_player.py --led-test --cores3 /dev/cu.usbmodem11201
```

---

## 4. Full loop

```bash
python3 noticebot_loop.py \
    --cam 0 \
    --cores3 /dev/cu.usbmodem11201 \
    --detector yoloworld \
    --cv-hz 4 \
    --feedback robot \
    --serve
```

Web UI at **http://localhost:8000** — live view, plan, feed, and the context box
(type a request there instead of speaking).

| flag | why |
|---|---|
| `--detector yoloworld --cv-hz 4` | the detector, and the only one. Now also the default; `gdino` is kept selectable for reproducing old logs and is never run |
| `--feedback robot` | let a confirmed finding drive S7. `console` prints the judgment and moves nothing |
| `--offline` | no Gemini; use it to prove the mechanics before adding cloud latency |
| `--no-stt` | skip Whisper; type into the web context box |
| `--cores3 <port>` | explicit beats auto-detect when you already know the port |

```
1-8 force a state · 0 S5a · f manual finding · c cycle · r relax · q quit
```

Those keys are read from the **OpenCV preview window** when it is open, and from
the **terminal** when it is not (`stdin_key()`, cbreak on a tty). So `--no-view`
costs the preview window, not the override — earlier revisions of this file said
it "takes the keyboard with it", which was never true of the code.

**Use `--no-view` whenever `--serve` is on.** The OpenCV window is a native
window and can never render inside the browser page; leaving it open just means
two copies of the same stream, one of which steals focus. The web UI has already
had the live view at `/stream.mjpg` since it was written. With `--no-view
--serve` the annotated frame is still computed and published — the flag suppresses
the window, not the overlay.

---

## 4b. The participant's screen — a SECOND terminal

The tablet beside the robot shows the reports. It needs a plain file server,
because `file://` forbids the `fetch` the page uses to notice new reports:

```bash
cd ~/Documents/Claude/Projects/potatobot/notice-sidekick-runkit/session_feed && \
python3 -m http.server 8765 --bind 0.0.0.0
```

**The full path and the `&&` both matter.** This was two lines with a relative
`cd`, and run from anywhere but the project's parent the `cd` fails while the
shell goes on to the next line anyway — so the server comes up happily, serving
whatever directory you happened to be in. It looks like it worked; the tablet
just says 404 for `latest.html`. Hit on 2026-08-17 from the home directory.

Leave it running all day. Point the tablet at, once, and never again:

```
http://localhost:8765/latest.html      # on the laptop itself, or a Sidecar iPad
http://<laptop-ip>:8765/latest.html    # ipconfig getifaddr en0
```

`latest.html` follows `current.txt`, which the loop rewrites at startup, so it
tracks the current run across participants and across restarts without anybody
retyping a session name.

**A SEPARATE PROCESS ON PURPOSE.** Serving this from the `--serve` web UI would
be one window fewer and was rejected for it: the two would then die together, and
the moment that matters is a mid-session restart of the loop. The participant is
sitting in front of the tablet; it should keep showing the last report rather
than "cannot connect".

**Do not put this on the participant's phone.** The questionnaire's
imagined-camera block and interview Q6 both turn on the contrast with *phone
alerts*; delivering the reports by phone answers that question before it is
asked. A fixed screen that is not theirs is the point.

If the tablet cannot reach the laptop, suspect the venue's WiFi before anything
else — campus networks commonly isolate clients from each other, and then no IP
will work. `curl -sI http://<laptop-ip>:8765/latest.html` FROM THE LAPTOP splits
it: a 200 means the server and firewall are fine and the block is between the
devices. Sidecar, or a phone hotspot both machines join, sidesteps it entirely.

---

## 5. Shut down — every time

```bash
python3 robot/tools/play_on_hardware.py --relax
```

Ctrl-C runs the cleanup. **Closing the terminal window or `pkill` does not**:
the `finally` block never executes, so torque stays on and the servos hold their
last pose, heating, and the CoreS3 keeps whatever colour it was showing. Check by
hand — a relaxed neck moves freely.

---

## Reading a failure

| symptom | first suspect |
|---|---|
| `no CoreS3 found` | another process owns the port (Arduino IDE's serial monitor); then firmware |
| tablet says "waiting for a session…" forever | the loop has not started, or it is writing to a `--feed-dir` outside `session_feed/`. `cat session_feed/current.txt` |
| tablet shows the previous participant's reports | the loop never started for this one; `current.txt` is written before anything else can fail, so it is a run that did not run |
| tablet page never updates | opened as `file://`, not through the server — `fetch` is blocked there and the poll dies silently |
| planning takes 20–50 s and the participant is waiting | almost never ours. `python3 -m robot.tools.net_check` (§2b). A slow FLOOR is Google queueing and cannot be fixed from here |
| `404` for `latest.html` on a server that started fine | the `cd` failed and the shell ran `http.server` anyway, so it is serving the wrong directory. The 404 line is the give-away: the server is up, the file is not there. Use the full path with `&&` (§4b) |
| `PONG cores3_sidekick v4` after flashing | the board still has the old build; v5 is STOP-as-hold, OK-only on `noticed`, OK on `error` |
| LED never changes | no `--cores3`; then firmware; then a flat `led` column in the CSV (re-export) |
| transcript comes back empty | microphone permission for Terminal — macOS hands out a **silent stream** rather than an error |
| stuck on the `waiting` bar | Whisper is running; `stt_busy` postpones the 15 s deadline up to a 30 s ceiling. Press STOP: if it responds, the main loop is alive |
| motion soft / short of target | battery, not calibration |
| BODYTAP with nobody touching it | TTP223 SIG floating. Harmless in S1 — **in S5b every false tap is an S6** |
| `ignoring model '…' — not a Claude model` | a `NOTICEBOT_GEMINI_*` model variable is still set while the provider is `anthropic`. It is being dropped, which is the correct outcome; clear it to silence the line |
| `404` / `not_found_error` on the Anthropic path | the model name. `NOTICEBOT_ANTHROPIC_MODEL` overrides it without validation |
| `ran out of output budget` | raise `NOTICEBOT_ANTHROPIC_MAX_OUTPUT_TOKENS` (default 4096). On the Gemini path the same cause surfaces as `parse-fail: Expecting ',' delimiter` instead |

The run's artefacts, including every LLM call, land in
`session_feed/e2e_<timestamp>/`. Failed calls are audited too.

---

## Branches

Naming follows the existing `sonan/…` convention:

```bash
git checkout -b cassie/<what>
git add -A && git commit -m "..."
git push -u origin cassie/<what>

git fetch origin
git rebase origin/sonan/modification
```

Before pushing, the three checks from step 1. Note that changing
`generate_s4_sweep.py` **will** turn `test_sweep_plan.py` red: it hard-codes S4's
old structure (`led == 223`, a return of `> 200` units). That is the test
asserting the bug you are removing, and it has to be updated with the generator.
