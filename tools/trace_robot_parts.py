#!/usr/bin/env python3
"""Cut Cassie's drawing of the robot into base / neck / head, for rigging.

WHY. The state figure was drawn with shapes invented in code, and it looked
like a diagram of some other robot. The body in the paper should be the body
she drew. So instead of redrawing it, this lifts her artwork off its background,
cuts it at the two joints, and records where those joints are -- and
`make_state_figure.py` then rotates the pieces by the angles in the clip CSVs.
Her line, our angles; neither one is guessed.

    python3 tools/trace_robot_parts.py ~/…/Untitled_Artwork\\ 5.jpg
    python3 tools/trace_robot_parts.py ART --side right --debug

WHAT GETS THROWN AWAY, and how. The drawing carries three things that are not
the robot: a grey drop shadow, cyan light rays, and dark-green annotation
arrows. Shadow and rays go by colour -- the body is green (g > r) and neither of
those is. Arrows that merely float nearby go by connectivity. An arrow whose
tail OVERLAPS the body cannot go by either: it is the same colour and the same
component, and a morphological reconstruction just flows down it and puts it
back. Those need an explicit `--erase` rectangle, which is at least honest about
being a decision rather than a filter.

WHICH DRAWING. Use the one whose pose is closest to UPRIGHT. The cuts are
horizontal, and a horizontal line cannot separate two parts that overlap in y --
on the bowed idle sketch the head hangs down beside the neck, so every row from
the crown to the collar contains both, and the "neck" slice comes away holding a
piece of jaw that splays out the moment it rotates.

THE CUTS overlap on purpose. A butt joint opens a visible wedge as soon as
anything turns, so each part keeps a little of its neighbour and they are drawn
base, neck, head -- the overlap always hidden under the piece above.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "motion", "robot_art")

# Fractions of the body's own bounding box, so the numbers survive a different
# scan or a re-export at another size. Tuned against Untitled_Artwork 5.jpg.
DEFAULT = {
    # where each part starts and ends, top = 0.0 at the crown
    "head":  [0.00, 0.46],
    # The neck must STOP ABOVE THE COLLAR. Its slice used to run past the
    # collar's top rim, and the rim is much wider than the neck -- so every
    # lean swung a green crescent out past the base that no draw order could
    # hide. The joint has to be cut where the body is narrow.
    "neck":  [0.40, 0.615],
    "base":  [0.59, 1.00],
    # the joints
    "head_pivot": [0.40, 0.430],     # x, y -- where the head sits on the neck
    "neck_pivot": [0.36, 0.630],     # x, y -- where the neck sits on the base
}

# The pose each drawing is IN, so every other pose is a delta from it. Without
# this the rig would treat her drawing as the zero pose and be wrong by exactly
# that much in every state. Both are read straight off the clips rather than
# eyeballed: the left figure is S1_IDLE's held pose, the right is the pose
# S2_LISTEN ends in.
REF = {"left":  ("S1_IDLE", 0), "right": ("S2_LISTEN", -1)}


def body_mask(sub):
    import numpy as np
    from scipy import ndimage
    r, g, b = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    # green, not cyan, not paper. The cyan rays satisfy g>r too, which is why
    # the blue test is needed and why it is written against b, not brightness.
    m = (g > r + 8) & (b <= r + 32) & (sub.sum(2) < 740)
    m = ndimage.binary_closing(m, np.ones((5, 5)))
    m = ndimage.binary_fill_holes(m)

    # ERODE, PICK, GROW BACK. Taking the largest connected component alone is
    # not enough: an arrow drawn so its tail grazes the head is one component
    # with the robot, and it came through. Eroding first snaps any contact
    # thinner than the neck, the largest surviving seed is unambiguously the
    # body, and propagating that seed back inside the original mask restores
    # the outline exactly -- so nothing of the robot is eaten in the process.
    k = np.ones((13, 13))
    seed = ndimage.binary_erosion(m, k)
    lab, n = ndimage.label(seed)
    if n == 0:
        sys.exit("no body found -- check --side and the crop")
    sizes = ndimage.sum(seed, lab, range(1, n + 1))
    seed = lab == (1 + int(max(range(n), key=lambda i: sizes[i])))
    return ndimage.binary_propagation(seed, mask=m)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("art", help="the jpg/png of the drawing")
    # LEFT by default. The right-hand figure has an annotation arrow whose tail
    # overlaps the head, and once two shapes touch, no colour test and no
    # connectivity test can separate them -- a reconstruction just flows down
    # the arrow and restores it. The left figure's extras (the zzz, the lamp)
    # touch nothing and fall out for free.
    ap.add_argument("--side", choices=("left", "right", "whole"), default="right",
                    help="which figure on the sheet (default: right = the "
                         "upright one, which is the one that cuts cleanly)")
    ap.add_argument("--erase", action="append", default=[], metavar="X0,Y0,X1,Y1",
                    help="rectangle of SOURCE pixels to blank before masking. "
                         "For annotation drawn in the body's own colour and "
                         "touching it. Repeatable.")
    ap.add_argument("-o", "--out", default=OUT)
    ap.add_argument("--debug", action="store_true",
                    help="also write a ruled sheet with the cuts drawn on")
    a = ap.parse_args(argv)

    import numpy as np
    from PIL import Image, ImageDraw

    im = Image.open(a.art).convert("RGB")
    arr = np.asarray(im).astype(int)
    for rect in a.erase:
        x0, y0, x1, y1 = (int(v) for v in rect.split(","))
        arr[y0:y1, x0:x1] = 255
        print(f"erased {x0},{y0} .. {x1},{y1}")

    if a.side != "whole":
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        ink = (g > r + 8) & (b <= r + 32) & (arr.sum(2) < 740)
        cols = ink.any(0)
        runs, s = [], None
        for i, v in enumerate(cols):
            if v and s is None:
                s = i
            if not v and s is not None:
                if i - s > 40:
                    runs.append((s, i))
                s = None
        if s is not None:
            runs.append((s, len(cols)))
        if not runs:
            sys.exit("no figures found on the sheet")
        x0, x1 = runs[0] if a.side == "left" else runs[-1]
        arr = arr[:, max(0, x0 - 20):x1 + 20]

    m = body_mask(arr)
    ys, xs = np.where(m)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    arr = arr[y0:y1 + 1, x0:x1 + 1]
    m = m[y0:y1 + 1, x0:x1 + 1]
    H, W = m.shape
    print(f"body {W}x{H}")

    os.makedirs(a.out, exist_ok=True)
    cfg = dict(DEFAULT)
    import csv as _csv
    state, idx = REF.get(a.side, REF["left"])
    rows = list(_csv.DictReader(
        open(os.path.join(ROOT, "motion", "clips", state + ".csv"))))
    cfg["ref_state"] = state
    cfg["ref_tilt"] = float(rows[idx]["tilt_deg"])
    cfg["ref_nod"] = float(rows[idx]["nod_deg"])
    print(f"reference pose = {state}[{idx}]  "
          f"tilt {cfg['ref_tilt']:+.0f}  nod {cfg['ref_nod']:+.0f}")
    rgba = np.dstack([arr, np.where(m, 255, 0)]).astype(np.uint8)

    # THE LAMP IS NOT PART OF THE BODY. It is warm (r > g) so the colour mask
    # rejected it, and then `binary_fill_holes` put it straight back, because it
    # sits enclosed by the head outline. Left alone it would be baked into all
    # ten states -- the robot glowing yellow while it sweeps the room. So the
    # warm pixels are repainted in the body's own fill: her head keeps its
    # shape, and the light channel is drawn per state by the figure, which is
    # where it belongs.
    warm = (rgba[:, :, 0] > rgba[:, :, 1] + 20) & (rgba[:, :, 3] > 0)
    if warm.any():
        body_px = rgba[:, :, :3][(~warm) & (rgba[:, :, 3] > 0)]
        fill = np.median(body_px.reshape(-1, 3), axis=0).astype(np.uint8)
        rgba[warm, 0:3] = fill
        print(f"  repainted {int(warm.sum())} lamp px as {tuple(int(v) for v in fill)}")

    parts = {}
    for name in ("base", "neck", "head"):
        f0, f1 = cfg[name]
        r0, r1 = int(f0 * H), min(H, int(f1 * H))
        piece = rgba[r0:r1].copy()
        if not piece[:, :, 3].any():
            sys.exit(f"{name} slice is empty -- the fractions need retuning")
        p = os.path.join(a.out, name + ".png")
        Image.fromarray(piece, "RGBA").save(p)
        # where this piece sits in the whole body, in body-box fractions, so
        # the figure can put it back together without knowing any pixel sizes
        parts[name] = {"file": name + ".png",
                       "x": 0.0, "y": r0 / H,
                       "w": W / W, "h": (r1 - r0) / H}
        print(f"  {name:5} rows {r0}-{r1}")

    meta = {
        "source": os.path.basename(a.art),
        "side": a.side,
        "erased": a.erase,
        "body_px": [int(W), int(H)],
        "aspect": W / H,
        "parts": parts,
        "head_pivot": cfg["head_pivot"],
        "neck_pivot": cfg["neck_pivot"],
        "ref_state": cfg["ref_state"],
        "ref_tilt": cfg["ref_tilt"],
        "ref_nod": cfg["ref_nod"],
        "_note": ("Pivots and cuts are fractions of the body bounding box. "
                  "Nudge them here rather than in the figure script; the "
                  "figure reads this file and never hard-codes a pixel."),
    }
    with open(os.path.join(a.out, "parts.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print("->", os.path.join(a.out, "parts.json"))

    if a.debug:
        flat = Image.new("RGB", (W, H), "white")
        flat.paste(Image.fromarray(rgba, "RGBA"), mask=Image.fromarray(rgba, "RGBA"))
        d = ImageDraw.Draw(flat)
        for name, col in (("head", (210, 70, 70)), ("neck", (70, 120, 210)),
                          ("base", (40, 150, 90))):
            for f in cfg[name]:
                y = int(f * H)
                d.line([(0, y), (W, y)], fill=col, width=3)
                d.text((6, y + 4), name, fill=col)
        for k, col in (("head_pivot", (230, 120, 0)), ("neck_pivot", (150, 0, 180))):
            px, py = cfg[k]
            d.ellipse([px * W - 9, py * H - 9, px * W + 9, py * H + 9],
                      outline=col, width=4)
        p = os.path.join(a.out, "_cuts_debug.png")
        flat.save(p)
        print("->", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
