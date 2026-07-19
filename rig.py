"""
rig.py — hardware adapter for the pan-tilt sidekick. The ONLY hardware-specific file.

Two links, kept separate (this is the whole point):
  * CAMERA  : M5 UnitCam S3, streams motion-JPEG over WiFi at http://<ip>/  (one viewer!).
              get_frame() pulls from it — NOT a USB webcam.
  * SERVOS  : Arduino Uno R4 + 2x MG90S over USB serial, firmware = pantilt_r4.ino.
              move_to(pan,tilt) sends one "dPan,dTilt\\n" line (degrees RELATIVE to centre);
              the board eases to it and auto-relaxes when idle.

robot_demo.run() only ever calls move_to() and get_frame(); everything else is generic.

>>> ADJUST FOR YOUR RIG (search "ADJUST") <<<
  1. CAM_URL       — the M5's MJPEG URL (from the Arduino serial monitor on boot)
  2. SERIAL_PORT   — the servo board's port (mac: ls /dev/tty.*  -> /dev/tty.usbmodemXXXX)
  3. PAN/TILT_RANGE— must stay inside the firmware's clamp (pan +-60, tilt -35..+35)
  4. TILT_TRIM     — per-placement tilt offset so "0" looks level, not at the ceiling/floor

Standalone test (no brain):  python rig.py --camera http://<ip>/ --port /dev/tty.usbmodemXXXX
"""

from __future__ import annotations
import argparse, threading, time
import cv2
import numpy as np

# ---------------------------------------------------------------------------
# CONVENTION used everywhere (incl. robot_demo): +pan looks RIGHT, +tilt looks DOWN, 0 = level.
# The constants below map that convention onto THIS rig's wiring/mounting.
# ADJUST 1-2
import os as _os
CAM_URL     = _os.environ.get("SIDEKICK_CAM",      # per-session override: export SIDEKICK_CAM=http://<ip>/
                              "http://172.20.10.2/")  # ADJUST 1 (M5 MJPEG URL — changes with the network)
SERIAL_PORT = _os.environ.get("SIDEKICK_PORT", "/dev/cu.usbmodem101")  # ADJUST 2 (cu.* on mac)
BAUD        = 115200                        # match pantilt_r4.ino

# Calibration (measured on the lab rig):
PAN_SIGN    = -1        # servo pan is mirrored -> flip so +pan command physically looks RIGHT
TILT_TRIM   = 30        # camera is mounted tilted UP ~30deg -> add 30 so logical 0 = level
PAN_LIMIT   = 60        # firmware clamps pan to +-60 (deg from centre)
TILT_LIMIT  = 45        # firmware now clamps tilt to +-45 (TILT_MAX raised to 135); +30 trim leaves ~+15 down
PAN_RANGE   = (-60, 60)                     # logical sweep range for the standalone test
TILT_RANGE  = (-30, 12)                     # logical: 0=level, +down (now ~12deg below level reachable)
SETTLE_S    = 0.35                          # extra wait after the glide, for shake to die down
GLIDE_S_PER_DEG = 0.013                     # pantilt_r4.ino eases ~12ms/deg; we wait for it
# ---------------------------------------------------------------------------


class _MJPEGGrabber:
    """Background reader holding only the LATEST M5 frame (+ a seq counter), with reconnect.
    seq lets get_frame() wait for a frame that arrived AFTER a move, so we never judge a
    stale frame from during the head's motion."""
    def __init__(self, url):
        self.url = url.rstrip("/") + "/"
        self.latest = None
        self.seq = 0
        self.stopped = False

    def _run(self):
        import requests
        while not self.stopped:
            try:
                s = requests.Session(); s.trust_env = False
                with s.get(self.url, stream=True, timeout=(5, 15)) as r:
                    buf = b""
                    for chunk in r.iter_content(8192):
                        if self.stopped:
                            return
                        buf += chunk
                        a = buf.find(b"\xff\xd8")
                        b = buf.find(b"\xff\xd9", a + 2) if a != -1 else -1
                        if a != -1 and b != -1:
                            jpg, buf = buf[a:b + 2], buf[b + 2:]
                            fr = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                            if fr is not None:
                                self.latest = fr; self.seq += 1
            except Exception as e:
                if not self.stopped:
                    print(f"[stream] dropped: {e} — reconnecting in 2s"); time.sleep(2)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self


class ServoOnlyRig:
    """Pan-tilt servos WITHOUT the camera link — move_to/nod/beep over serial only.
    Use when frames come from elsewhere (USB webcam mounted on the head)."""

    def __init__(self, port=SERIAL_PORT, baud=BAUD):
        import serial
        self.pan = 0.0
        self.tilt = 0.0
        # If the configured port is gone (Mac renames it when the USB socket changes),
        # auto-pick the first /dev/cu.usbmodem* present.
        import glob, os
        if not os.path.exists(port):
            found = sorted(glob.glob("/dev/cu.usbmodem*"))
            if not found:
                raise RuntimeError(f"servo port {port} not found and no /dev/cu.usbmodem* "
                                   "present — is the R4 plugged in? (close Serial Monitor too)")
            print(f"[rig] {port} missing -> using {found[0]}")
            port = found[0]
        self.ser = serial.Serial(port, baud, timeout=1)
        time.sleep(2)                               # R4 resets when the port opens

    def move_to(self, pan: float, tilt: float):
        """Logical pose: +pan=right, +tilt=down, 0=level. Maps to firmware via sign+trim."""
        dist = max(abs(pan - self.pan), abs(tilt - self.tilt))   # glide time ~ logical delta
        cmd_pan  = max(-PAN_LIMIT,  min(PAN_LIMIT,  PAN_SIGN * pan))
        cmd_tilt = max(-TILT_LIMIT, min(TILT_LIMIT, tilt + TILT_TRIM))
        self.ser.write(f"{int(round(cmd_pan))},{int(round(cmd_tilt))}\n".encode())  # board eases there
        self.pan, self.tilt = pan, tilt                          # remember LOGICAL pose
        time.sleep(dist * GLIDE_S_PER_DEG + SETTLE_S)            # wait out the glide

    def beep(self):
        """'Noticed' chirp on the rig's passive buzzer (firmware 'beep' command; D8)."""
        try:
            self.ser.write(b"beep\n")
        except Exception:
            pass

    def nod(self, depth=8, times=1):
        """Legible 'noticed' — chirp + a small tilt dip and back. Sound fires FIRST so the
        cue lands even when eyes are on the screen, then the head dips (cue trio: turn
        happened earlier, now beep + nod)."""
        self.beep()
        base = self.tilt
        for _ in range(times):
            self.move_to(self.pan, base + depth); self.move_to(self.pan, base)  # +tilt = dip DOWN

    def close(self):
        try:
            self.ser.write(b"off\n"); self.ser.close()
        except Exception:
            pass


class GimbalRig(ServoOnlyRig):
    """Servos + the M5 WiFi camera: move_to/nod/beep inherited, plus get_frame()."""

    def __init__(self, cam_url=CAM_URL, port=SERIAL_PORT, baud=BAUD):
        # CAMERA FIRST: confirm the M5 streams before we grab the serial port, so a camera
        # problem never leaves the port held busy (saves a manual kill next run).
        self.grab = _MJPEGGrabber(cam_url).start()
        t0 = time.time()
        while self.grab.latest is None and time.time() - t0 < 15:
            time.sleep(0.1)
        if self.grab.latest is None:
            self.grab.stopped = True
            raise RuntimeError(f"No frames from {cam_url} — is the M5 streaming on this network? "
                               f"(check the IP + that you're on the same WiFi/hotspot; one viewer only)")
        super().__init__(port, baud)

    def get_frame(self):
        """Return a frame captured AFTER this call (post-move), not a stale one."""
        start_seq = self.grab.seq
        t0 = time.time()
        while self.grab.seq <= start_seq and time.time() - t0 < 5:
            time.sleep(0.02)
        if self.grab.latest is None:
            raise RuntimeError("Lost the M5 stream")
        return self.grab.latest.copy()

    def close(self):
        self.grab.stopped = True
        try:
            self.ser.write(b"off\n"); self.ser.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
def sweep_positions(pan_steps=5, tilt_steps=3):
    pans  = [PAN_RANGE[0]  + (PAN_RANGE[1]  - PAN_RANGE[0])  * i / (pan_steps  - 1) for i in range(pan_steps)]
    tilts = [TILT_RANGE[0] + (TILT_RANGE[1] - TILT_RANGE[0]) * j / (tilt_steps - 1) for j in range(tilt_steps)]
    return [(p, t) for t in tilts for p in pans]


if __name__ == "__main__":
    # Standalone rig test: sweep the grid, grab an M5 frame at each pose, save it. Confirms
    # BOTH links (servo move + WiFi grab) before wiring the brain. q-less; runs one sweep.
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", default=CAM_URL)
    ap.add_argument("--port", default=SERIAL_PORT)
    args = ap.parse_args()
    rig = GimbalRig(cam_url=args.camera, port=args.port)
    try:
        for i, (pan, tilt) in enumerate(sweep_positions()):
            rig.move_to(pan, tilt)
            fr = rig.get_frame()
            cv2.imwrite(f"sweep_{i:02d}_p{int(pan)}_t{int(tilt)}.jpg", fr)
            print(f"saved sweep_{i:02d}  pan={pan:.0f} tilt={tilt:.0f}  {fr.shape[1]}x{fr.shape[0]}")
        rig.move_to(0, 0)
    finally:
        rig.close()
    print("Done — check sweep_*.jpg. If the head moved AND the frames look right, the rig works.")
