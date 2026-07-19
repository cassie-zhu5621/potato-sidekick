# Potatobot

Pipeline: `context (typed) → VLM planner (planner.py) → watch-spec → CV executor
(relations.py + watch_exec.py) → records + web UI (attention_ui.py)`. The VLM is a
compiler/auditor, **not** per-frame. Vocabulary: `docs/relation_table.md`.

## 1. Install

```bash
python -m venv .venv && source .venv/bin/activate      # or conda, python 3.10+
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...                         # for the planner + judge
```

Auto-downloads on first run (no action needed): YOLO weights (`ultralytics`), and — if you
delete them — the two MediaPipe `.task` models (already shipped in `weights/`). Grounding DINO
downloads only if you pass `--detector gdino` (needs `transformers`).

## 2. Hardware (optional — also runs on a laptop webcam)

Flash from `firmware/` with the Arduino IDE:

- **`pantilt_r4/`** → Arduino Uno R4 + 2× MG90S (pan-tilt). Serial
  `/dev/cu.usbmodem*` @115200.
- **`cores3_sidekick/`** → M5 CoreS3 I/O board (screen / sound / touch)

macOS serial: use `/dev/cu.*` (not `tty.*`); close the Arduino Serial Monitor first.

## 3. Run one round

```bash
# edit this file live and the system re-plans on the next frame
echo "Two of us are assembling a robot arm this afternoon." > context.txt

# A) laptop webcam, keyless dry run (fake planner, no API key) 
python attention_system.py --offline --camera 0 --serve

# B) real planner, laptop webcam + web UI  (open the printed localhost URL)
python attention_system.py --camera 0 --serve --save --plan-frame

# C) full robot: M5 camera + pan-tilt + CoreS3
python attention_system.py --serve --save --plan-frame \
       --rig --port /dev/cu.usbmodem101 --cores3 /dev/cu.usbmodem1101
```

Useful flags: `--detector yolo|yoloworld|gdino` (default `yolo`, closed COCO), `--confirm`
(VLM re-checks each fire), `--no-sound`, `--cooldown <s>` (habituation), `--no-save` (test, no
disk). Hardware smoke tests: `python cam_test.py --camera http://<ip>/` and
`python rig_moves.py --port /dev/cu.usbmodem101`.

