"""
rig_moves.py — watch the head run a clear, named motion sequence to eyeball the calibration.
No brain, no detection: just move so you can SEE each axis is right.

Convention (from rig.py): +pan = RIGHT, +tilt = DOWN, 0 = level.

Sequence each round:  LEVEL -> RIGHT -> LEFT -> LEVEL -> UP -> DOWN -> LEVEL -> NOD
Saves a frame at each named pose (move_<n>_<name>.jpg) so you can check framing too.

    python rig_moves.py                                  # uses rig.py defaults
    python rig_moves.py --camera http://172.20.10.2/ --port /dev/cu.usbmodem101
    python rig_moves.py --reps 5 --dwell 2               # loop 5x, hold 2s per pose
"""

from __future__ import annotations
import argparse, time
import cv2
from rig import GimbalRig, CAM_URL, SERIAL_PORT

# (name, pan, tilt) — tweak the magnitudes if you want bigger/smaller throws
POSES = [
    ("level",  0,   0),
    ("right", 40,   0),
    ("left", -40,   0),
    ("level",  0,   0),
    ("up",     0, -20),
    ("down",   0,   8),
    ("level",  0,   0),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", default=CAM_URL)
    ap.add_argument("--port", default=SERIAL_PORT)
    ap.add_argument("--reps", type=int, default=1, help="how many times to run the sequence")
    ap.add_argument("--dwell", type=float, default=1.5, help="seconds to hold at each pose")
    ap.add_argument("--save", action="store_true", help="also save a frame at each pose")
    args = ap.parse_args()

    rig = GimbalRig(cam_url=args.camera, port=args.port)
    try:
        for rep in range(args.reps):
            print(f"\n=== round {rep + 1}/{args.reps} ===")
            for i, (name, pan, tilt) in enumerate(POSES):
                rig.move_to(pan, tilt)
                print(f"  {name:6s}  pan={pan:+d} tilt={tilt:+d}")
                if args.save:
                    cv2.imwrite(f"move_{i}_{name}.jpg", rig.get_frame())
                time.sleep(args.dwell)
            print("  nod ↓↑")            # legible 'noticed': dip down and back, twice
            rig.nod(depth=8, times=2)
            time.sleep(args.dwell)
        rig.move_to(0, 0)               # rest at level
        print("\ndone.")
    finally:
        rig.close()


if __name__ == "__main__":
    main()
