# Getting started on a fresh machine

Three tiers. Each one runs on its own and adds hardware to the one before it, so
when something breaks you already know which layer it is in.

| tier | needs | what you can do |
|---|---|---|
| **0 — logic only** | just Python | run the state-machine tests, inspect clips |
| **1 — motion only** | servos + FE-URT2 (+ CoreS3) | play any state, or walk the whole cycle. **This is the tier for studying the movement.** No camera, no VLM, no API key. |
| **2 — full loop** | + head camera + API key | a real session: speak, plan, watch, notice |

---

## 0. Logic only (5 minutes, no hardware)

```bash
git clone <this repo> && cd notice-sidekick-runkit
python3 -m venv .venv && source .venv/bin/activate     # or conda; Python 3.10+
pip install -r requirements.txt

python3 tests/test_session_flow.py
```

Expect `all paths pass` (86 assertions). This exercises every transition —
STOP mid-scan, a transcript that arrives after its timeout, a tap in the wrong
state — with a fake clock and no devices. If this fails, do not plug anything in;
something is wrong with the checkout, not with your robot.

You can also inspect the motion library without hardware:

```bash
python3 robot/tools/check_clips.py motion/clips
```

It validates every CSV against this build's calibrated limits and reports peak
velocity. `GO` means the clips are safe to play.

---

## 1. Motion only — the tier for studying movement

**This is what you use when the question is about the movement itself.** It
touches the servo bus and nothing else: no camera is opened, no model is loaded,
no network call is made.

### Wire it up

Full detail in `HARDWARE_SETUP.md`. The three things that most often go wrong:

1. **The servo bus needs its own 5–6 V supply** on the FE-URT2's blue screw
   terminal. The board back-feeds USB 5 V through a diode, so a servo will answer
   at **4.3 V with no external supply at all**  enough to read registers. **If the bus reads 4.3 V, your external power is not arriving (Now a battery box with 4 batteries is used for current version).**
2. **The signal-level slider goes to 5 V**, not 3.3 V.
3. **Servo IDs must be 1 = pan, 2 = tilt, 3 = nod.** New servos all ship as ID 1,
   and three servos answering as ID 1 gives you total silence on the bus — which
   looks exactly like nothing being plugged in.

### Find the bus

```bash
python3 robot/tools/check_bus.py                 # probes ports and baud rates
export NOTICEBOT_PORT=/dev/cu.usbmodemXXXXXXX    # put this in ~/.zshrc
```

macOS calls the adapter `/dev/cu.usbmodem<location-id>`, and the name changes if
you replug into a different port or hub. `check_bus.py` probes rather than
guessing, so re-run it rather than trusting an old value.

Expect three lines with **~6 V**, not 4.3 V.

### (NOT NECESSARY with the SAME hardware) Calibration — read before you play anything 

`robot/calibration.py` holds numbers that describe **the physical build they were
measured on**. If you are running someone else's servos, or the neck has been
rebuilt, they are wrong for you and the robot will drive into its end stops.

```bash
python3 robot/tools/jog.py            # drive one joint by hand; c = centre, [ ] = limits
```

It writes `calibration.py` for you. This is measurement, do not
nudge the values until it looks right.

### Play the motion

```bash
# ONE state, then hold. This is the one you use to study a single movement.
python3 robot/clip_player.py S7a
python3 robot/clip_player.py S4_PLAN

# THE WHOLE CYCLE, in designed order: S1 → S2 → S3 → S4 → S5 → S6 → S7a → S7b
python3 robot/clip_player.py

# every state including S8_ERROR, which sits outside the cycle on purpose --
# it is not a step in the communication, it is what happens when the
# communication cannot continue
python3 robot/clip_player.py --all

# with the CoreS3 attached, so the LED envelope and the sounds play too.
# The LED level is authored INSIDE the clip, so it belongs to this tier.
python3 robot/clip_player.py --all --cores3
```

Two more, for when the movement looks wrong and you want to know why:

```bash
python3 robot/tools/play_on_hardware.py motion/clips/S4_PLAN.csv --dry-run  # read the CSV, touch nothing
python3 robot/tools/play_on_hardware.py motion/clips/S1_IDLE.csv --loop     # one clip on repeat
python3 robot/clip_player.py --cores3 --led-test    # LED only: is the firmware flashed?
```

**To change a movement you do not edit code.** Edit the `.blend` in
`motion/src/`, re-export, and drop the CSV into `motion/clips/`. See
`MOTION_AUTHORING.md`.

### Flash the CoreS3

Arduino IDE → open `robot/firmware/cores3_sidekick/cores3_sidekick.ino` →
board **M5Core S3** → upload. On boot it prints `IN HELLO cores3_sidekick v2`
and draws the idle screen with a big green PTT and a red STOP. 

---

## 2. The full loop

Everything in tier 1, plus:

```bash
export ANTHROPIC_API_KEY=sk-...      # the planner and the story narrator
python3 noticebot_loop.py --list-cams
```

`--list-cams` prints a verdict per camera. The head camera is the InnoMaker
OV4688: asked for 2560×1440 it complies and returns a wide 16:9 frame. A Mac's
built-in camera answers with something square (1552×1552) because of the Center
Stage sensor — **square is the giveaway, not size.**

### First run: go offline

```bash
python3 noticebot_loop.py --cam 0 --cores3 --serve --offline
```

`--offline` makes story captions come from the grounded CV trace instead of a
VLM call. Confirm the comic strip, the NOTICED feed and the screens all work
before you add the network to the list of things that might be wrong.

Then open <http://localhost:8000> and watch for these three lines in the
terminal. A missing one tells you which layer did not come up:

```
[cv]  ready (yolo)
[stt] whisper ready                    ← do not press PTT before this appears
[cores3] /dev/cu.usbmodemXXXX answered PING
```

The first `whisper ready` on a new machine takes a while — it downloads the
model. The loop warms it at startup precisely so that no participant is ever the
one waiting for it.

### The real thing

```bash
python3 noticebot_loop.py --cam 0 --cores3 --serve
```

Walk one round to confirm the whole chain:

1. Hold **PTT** on the CoreS3 — the bar moves while you speak.
2. Release — screen says `waiting...`.
3. Whisper answers — the robot nods, screen says `I heard you.`, and your
   sentence appears on the web page under the live view.
4. The head sweeps (`planning...`), then holds. **It stays on `planning...`
   while the VLM is still deciding** — that is correct, not a hang.
5. The plan lands: THE PLAN panel fills with watch entries, the head turns to the
   richest angle, screen changes to `tracking...`.
6. Press `f` in the terminal to force a finding — the robot looks up, the
   NOTICED tab shows `collecting a story: … 1/10 panels`, and about six seconds
   later a comic strip appears.
7. Press **OK** on the board, or leave it 30 s.
8. Press **STOP** at any point — the robot returns to idle *and* the watch-spec
   is torn down.

### Useful flags

| flag | for |
|---|---|
| `--offline` | no VLM narration; captions are the grounded trace |
| `--no-cv` | no detector or pose. THE PLAN stays empty; `f` is the only finding |
| `--no-cam` | servos + CoreS3 only |
| `--no-stt` | no Whisper; type requests into the web UI instead |
| `--detector yoloworld` | open vocabulary — the VLM's tiers re-prompt the detector live |
| `--feed-dir <path>` | where findings and sweeps are written |

### Where a session ends up

`session_feed/` (gitignored — it is data saved on local machine):

```
frame_<id>.jpg               the comic strip of one noticed moment
thumb_<id>.jpg               its card in the feed
attention_log.jsonl          one line per record: note, story, truth vector, worth
sweeps/<timestamp>/
    panorama.jpg             the contact sheet the VLM actually read
    pan_+030.jpg …           each angle, with the VLM's boxes drawn by tier
    plan.json                seen / detect / focus, the watch-spec, and coverage
```

`plan.json`'s `coverage` is worth reading after a session: it counts, per planned
object, how many angles the VLM boxed it in. A zero means it planned around
something it never actually saw.

---

## Debug

| symptom | look here |
|---|---|
| bus reads 4.3 V | external power is not reaching the screw terminal |
| total silence on the bus | two servos sharing an ID — `check_bus.py --scan-ids` |
| port disappeared after replugging | re-run `check_bus.py`; usbmodem names move |
| robot drives into its end stop | `calibration.py` is not this build's — re-measure with `jog.py` |
| screen shows something other than the nine screens | old firmware; reflash |
| first PTT fails, second works | you pressed before `[stt] whisper ready` |
| THE PLAN panel is empty | no `ANTHROPIC_API_KEY`, or you passed `--no-cv` |
| NOTICED stays 0 after a finding | wait out the 6 s linger; the tab shows `+1` while collecting |
