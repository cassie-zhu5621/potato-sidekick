#!/usr/bin/env python3
"""Cut a storyboard strip back into its panels, at full resolution.

`make_strip` composites N shots into one wide jpg with a plain hstack -- no
padding, no separators, no scaling once the camera's own height is kept. So the
cuts are exact arithmetic rather than a guess: a 10240x720 strip is eight
1280x720 frames and nothing else.

WHY PNG. The strip on disk is already a jpg, and its pixels are decoded before
they are written out again. Re-encoding as jpg would add a second generation of
loss to a figure for no reason at all; PNG from decoded pixels loses nothing
further. Pass --jpg if a paper's page budget needs it.

    python3 tools/split_strip.py session_feed/<run>/frame_2026...jpg
    python3 tools/split_strip.py <strip.jpg> -o figures/ --jpg
    python3 tools/split_strip.py <strip.jpg> --panel-width 1280

The panel width is inferred from the strip's own height by assuming 16:9, which
is what the head camera delivers. Anything else, say so with --panel-width; the
tool refuses rather than guessing when the width does not divide evenly, because
a strip cut a few pixels off is worse than one not cut at all -- the error shows
up as a sliver of the next panel down the edge of a figure.
"""
from __future__ import annotations

import argparse
import os
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("strip")
    ap.add_argument("-o", "--out", default=None,
                    help="directory for the panels (default: beside the strip)")
    ap.add_argument("--panel-width", type=int, default=None,
                    help="pixels; default = height * 16/9")
    ap.add_argument("--jpg", action="store_true",
                    help="write jpg quality 97 instead of lossless PNG")
    a = ap.parse_args(argv)

    import cv2
    im = cv2.imread(a.strip)
    if im is None:
        sys.exit(f"cannot read {a.strip}")
    h, w = im.shape[:2]

    pw = a.panel_width or int(round(h * 16 / 9))
    if w % pw:
        sys.exit(f"{w}px does not divide into {pw}px panels ({w / pw:.2f} of "
                 f"them). Pass --panel-width; a strip cut a few pixels off "
                 f"shows up as a sliver of the next panel along the edge.")
    n = w // pw

    out = a.out or os.path.dirname(os.path.abspath(a.strip))
    os.makedirs(out, exist_ok=True)
    base = os.path.splitext(os.path.basename(a.strip))[0]
    ext, params = ((".jpg", [cv2.IMWRITE_JPEG_QUALITY, 97]) if a.jpg
                   else (".png", [cv2.IMWRITE_PNG_COMPRESSION, 3]))

    print(f"{w}x{h} -> {n} panels of {pw}x{h}")
    for i in range(n):
        p = os.path.join(out, f"{base}_panel{i + 1}of{n}{ext}")
        cv2.imwrite(p, im[:, i * pw:(i + 1) * pw], params)
        print(f"  {os.path.basename(p)}  {os.path.getsize(p) // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
