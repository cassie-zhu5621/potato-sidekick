# Blender 5.2-safe exporter. Run inside the clip's .blend (save the file first).
# Samples the scene timeline (Start..End frame) — no Action juggling, no slots.
# Writes export/<blend_name>.csv with t_ms, pan_deg, tilt_deg, pan_unit, tilt_unit.

import bpy
import csv
import math
import os

FPS = bpy.context.scene.render.fps
MAX_SAFE_DEG_S = 200.0   # Feetech SC no-load ~270-330 deg/s; stay well under

pan = bpy.data.objects["pan_pivot"]
tilt = bpy.data.objects["tilt_pivot"]
nod = bpy.data.objects.get("nod_pivot")   # 3rd DOF, optional

# LED. The generators keyframe the emission Strength, and some of that envelope
# is synchronised to motion accents -- S4's shutter flash fires 8 frames after
# the head stops at a station, S7b's peak lands on the top of the toss. Firmware
# cannot know when those moments are, so the envelope has to travel with the
# clip. Free-running behaviour (S5's cool breath) stays in firmware, which is
# also what keeps the LED alive if the serial link stalls.
LED_FULL = 8.0        # Strength that maps to 255. Generators use 0.8 .. 8.0.
led_socket = None
_mat = bpy.data.materials.get("led_mat")
if _mat and _mat.use_nodes:
    for _n in _mat.node_tree.nodes:
        if _n.type == 'EMISSION':
            led_socket = _n.inputs['Strength']
            break

blend_path = bpy.data.filepath
if not blend_path:
    raise RuntimeError("Save the .blend file first (Ctrl+S), then run again.")

name = os.path.splitext(os.path.basename(blend_path))[0]
export_dir = os.path.join(os.path.dirname(blend_path), "export")
os.makedirs(export_dir, exist_ok=True)

scene = bpy.context.scene
f_start, f_end = scene.frame_start, scene.frame_end

def to_unit(deg):
    # Feetech SC: 0-1023 over 300 deg, center (rig zero) = 512
    return max(0, min(1023, round((deg + 150.0) / 300.0 * 1023.0)))

rows = []
for f in range(f_start, f_end + 1):
    scene.frame_set(f)
    t_ms = round((f - f_start) * 1000.0 / FPS)
    p = round(math.degrees(pan.rotation_euler.z), 2)
    ti = round(math.degrees(tilt.rotation_euler.x), 2)
    no = round(math.degrees(nod.rotation_euler.x), 2) if nod else 0.0
    led = 0
    if led_socket is not None:
        led = max(0, min(255, round(led_socket.default_value / LED_FULL * 255)))
    rows.append((t_ms, p, ti, no, led))

max_v = 0.0
for r0, r1 in zip(rows, rows[1:]):
    dt = (r1[0] - r0[0]) / 1000.0
    if dt > 0:
        for a, b in zip(r0[1:4], r1[1:4]):      # joints only; LED has no speed limit
            max_v = max(max_v, abs(b - a) / dt)

path = os.path.join(export_dir, f"{name}.csv")
with open(path, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["t_ms", "pan_deg", "tilt_deg", "nod_deg",
                "pan_unit", "tilt_unit", "nod_unit", "led"])
    for t_ms, p, ti, no, led in rows:
        w.writerow([t_ms, p, ti, no, to_unit(p), to_unit(ti), to_unit(no), led])

warn = "  !! TOO FAST — flatten the steep part in the Graph Editor" if max_v > MAX_SAFE_DEG_S else ""
leds = [r[4] for r in rows]
led_note = (f", led {min(leds)}-{max(leds)}" if led_socket is not None
            else ", NO led_mat found")
msg = (f"{name}: {len(rows)} frames ({(rows[-1][0] / 1000.0):.1f}s), "
       f"peak {max_v:.0f} deg/s{led_note}{warn}")
print(msg + f" -> {path}")

# popup so the result is visible without a terminal (macOS)
def draw(self, context):
    self.layout.label(text=msg)
    self.layout.label(text=f"Saved: export/{name}.csv")

bpy.context.window_manager.popup_menu(
    draw, title="Export OK" if max_v <= MAX_SAFE_DEG_S else "Export — TOO FAST", icon='INFO')
