#!/usr/bin/env python3
"""
touch_test.py — the TTP223 body-tap sensor, on its own.

The question this answers: does a tap register THROUGH the printed shell, and
does it register only when someone actually taps?

Nothing else runs. No servo bus is opened, no camera, no models -- so a result
here is about the sensor and the shell, and cannot be anything else.

  python3 robot/tools/touch_test.py                      # auto-detect the CoreS3
  python3 robot/tools/touch_test.py /dev/cu.usbmodem1201
  python3 robot/tools/touch_test.py --quiet              # no beep (silent room)
  python3 robot/tools/touch_test.py --idle 60            # false-positive watch

FEEDBACK IS ON THE ROBOT, NOT THE SCREEN. Every accepted tap flashes the antenna
green and beeps, because while you are feeling around a shell for the sweet spot
your eyes are on your hand -- not on a terminal behind you. The terminal log is
for afterwards.

THE ONE GOTCHA THAT WASTES AN HOUR. The TTP223 self-calibrates its baseline at
power-on. If anything is resting against the pad when the board boots -- your
hand, a cable, the shell pressed hard against it -- that becomes "untouched",
and it will then feel dead. If it never triggers, unplug the CoreS3, move your
hands away, plug it back in, and try again BEFORE changing anything else.
"""
import argparse, os, statistics, sys, time

# Run me directly: the repo root is two levels up from robot/tools/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from session.cores3_link import CoreS3Link, find_cores3


def main():
    ap = argparse.ArgumentParser(description="TTP223 body tap, nothing else")
    ap.add_argument("port", nargs="?", default=os.environ.get("NOTICEBOT_CORES3"),
                    help="the CoreS3 port; omitted = auto-detect")
    ap.add_argument("--quiet", action="store_true", help="no beep, LED only")
    ap.add_argument("--idle", type=float, default=0.0,
                    help="watch for N seconds and report any tap as a FALSE "
                         "POSITIVE. Run this with nobody touching the robot: a "
                         "sensor that fires on its own is worse than one that "
                         "never fires, because it puts the robot into S6 in the "
                         "middle of somebody's task.")
    a = ap.parse_args()

    port = a.port
    if not port:
        port = find_cores3()
        if not port:
            sys.exit("no CoreS3 found. Pass the port:\n"
                     "  ls /dev/cu.*\n"
                     "  python3 robot/tools/touch_test.py /dev/cu.usbmodemXXXX")

    taps = []
    t0 = time.time()

    def on_line(s):
        if "BODYTAP" not in s:
            if "HELLO" in s or "PONG" in s:
                print(f"  [{s}]")
            return
        t = time.time()
        taps.append(t)
        gap = f"{t - taps[-2]:5.2f}s since last" if len(taps) > 1 else "first"
        print(f"  TAP #{len(taps):<3} at {t - t0:6.2f}s   ({gap})")
        try:
            link.hue("GREEN")
            link.led(255)
            if not a.quiet:
                link.sfx("ACK")
            time.sleep(0.12)
            link.led(20)
        except Exception:
            pass

    link = CoreS3Link(port, on_input=on_line)
    link.ui("idle")
    link.hue("COOL")
    link.led(20)

    if a.idle:
        print(f"\nFALSE-POSITIVE WATCH: {a.idle:.0f}s. Do not touch the robot.\n")
        time.sleep(a.idle)
        n = len(taps)
        print(f"\n  {n} tap(s) in {a.idle:.0f}s with nobody touching it")
        print("  0        -> clean" if n == 0 else
              "  1 or 2   -> marginal; check the SIG wire routing and that VCC "
              "is on 3V3, not 5V" if n <= 2 else
              "  many     -> the pad is picking up something. Move the wire away "
              "from the servo loom;\n"
              "              motor current is the usual culprit.")
        link.rest(); link.close()
        return

    print(f"\nconnected: {port}")
    print("Tap the shell. Each accepted tap flashes GREEN and beeps.\n"
          "Ctrl-C when done.\n")
    print("  Worth trying in this order, so you learn WHY it fails:")
    print("    1. touch the bare sensor pad      -> is the sensor alive at all")
    print("    2. through the shell, over the pad -> is the shell too thick")
    print("    3. through the shell, 1-2 cm off   -> how big is the sweet spot")
    print("    4. a slow press vs a quick tap     -> the firmware wants a rising")
    print("       edge with 250 ms of debounce, so a slow press still counts once,")
    print("       but resting a hand there does NOT keep re-triggering.\n")

    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass

    dur = time.time() - t0
    print(f"\n\n--- {len(taps)} tap(s) in {dur:.0f}s ---")
    if len(taps) > 1:
        gaps = [b - a_ for a_, b in zip(taps, taps[1:])]
        print(f"  gap between taps: min {min(gaps):.2f}s  "
              f"median {statistics.median(gaps):.2f}s  max {max(gaps):.2f}s")
        if min(gaps) < 0.30:
            print("  !! a gap under 0.30s means one physical tap registered twice.")
            print("     The firmware debounce is 250 ms (checkTap, TAP_PIN 17);")
            print("     raise it if this happens often.")
    print("\nIf it never fired through the shell but fires on the bare pad:")
    print("  * stick copper tape on the sensor pad, the size of the area you want")
    print("    touchable. A bigger electrode is the standard fix and it costs")
    print("    nothing -- sensitivity falls off fast with dielectric thickness.")
    print("  * make sure the shell is actually TOUCHING the sensor. An air gap")
    print("    behind the wall is worse than more plastic.")
    link.rest()
    link.close()


if __name__ == "__main__":
    main()
