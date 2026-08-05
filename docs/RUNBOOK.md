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
