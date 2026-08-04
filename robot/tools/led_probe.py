#!/usr/bin/env python3
"""
Smallest possible LED test: raw bytes to the port, nothing else in the way.

Exists because `clip_player --led-test` sits on top of find_cores3, CoreS3Link,
a reader thread and argparse -- any of which can be the thing that is broken.
This is pyserial and a write().

  python3 -m robot.tools.led_probe /dev/cu.usbmodem1201

What you should see, in order:
  IN PONG cores3_sidekick v2     the firmware is alive and parsing EVT
  antenna goes RED, then GREEN, then back to WARM
  antenna steps dark -> full -> dark, held long enough to be unmissable

How to read the result:
  PONG, hue changes, level steps      everything works
  PONG, hue changes, level does NOT   EVT LED / setAntennaLevel is the problem
  PONG, NOTHING on the antenna        the board is fine, the LED is not:
                                      Grove lead on CLK=8/DATA=9? USE_ANTENNA?
                                      is leds.init() ever called?
  no PONG                             wrong port, or firmware without EVT PING
"""
import os
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial not installed: pip install pyserial")


def main():
    if len(sys.argv) > 1:
        port = sys.argv[1]
    else:
        import glob
        servo = os.environ.get("NOTICEBOT_PORT")
        cands = [p for p in sorted(glob.glob("/dev/cu.usbmodem*")) if p != servo]
        if len(cands) != 1:
            sys.exit(f"pass the port explicitly; candidates: {cands or 'none'}")
        port = cands[0]

    servo = os.environ.get("NOTICEBOT_PORT")
    if servo and os.path.realpath(port) == os.path.realpath(servo):
        sys.exit(f"{port} is NOTICEBOT_PORT -- that is the servo bus, not the CoreS3.")

    print(f"port {port}")
    with serial.Serial(port, 115200, timeout=0.2) as s:
        time.sleep(0.3)
        s.reset_input_buffer()

        def send(line, hold=0.0):
            print(f"  -> {line}")
            s.write((line + "\n").encode())
            t0 = time.time()
            while time.time() - t0 < hold:
                # The board falls back to its own breathing after 500 ms of
                # silence, so anything meant to be HELD has to be repeated.
                s.write((line + "\n").encode())
                time.sleep(0.2)

        send("EVT PING")
        t0, buf = time.time(), b""
        while time.time() - t0 < 1.5:
            buf += s.read(128)
            if b"\n" in buf:
                break
        reply = buf.decode("utf-8", "ignore").strip()
        print(f"  <- {reply or '(nothing)'}")
        if "cores3" not in reply.lower():
            print("\n  no PONG. Wrong port, or firmware predates EVT PING.")
            return

        print("\nhue: watch the antenna colour")
        for h in ("RED", "GREEN", "COOL", "WARM"):
            send(f"EVT HUE {h}")
            time.sleep(1.2)

        print("\nlevel: watch it step dark <-> full (each held 1.6 s)")
        for v in (0, 255, 0, 255):
            send(f"EVT LED {v}", hold=1.6)

        send("EVT REST")
        print("\ndone.")


if __name__ == "__main__":
    main()
