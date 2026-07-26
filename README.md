# NoticeBot

A desk robot that watches for one thing you asked it to watch for, and tells you
when it happens.

You say *"the roundtable area at my lab — people usually have meetings here."*
The robot turns its head across the room, photographs each angle, and sends all
of them to a VLM in a **single** call. The VLM enumerates what is there, sorts it
into what matters and what is only context, and compiles your sentence into a
**watch-spec** over an eleven-row vocabulary of social relations (gaze, joint
attention, pointing, F-formation, turn-taking …). CV then evaluates that spec
frame by frame, at the angle that looked richest. When the spec is satisfied the
robot collects a short photo story of the moment and looks up at you.

The contribution is not the detection. It is that **you can read and argue with
what it decided to watch for** — the compiled spec is on screen, each row lit as
it becomes true.

---

## The hardware this repo is for

This is v2, the 3-DOF lamp form. Nothing here supports the earlier Arduino R4
pan-tilt rig or the UnitCam S3 head; that code has been removed.

| | |
|---|---|
| Neck | 3 × Feetech **SCS0009** serial-bus servos — pan / tilt / nod |
| Bus | **FE-URT2-C001** USB↔TTL adapter, 1 Mbaud, external 5–6 V on the servo rail |
| Head camera | **InnoMaker OV4688** UVC module, 4 MP, H-FOV 58° |
| Face | **M5Stack CoreS3** — screen, speaker, TTP223 body-tap sensor |
| Antenna | Grove Chainable RGB LED (P9813) on CoreS3 Port B |
| Body | printed lamp form — `robot/cad/stl/` |
| Brain | your laptop. Everything runs here; the boards are I/O. |

---

## Running it

**On a new machine, start at `docs/GETTING_STARTED.md`.** It walks the three
tiers below in order, and it is written so you can stop at whichever one your
question lives in.

```bash
pip install -r requirements.txt

# tier 0 — logic only, no hardware at all
python3 tests/test_session_flow.py

# tier 1 — MOTION ONLY. Servos and nothing else: no camera, no VLM, no API key.
export NOTICEBOT_PORT=/dev/cu.usbmodemXXXXX     # robot/tools/check_bus.py finds it
python3 robot/clip_player.py S7a                # one state
python3 robot/clip_player.py                    # the whole designed cycle
python3 robot/clip_player.py --all --cores3     # every state, with LED and sound

# tier 2 — the full loop
export ANTHROPIC_API_KEY=sk-...
python3 noticebot_loop.py --cam 0 --cores3 --serve      # then localhost:8000
```

Tier 1 is the one to use when the question is about the movement. Do not debug a
motion problem from tier 2, where it looks like a perception problem.

Before trusting a session: `python3 robot/tools/preflight.py` — a GO / NO-GO
check with numeric criteria.

---

## What is where

```
noticebot_loop.py   the conductor — the only long-running process
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

## Working on this together

The split is meant to let two people work at once without meeting in the same
file:

| If you are changing… | you live in | you should not need to touch |
|---|---|---|
| how the robot moves | `motion/`, `robot/` | perception, planning |
| what counts as a relation | `perception/` | motion, the web UI |
| what the VLM is asked, and how its answer is compiled | `planning/` | motion, firmware |
| the interaction — screens, timing, sound | `session/`, `robot/firmware/` | perception |
| what the researcher sees | `webui/` | everything else |

Three conventions that are not negotiable, because breaking each one has already
cost us a day:

1. **The CSVs in `motion/clips/` are build artefacts. Never hand-edit one.**
   To change a movement, edit the `.blend` in `motion/src/` (or the generator in
   `motion/blender/`) and re-export. A hand-edited CSV looks fine, then disagrees
   with the file it came from, and nobody can tell afterwards which one is real.

2. **`robot/calibration.py` describes THIS physical build.** Those numbers were
   *measured*, with `robot/tools/jog.py` — not chosen. If you rebuild the neck,
   re-measure. Do not nudge values until it looks right.

3. **One owner per channel.** The screen belongs to the state machine, the
   antenna's colour to the state table, its brightness to the playing clip. When
   two code paths wrote the same channel we spent a day chasing a red LED that
   turned blue by itself.

---

## Specs

- `docs/GETTING_STARTED.md` — **start here on a new machine**
- `docs/ARCHITECTURE.md` — the four layers and what crosses between them
- `docs/INTERACTION_SPEC.md` — every trigger, screen and timeout, and why
- `docs/HARDWARE_SETUP.md` — wiring, servo IDs, calibration, flashing
- `docs/MOTION_AUTHORING.md` — the Blender workflow
- `docs/relation_table.md` — the eleven-row relation vocabulary, with citations
- `docs/TEST_PLAN.md` — staged bring-up, each stage with numeric pass criteria
