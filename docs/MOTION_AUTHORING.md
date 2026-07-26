# Motion authoring

The robot's movement is **designed, not tuned in code** (animation-first, after
Hoffman & Ju). Blender 5.2 is the authoring tool; the CSVs the robot plays are
its output.

```
motion/src/<STATE>.blend   ← you edit this
motion/blender/*.py        ← or this, if the motion is procedural
        │  export_clip.py
        ▼
motion/clips/<STATE>.csv   ← build artefact. NEVER hand-edit.
        │
        ▼
robot/clip_player.py       ← plays it with absolute scheduling
```

## The one rule

**Never hand-edit a CSV.** Change the `.blend` (or its generator) and re-export.

A hand-edited CSV looks right and plays right, and then permanently disagrees
with the file it came from. The next export silently destroys the edit, and there
is no record of why the motion ever changed. Clips are cheap to regenerate; the
provenance is the expensive part.

## Editing by hand, in Blender

1. Open `motion/src/S3_ACK.blend`. One clip per file — no Action Editor, which
   avoids Blender 5.x slotted-action complexity entirely.
2. Select `pan_pivot`, `tilt_pivot` or `nod_pivot` in the Outliner, press `N`,
   Item tab, set the rotation, press `I` to key it. Only those three channels
   move; everything else is locked.
3. Set the scene End frame to the clip's last frame — the exporter uses the
   Start/End range.
4. Scripting tab → open `motion/blender/export_clip.py` → Run. It writes
   `export/<name>.csv` next to the `.blend` and warns on peak velocity.
5. Copy it into `motion/clips/`, then validate before going near the robot:

```bash
python3 robot/tools/check_clips.py ../../motion/clips
```

## Editing by generator

Some states are procedural, because their timing carries the meaning and hand
keys drift. Each generator is run inside Blender and rebuilds its `.blend`:

| generator | state | what it encodes |
|---|---|---|
| `generate_s1_idle.py` | S1 | slow sway + LED breathing, a true loop |
| `generate_s3_ack.py` | S3 | one nod with overshoot; sound fires at the nod's bottom |
| `generate_s4_sweep.py` | S4 | 5 stations over ±60°, no nod during the sweep (camera shake ruins the photos), LED flash per capture, lean-in at the lock |
| `generate_s5_track.py` | S5 | motionless hold; only the cool LED breathes |
| `generate_s6_finetune.py` | S6 | horizontal shake = negation; the red flashes share one key list with the shake, so they cannot drift off the beat |
| `generate_s7_found.py` | S7a | fast upward accent = summons |
| `generate_s7_beckon.py` | S7b | repeated upward throw, never a downward nod |
| `generate_s8_error.py` | S8 | slow lost sway + amber |

The generators raise rather than clamp when an angle is out of reach or a sweep
leaves a gap wider than the camera's 58° H-FOV. A clip that silently lost its
shape is worse than one that refuses to build.

## The grammar the library encodes

Worth knowing before adding a state, because it is a finding, not a style choice:

- **The vocabulary is separated by TIME, not by axis.** Fast horizontal (115°/s)
  reads as negation; slow horizontal sway (38°/s) reads as lost. Same axis,
  opposite meanings.
- **The accented stroke carries the meaning.** Fast-up summons; fast-down assents.
  A symmetric movement says nothing.
- **Redundant coding.** Colour, motion and rhythm each carry the state, so the
  signal survives red-green colour deficiency.
- **The floor is the actuator.** One SCS0009 unit is 0.293°, with a 1–2 unit
  deadband. Anything finer than that is not a movement, it is a hope.

## Reachable range on this build

Measured, in `robot/calibration.py`. Blender degrees:

| joint | range | note |
|---|---|---|
| pan | −76.4 … +70.2 | limited by the cable loom, not the servo |
| tilt | −38.3 … +29.2 | no mechanical stop |
| nod | +26.2 up … −47.1 down | **up is the tight side**, and S4/S5/S7 all use it |

Positive nod is UP in Blender and *down* in servo units; `robot/pose.py`
reconciles the two frames with `INVERT`. Get that backwards and the robot
performs every one of its gestures inside out.
