# Hardware setup

Everything you need to bring the neck up on a fresh machine: wiring, power,
servo IDs, calibration, and flashing the CoreS3. Written from the first
bring-up session, with the wrong turns kept in -- most of them are not obvious
and every one cost hours.

**Last verified 2026-07-27.** All three joints calibrated, all eight clips play,
the full state machine runs.

---

## The robot

**3 DOF: pan (turn) → tilt (neck lean) → nod (head).** Animation-first motion
design (Hoffman & Ju) in **Blender 5.2**. All files in `motion/` and `robot/`.

- `motion/blender/` — `build_pantilt_rig.py`, `repair_rig.py`, `add_nod_joint.py`,
  `build_shell.py`, `setup_scene.py`, `export_clip.py`, and clip generators
  `generate_s3_ack.py` / `generate_s4_sweep.py` / `generate_s6_finetune.py` /
  `generate_s7_found.py` / `generate_s7_beckon.py`.
- Motion library v0.1 = 8 states (S1 idle, S2 listen, S3 ack, S4 plan/sweep,
  S5 track, S6 fine-tune, S7 found+beckon, S8 error).
  Rendered demo: the demo render (not committed -- ask cassie).
- Motion grammar: horizontal shake = negation (S6 only); vertical = affirm /
  beckon; loop clips (S1/S5/S8) are pure loops.

### Exported clips
`export_clip.py` writes `motion/clips/<CLIP>.csv` with columns
`t_ms, pan_deg, tilt_deg, nod_deg, pan_unit, tilt_unit, nod_unit`
where `*_unit` is the Feetech 0–1023 position (300° travel, rig zero = 512).


CSVs are build artefacts. **Never hand-edit them** -- see `docs/MOTION_AUTHORING.md` for why, and for the export workflow.

---

## Hardware as built

- **3× Feetech SCS0009** serial bus servos. **IDs: pan=1, tilt=2, nod=3.**
  They report `model=1284`.
- **FEETECH FE-URT2-C001** USB→TTL adapter. **CH343 chip → enumerates as
  `/dev/cu.usbmodem*`, not `usbserial*`.** Measured port on this Mac:
  `/dev/cu.usbmodem5B790340551` @ **1 000 000 baud**.
- SCS0009 has only ONE cable (no pass-through), so the three servos are **not**
  daisy-chained: nod goes to one GVS socket, pan+tilt share the other through a
  small splitter PCB.
- **Camera: OV4688 UVC module**, 32×32 mm, 4×M2 holes.
  **FOV D=71°, H=58°.** Up to 4 MP (2688×1520) @30 fps, 2K @60, 1080p @60;
  YUY2 + MJPEG; UVC class, driver-free. HDR/WDR, OmniBSI-2 BSI.
  *(Supersedes the old Waveshare OV2735 / 96° figure — that is no longer true
  and the 38° of lost field changes the S4 sweep maths.)*
- Print/assembly spec + STLs: `robot/cad/`.

### Power — read this before plugging anything in
- **The servo bus needs its own 5–6 V supply on the blue screw terminal.**
  SCS0009 is a 6 V-class servo. The board accepts 4.8–12 V but V1 is a
  **pass-through to the servo V pin**, so 9 V or 12 V destroys the servos.
- The board **does** back-feed USB 5 V onto the bus through a diode, so a servo
  will answer at **4.3 V with no external supply at all**. That is enough to
  read registers and nothing more — it is not "working", and it masks a
  disconnected supply. If the bus reads 4.3 V, external power is not arriving.
- Board LED is on the USB/logic rail. **It does not light from the screw
  terminal alone.** That is normal, not a fault.
- The serial signal level slider should be at **5 V**, not 3.3 V.
- Type-C and the UART header are alternative *command* sources — pick one. That
  is unrelated to power; USB and external power are used together.

---

## Software

```bash
python3 -m pip install feetech-servo-sdk pyserial
export NOTICEBOT_PORT=/dev/cu.usbmodem5B790340551   # put this in ~/.zshrc
export NOTICEBOT_BAUD=1000000
```

> **The pip package is `feetech-servo-sdk`; the import name is `scservo_sdk`.**
> `scservo-sdk` does not exist on PyPI. The pip package ships only the
> low-level `PacketHandler` — there is **no `scscl` class** like in Feetech's
> C++ SDK or GitHub folder. `robot/scs.py` supplies the SCSCL register map
> and packet packing on top of it.
>
> SCS/SCSCL is big-endian: `PacketHandler(1)`. Using 0 (the STS/SMS setting)
> does not error, it just returns byte-swapped garbage positions.

`robot/` and `robot/tools/`, in the order you use them:

| script | what it does |
|---|---|
| `robot/scs.py` | SCSCL layer: ping, read pos/load/voltage/temp, write pos, torque, set ID |
| `robot/tools/check_bus.py` | find port + baud, ping IDs, `--watch` live voltage, `--noise` sensor steadiness, `--set-id` |
| `robot/tools/jog.py` | interactive per-joint jogging; records centre `c` and limits `[` `]`; writes `calibration.py` |
| `robot/calibration.py` | per-joint CENTRE / LIMITS / OFFSET / INVERT. Config, hand-editable |
| `robot/tools/play_on_hardware.py` | plays a clip CSV on the clip's own clock. `--dry-run`, `--center`, `--loop`, `--relax` |

Note zsh: `P="--port X --baud Y"; script.py $P` does **not** work — zsh does not
word-split unquoted expansions. Hence the environment variables.

---

## Calibration (measured 2026-07-26)

| joint | id | centre | limits | usable angle | offset | invert |
|---|---|---|---|---|---|---|
| pan | 1 | 508 | 268–768 | −70° / +76° | −4 | **True** |
| tilt | 2 | 218 | 118–348 | −29° / +29° | −294 | **True** |
| nod | 3 | 560 | 470–720 | −26° / +47° | +48 | False |

- **pan's limit is the cable loom through the axis, not the servo.** The USB
  camera cable is the stiffest thing in it.
- **tilt has no mechanical stop** — it runs to the servo's electrical end (unit
  58 was reachable by hand). Its limits are chosen from what the clips need
  plus margin, not from where it stops.
- pan and tilt run mirrored vs the Blender render, confirmed against
  the demo render (not committed -- ask cassie) using S2_LISTEN (pan) and S3_ACK (tilt).
- nod: two frames that run opposite, and mixing them up is easy —
  **in Blender positive nod = head UP**; **on the bus a higher unit = head
  DOWN**. `INVERT["nod"]=True` reconciles them. Always author against the
  render; the servo frame is what INVERT absorbs. (nod's True is provisional —
  set from the viewport, not yet seen move on hardware.)
- The pan horn originally sat so the working arc **straddled the pot's 0/1023
  seam**, which makes position control undefined. It was remounted. If pan
  readings ever wrap 1023→0 again, remount rather than trying to fix it in
  software.

`LIMITS` are a guard rail, not a record of travel: wider than the motion needs,
well short of anything that can jam. `play_on_hardware.py` refuses to play while
any joint is listed in `UNCALIBRATED`.

---

## Coverage design for S4 (the scan)

The study only needs the **forward ~180°**; behind the robot does not matter.

**±60° pan sweep + 58° H FOV = −89°…+89° = 178° covered.** The cable-imposed pan
limit and the coverage requirement happen to land on the same number, so there
is nothing to gain from re-routing the loom for coverage.

Station spacing must stay **under 58°** or the sweep leaves unphotographed gaps
(invisible in the render; shows up later as the VLM "missing" objects).
`generate_s4_sweep.py` raises rather than generating a gapped sweep.

| N | step | overlap | length |
|---|---|---|---|
| 3 | 60° | **gap** | 5.0 s |
| 4 | 40° | 31% | 5.6 s |
| **5** | **30°** | **48%** | **6.2 s** |
| 7 | 20° | 66% | 7.4 s |

N=5 is the working choice: ~6 s reads as deliberate scanning without making the
participant wait, and 30° steps resolve the "richest position" finely enough.

---

## Clip status

| clip | on hardware | note |
|---|---|---|
| S1_IDLE | ✅ played | 9°/s, the smoke test |
| S2_LISTEN | ✅ played | used to confirm pan INVERT |
| S3_ACK | ✅ played | used to confirm tilt INVERT |
| S4_PLAN | ❌ | **regenerated (v2), needs re-export.** v1 asked for ±150° |
| S5_TRACK | not yet | opens at pan +25°, tilt −12° — S4 v2 ends exactly there |
| S6_FINETUNE | not yet | |
| S7a / S7b | not yet | S7a peaks 204°/s, over the 200°/s ceiling — re-author |
| S8_ERROR | not yet | |

Velocity ceiling when authoring: **200°/s** (SCS0009 no-load ~270–330°/s at 6 V;
less at 5.9 V on batteries).

---

## Next steps

1. Re-run `generate_s4_sweep.py` (v2: no nod, neck vertical during the sweep,
   ±60°, LED-only capture signal, soft arrival) → save → `export_clip.py`.
2. Re-export every other clip so they carry the nod channel. In Blender signs
   nod may run **+26° (up) … −47° (down)** — the tight side is UP, which is the
   side S4/S5 use (+15° leaves 11° spare).
3. Slow S7a below 200°/s.
4. Play the rest of the library: S5, S6, S8, S2, then S7 last.
5. Wire the state machine: loop S1/S5/S8, one-shot S2/S3/S4/S6/S7, ~200 ms
   eased transitions.
6. LED (warm/cool, breath/pulse/flash per state) + optional audio driven per
   state, not from the CSV. The removed v1 `pantilt_r4.ino` (Arduino R4) had
   usable `breathe()` / `ledBurst()` / `chirp()` on D6/D8 — but note that
   firmware drives **MG90S PWM servos** and cannot drive SCS0009, and its
   blocking linear `easeTo()` would flatten the authored timing curves. If an
   Arduino comes back, it should do LED and sound only, on its own USB port.

Swap the AA battery pack for a 5 V USB supply or a 6 V adapter when one is to
hand — the pack was down to 5.9 V after one session, and torque scales with it.

---

## Paper context

**NoticeBot** — notice delegation; a placed "second attention". The motion
library makes the robot's states legible. Target: **HRI 2026 Design track,
deadline 2026-09-18.** The motion work supports the Design contribution: a
legible communication cycle. This is why playback timing is preserved exactly
rather than being re-interpolated by firmware — the expressive character lives
in the ease curves, and re-timing them throws away the contribution.
