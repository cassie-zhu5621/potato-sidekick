#!/usr/bin/env python3
"""
Run this before every session. One command, GO / NO-GO.

Everything here is a thing that has already gone wrong once and cost time:
torque left off by the previous script, a battery pack down to 4.8 V, a
calibration file with a joint still marked provisional, a camera index that
moved because something else was plugged in. None of them announce themselves --
they present as "the robot does nothing" or "the motion looks wrong", twenty
minutes into a session with a participant sitting there.

  export NOTICEBOT_PORT=/dev/cu.usbmodemXXXX
  python3 preflight.py
  python3 preflight.py --cam 0
"""
import argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Run me directly: the repo root is two levels up from robot/tools/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

OK, WARN, FAIL = "ok  ", "warn", "FAIL"
results = []


def check(label, status, detail=""):
    results.append((status, label, detail))
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", type=int, default=None)
    ap.add_argument("--clips", default=os.path.join(HERE, "..", "model", "v2.0", "export"))
    a = ap.parse_args()

    print("\n--- calibration ---")
    try:
        from robot import calibration as cal
        uncal = set(getattr(cal, "UNCALIBRATED", set()))
        check("calibration.py loads", OK)
        check("all joints calibrated", FAIL if uncal else OK,
              f"still provisional: {sorted(uncal)}" if uncal else "")
        for j in ("pan", "tilt", "nod"):
            lo, hi = cal.LIMITS[j]
            span = (hi - lo) / (1023 / 300.0)
            check(f"{j} limits {lo}-{hi}", WARN if span < 15 else OK,
                  f"only {span:.0f} deg of travel" if span < 15 else f"{span:.0f} deg")
    except Exception as e:
        check("calibration.py", FAIL, str(e))

    print("\n--- clips ---")
    try:
        rc = subprocess.run([sys.executable, os.path.join(HERE, "check_clips.py"),
                             a.clips], capture_output=True, text=True)
        out = rc.stdout
        # check_clips exits 1 on any problem; surface only the summary here
        tail = out.split("--- summary ---")[-1].strip() if "--- summary ---" in out else out
        check("check_clips.py", OK if rc.returncode == 0 else WARN,
              tail.replace("\n", " | ")[:160])
    except Exception as e:
        check("check_clips.py", FAIL, str(e))

    print("\n--- state table ---")
    try:
        from robot import states as ST
        avail = {f[:-4] for f in os.listdir(a.clips) if f.endswith(".csv")}
        probs = ST.validate(avail)
        check(f"{len(ST.STATES)} states vs {len(avail)} clips",
              FAIL if probs else OK, "; ".join(probs))
    except Exception as e:
        check("states.py", FAIL, str(e))

    print("\n--- servo bus ---")
    if True:
        try:
            from robot.scs import open_bus, list_candidates
            from robot.pose import IDS
            # Probed, not read from a config: macOS renames usbmodem devices by
            # location id, so any stored path goes stale the moment the adapter
            # is moved to another socket.
            bus, port = open_bus()
            check("servo bus port", OK, f"{port} (probed)")
            try:
                volts = []
                for name, sid in IDS.items():
                    model, alive = bus.ping(sid)
                    if not alive:
                        check(f"{name} (id {sid})", FAIL, "no reply")
                        continue
                    v = bus.read_voltage(sid)
                    t = bus.read_temp(sid)
                    volts.append(v or 0)
                    st = OK
                    detail = f"{v}V {t}C"
                    if v and v < 5.0:
                        st, detail = FAIL, f"{v}V -- too low, the bus is on USB "\
                                           f"back-feed or the pack is flat"
                    elif v and v < 5.5:
                        st, detail = WARN, f"{v}V -- torque is down, swap the pack"
                    if t and t > 50:
                        st, detail = WARN, f"{detail}, running hot"
                    check(f"{name} (id {sid})", st, detail)
                    # torque state is invisible until something fails to move
                    bus.torque(sid, True)
                check("torque enabled on all three", OK)
            finally:
                bus.close()
        except Exception as e:
            check("servo bus", FAIL, str(e).replace("\n", " | "))

    if a.cam is not None:
        print("\n--- camera ---")
        try:
            import cv2
            cap = cv2.VideoCapture(a.cam)
            if not cap.isOpened():
                check(f"camera {a.cam}", FAIL, "will not open; try --list-cams")
            else:
                ok, fr = cap.read()
                if not ok:
                    check(f"camera {a.cam}", FAIL, "opens but yields no frame")
                else:
                    # Resolution does not identify it -- the built-in FaceTime
                    # camera is also 1080p. The OV4688 does 1440p and the built-in
                    # does not, so ask for 1440p and see who complies.
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 2560)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1440)
                    ok2, fr2 = cap.read()
                    w = fr2.shape[1] if ok2 else fr.shape[1]
                    h = fr2.shape[0] if ok2 else fr.shape[0]
                    if abs(w - h) < 0.15 * max(w, h):
                        check(f"camera {a.cam}", WARN,
                              f"{w}x{h} square -- that is the Mac's built-in "
                              f"Center Stage sensor, not the head cam. --list-cams")
                    elif w >= 2560:
                        check(f"camera {a.cam}", OK, f"{w}x{h} -- head cam")
                    else:
                        check(f"camera {a.cam}", WARN,
                              f"{w}x{h} -- did not take the 2K mode; may not be "
                              f"the head cam. --list-cams to check")
            cap.release()
        except Exception as e:
            check("camera", FAIL, str(e))

    fails = [r for r in results if r[0] == FAIL]
    warns = [r for r in results if r[0] == WARN]
    print("\n" + "=" * 60)
    if fails:
        print(f"NO-GO -- {len(fails)} failure(s):")
        for _, lbl, d in fails:
            print(f"  {lbl}: {d}")
    else:
        print("GO" + (f" -- with {len(warns)} warning(s)" if warns else ""))
        for _, lbl, d in warns:
            print(f"  warn {lbl}: {d}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
