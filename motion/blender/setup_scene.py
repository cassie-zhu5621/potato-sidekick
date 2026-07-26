# One-click studio setup: lights + backdrop + camera + world.
# Run once in each clip file (after shell), then render. Re-runnable.
#
# Renders via: View > Viewport Render Animation (in Rendered shading), or
# proper Render > Render Animation (Ctrl+F12) using the camera this creates.

import bpy
import math

ROBOT_FOCUS = (0, 0, 0.14)   # aim mid-body; raised for the 8cm neck

# clear previous setup
for name in ["key_light", "fill_light", "rim_light", "backdrop",
             "studio_cam", "cam_target"]:
    o = bpy.data.objects.get(name)
    if o:
        bpy.data.objects.remove(o, do_unlink=True)

# --- world: Material-Preview-style studio HDRI lighting,
#     but the camera still sees a clean flat gray backdrop ---
import os
HDRI = "forest.exr"   # same as Material Preview default; try "studio.exr", "interior.exr"
world = bpy.context.scene.world
if world is None:
    world = bpy.data.worlds.new("World")
    bpy.context.scene.world = world
world.use_nodes = True
wn = world.node_tree
wn.nodes.clear()
out = wn.nodes.new('ShaderNodeOutputWorld')
mix = wn.nodes.new('ShaderNodeMixShader')
bg_hdri = wn.nodes.new('ShaderNodeBackground')
bg_flat = wn.nodes.new('ShaderNodeBackground')
lp = wn.nodes.new('ShaderNodeLightPath')
env = wn.nodes.new('ShaderNodeTexEnvironment')
loaded = False
try:
    base = bpy.utils.system_resource('DATAFILES', path=os.path.join("studiolights", "world"))
    files = sorted(f for f in os.listdir(base) if f.lower().endswith((".exr", ".hdr")))
    pick = HDRI if HDRI in files else (files[0] if files else None)
    if pick:
        env.image = bpy.data.images.load(os.path.join(base, pick), check_existing=True)
        print("World HDRI:", pick, "| available:", files)
        loaded = True
except Exception as e:
    print("HDRI load failed:", e)
if not loaded:
    # fallback: flat light — never leave a missing texture (renders magenta!)
    wn.links.remove(bg_hdri.inputs["Color"].links[0]) if bg_hdri.inputs["Color"].links else None
    bg_hdri.inputs["Color"].default_value = (0.85, 0.85, 0.86, 1.0)
bg_hdri.inputs["Strength"].default_value = 1.0
bg_flat.inputs["Color"].default_value = (0.80, 0.80, 0.81, 1.0)  # what the camera sees
bg_flat.inputs["Strength"].default_value = 1.0
if loaded:
    wn.links.new(env.outputs["Color"], bg_hdri.inputs["Color"])
wn.links.new(lp.outputs["Is Camera Ray"], mix.inputs["Fac"])
wn.links.new(bg_hdri.outputs["Background"], mix.inputs[1])   # lighting
wn.links.new(bg_flat.outputs["Background"], mix.inputs[2])   # seen by camera
wn.links.new(mix.outputs["Shader"], out.inputs["Surface"])

# tracking target first (camera + lights all aim at this)
tgt = bpy.data.objects.new("cam_target", None)
tgt.location = ROBOT_FOCUS
bpy.context.scene.collection.objects.link(tgt)

def aim(obj):
    tc = obj.constraints.new('TRACK_TO')
    tc.target = tgt
    tc.track_axis = 'TRACK_NEGATIVE_Z'
    tc.up_axis = 'UP_Y'

def add_area(name, loc, power, size):
    data = bpy.data.lights.new(name, 'AREA')
    data.energy = power
    data.size = size
    obj = bpy.data.objects.new(name, data)
    obj.location = loc
    bpy.context.scene.collection.objects.link(obj)
    aim(obj)
    return obj

# --- HDRI does the main lighting; keep only a gentle rim for edge definition ---
add_area("rim_light", (0.05, -0.45, 0.30), 30, 0.4)

# --- seamless white backdrop (floor plane, big) ---
bpy.ops.mesh.primitive_plane_add(size=4, location=(0, 0, -0.001))
floor = bpy.context.object
floor.name = "backdrop"
fm = bpy.data.materials.get("backdrop_white") or bpy.data.materials.new("backdrop_white")
fm.use_nodes = True
fb = fm.node_tree.nodes.get("Principled BSDF")
if fb:
    fb.inputs["Base Color"].default_value = (0.92, 0.92, 0.92, 1)
    fb.inputs["Roughness"].default_value = 0.9
floor.data.materials.append(fm)

# --- camera: 3/4 FRONT view (face is on +Y; S7 pans toward -X side) ---
cam_data = bpy.data.cameras.new("studio_cam")
cam_data.lens = 45   # wider — full robot with headroom for big motions
cam = bpy.data.objects.new("studio_cam", cam_data)
cam.location = (0.48, 0.50, 0.30)   # ~45 deg off front: tilt/nod motions read in profile
bpy.context.scene.collection.objects.link(cam)
aim(cam)
bpy.context.scene.camera = cam

# --- render settings ---
scene = bpy.context.scene
scene.render.resolution_x = 1920
scene.render.resolution_y = 1080
scene.render.fps = 30

msg = ("Studio ready. World: " + ("HDRI " + pick if loaded else "flat gray (no HDRI found)")
       + ". Render menu > Render Animation.")
print(msg)
def draw(self, context):
    self.layout.label(text=msg)
bpy.context.window_manager.popup_menu(draw, title="Scene setup", icon='INFO')
