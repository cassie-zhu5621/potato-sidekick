# NoticeBot-Potato2.0

A placed sidekick that watches for one thing you asked it to watch for, and tells you
when it happens in real-time.

You say *"the roundtable area at my lab — people usually have meetings here."*
The robot turns its head across the room, photographs each angle, and sends the
five independent images to Gemini in a **single** call. Gemini enumerates what is there, sorts it
into what matters and what is only context, and compiles your sentence into a
**watch-spec** over an eleven-row vocabulary of social relations (gaze, joint
attention, pointing, F-formation, turn-taking …). CV then evaluates that spec
frame by frame, at the angle that looked richest. When the spec is satisfied the
robot collects a short photo story of the moment and looks up at you.

   what is interesting is: 
      1.you can read and argue with what it decided to watch for
      2.the whole human-robot-collaboration loop of notice delegation


---

## The hardware list

This is v2, the 3-DOF lamp form.

| | |
|---|---|
| Motions | 3 × Feetech **SCS0009** serial-bus servos — pan(id:1) / tilt(id:2) / nod(id:3) |
| Bus | **FE-URT2-C001** USB↔TTL adapter, 1 Mbaud, external 5–6 V on the servo rail |
| Head camera | **InnoMaker OV4688** UVC module, 4 MP, H-FOV 58° |
| Microcontroller | **M5Stack CoreS3** — screen, speaker, TTP223 body-tap sensor, Grove Chainable RGB LED (P9813) |
| Body | simply designed 3-DOF lamp form (built in rhino)|
| Reasoning Brain | laptop. Everything runs here; the boards are I/O. |

---

## Running it

**On a new machine, start at `docs/GETTING_STARTED.md`.

```bash
pip install -r requirements.txt

# tier 0 — logic only, no hardware at all
python3 tests/test_session_flow.py

# tier 1 — MOTION ONLY. Servos and nothing else: no camera, no VLM, no API key.
export NOTICEBOT_PORT=/dev/cu.usbmodemXXXXX     
python3 robot/clip_player.py S7a                # one state
python3 robot/clip_player.py                    # every state
python3 robot/clip_player.py --all --cores3 /dev/cu.usbmodemXXXX     # every state, with LED and sound

# tier 2 — the full loop
export GEMINI_API_KEY=...                               # Planner + five-frame confirm
python3 noticebot_loop.py --cam 0 --cores3 /dev/cu.usbmodemXXXX --serve --feedback console
```

Before trusting a session: `python3 robot/tools/preflight.py` — a check for connection. will print a GO if everything's ok.

### Local CV smoke test

The default detector is Grounding DINO; MediaPipe Tasks supplies pose and face
landmarks. Bring up only that stack, without the robot, camera, VLM API, or
speech dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-cv.txt
.venv/bin/python -m pytest tests/test_cv_unit.py -q
.venv/bin/python perception/cv_smoke_test.py path/to/person.jpg \
  --vocab person,cup,laptop

# optional faster open-vocabulary adapter (same smoke-test contract)
.venv/bin/python perception/cv_smoke_test.py path/to/person.jpg \
  --detector yoloworld --vocab person,cup,laptop
```

The first real run downloads `IDEA-Research/grounding-dino-tiny` into the local
Hugging Face cache. Later runs are local. Inspect `session_feed/cv_smoke/result.json`
and `overlay.jpg` for boxes, pose, face count, device, and per-stage latency.

The explicit real-model integration test uses the same function:

```bash
CV_TEST_IMAGE=path/to/person.jpg .venv/bin/python -m pytest \
  tests/test_cv_integration.py -m integration -s
```

That integration test exercises both Grounding DINO and YOLO-World. Grounding
DINO remains the default; choose `--detector yoloworld` when latency matters.

### Gemini video trigger smoke test

Put `GEMINI_API_KEY=...` in the ignored `.env` file, then pass one local video and
one text trigger condition:

```bash
python3 planning/gemini_video_trigger.py path/to/video.mp4 \
  "当有人挥手时触发反馈"
```

The default is the low-latency profile: `gemini-3.5-flash-lite`, minimal
thinking, low media resolution, a 256-token output cap, and priority service
tier. Every value can be overridden through `.env` or CLI flags. The command
prints structured JSON containing `feedback_trigger`, confidence,
evidence timestamps, and a short Chinese feedback message. The uploaded test file
is deleted from the Gemini Files API after the request.

---

## What is where

```
noticebot_loop.py   MAIN — the only process
robot/              servos: bus, calibration, clip playback, firmware, CAD
motion/             the movement design: Blender sources, generators, clips
perception/         CV: detectors, pose, the 11-row relation engine
planning/           VLM: the planner, the relevance layer, the sweep
session/            the state machine, speech, storyboard, CoreS3, the feed
webui/              the researcher's browser view
docs/               specs and setup
tests/              pure-logic tests, no hardware needed
```

Every package's `__init__.py` says in a few lines what that package owns and what
each file in it does. Start there, then `docs/ARCHITECTURE.md` for how the layers
hand off.

---

## How to work on this
(manage the folders properly!!!)
| If you are changing… | you live in | you should not need to touch |
|---|---|---|
| how the robot moves | `motion/`, `robot/` | perception, planning |
| what counts as a relation | `perception/` | motion, the web UI |
| what the VLM is asked, and how its answer is compiled | `planning/` | motion, firmware |
| the interaction — screens, timing, sound | `session/`, `robot/firmware/` | perception |
| what the researcher sees | `webui/` | everything else |

!!!!!!!!!!!!Three important conventions: breaking each one has already cost us a day!!!!!!!!!!!!

1. **The CSVs in `motion/clips/` are build artefacts. Never hand-edit one.**
   To change a movement, edit the `.blend` in `motion/src/` (or the generator in
   `motion/blender/`) and re-export. A hand-edited CSV looks fine, then disagrees
   with the file it came from, and nobody can tell afterwards which one is real.

2. **`robot/calibration.py` describes THIS physical build.** Those numbers were
   *measured*, with `robot/tools/jog.py` — not chosen. If you rebuild the neck,
   re-measure. Do not nudge values until it looks right.

3. **One owner per channel.** The screen belongs to the state machine, the
   antenna's colour to the state table, its brightness to the playing clip.

---

## Specs

- `docs/GETTING_STARTED.md` — **start here on a new machine**
- `docs/ARCHITECTURE.md` — the four layers and what crosses between them
- `docs/INTERACTION_SPEC.md` — every trigger, screen and timeout, and why
- `docs/HARDWARE_SETUP.md` — wiring, servo IDs, calibration, flashing
- `docs/MOTION_AUTHORING.md` — the Blender workflow
- `docs/relation_table.md` — the eleven-row relation vocabulary, with citations
- `docs/TEST_PLAN.md` — pre-test finished, replace with user study design?
