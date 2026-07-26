# Feetech SC playback notes (Arduino)

## Setup

- Library: `SCServo` (Feetech's official Arduino lib, a.k.a. FTServo). Bus servos share one serial line — give pan ID=1, tilt ID=2 (use Feetech's FD software or the lib's ID-write example once).
- Wiring: servo TTL bus ↔ board UART (needs the Feetech TTL interface board or a half-duplex circuit). Power servos from a separate 5–7.4V supply, common ground.

## Calibration

1. Mount horns with the head facing "forward" while both servos are at position 512 (center = rig zero).
2. If mechanical zero is off, store per-servo offsets in firmware: `unit = csv_unit + offset`.
3. Verify travel: command 512±100 slowly, confirm direction matches Blender (+pan = same turn direction). If reversed, mirror in firmware: `unit = 1023 - unit`.

## Playing a clip

CSV rows are (t_ms, pan_unit, tilt_unit) at ~33 ms spacing (30 fps). Two options:

**Option A (simple, recommended first):** timed stepping — every 33 ms send `WritePosEx(id, unit, speed=0, acc=0)` with the next row. Bus servos glide between close targets; at 30 Hz this looks smooth.

**Option B (smoother):** use the servo's own time-based move — send target + travel time (`RegWritePosEx` with time = gap to next waypoint, then `RegWriteAction()` to fire pan+tilt simultaneously). Prevents pan/tilt de-sync.

Convert CSV → C array with a one-liner (run on laptop):
```bash
python3 -c "
import csv,sys
rows=list(csv.DictReader(open(sys.argv[1])))
print('const uint16_t CLIP[][3] PROGMEM = {')
print(',\n'.join(f'  {{{r[\"t_ms\"]},{r[\"pan_unit\"]},{r[\"tilt_unit\"]}}}' for r in rows))
print('};')" export/S3_ack.csv > S3_ack.h
```

## State machine integration

Each state plays its clip: looping (S1, S5, S8) or one-shot (S2, S3, S4, S6, S7). On state transition, interrupt the current clip and ease to the new clip's first pose over ~200 ms (send it as a single timed move) — avoids visible snapping.

## Safety

- Firmware clamps: never command outside [pan: 512±512 → mechanical limit; tilt: your mechanical range in units].
- On boot: slow move to center over 1 s, then enter S1 idle.
