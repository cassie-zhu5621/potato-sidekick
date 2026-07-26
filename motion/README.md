# motion — the movement design

```
src/        one .blend per state (plus rig.blend, the master rig)
blender/    scripts run INSIDE Blender: the rig builders, the clip generators,
            and export_clip.py
clips/      the exported CSVs the robot actually plays -- BUILD ARTEFACTS
```

Read `../docs/MOTION_AUTHORING.md` before changing anything here. The one rule:
never hand-edit a file in `clips/`.

A CSV row is `t_ms, pan_deg, tilt_deg, nod_deg, pan_unit, tilt_unit, nod_unit,
led`. `*_deg` is Blender's frame (positive nod = UP); `*_unit` is the Feetech
0-1023 position with the rig zero at 512. `led` is the antenna envelope, 0-255,
authored alongside the motion so the light and the movement cannot drift apart.
