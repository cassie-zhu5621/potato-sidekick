"""
cam_test.py — sanity-check the camera's LIVE stream ALONE (no detection / gate / VLM).

The M5 UnitCam S3 already serves a CONTINUOUS motion-JPEG stream at http://<ip>/ ; the old
collector merely SAMPLED it every few seconds. This reads it continuously so you can confirm
(a) it streams at all, (b) the resolution, and (c) the real frame rate — before adding the
heavier pipeline. Auto-reconnects if the stream drops (ESP32 cams stall sometimes).

    python cam_test.py --camera http://sidekick-cam.local     # the M5 over WiFi
    python cam_test.py --camera http://192.168.3.26           # or by IP
    python cam_test.py --webcam 0                              # a normal USB webcam
Press q to quit.
"""

from __future__ import annotations
import argparse, time
import cv2
import numpy as np


def mjpeg_frames(url):
    """Yield frames from an MJPEG stream, reconnecting on any drop."""
    import requests
    while True:
        try:
            s = requests.Session(); s.trust_env = False
            with s.get(url.rstrip("/") + "/", stream=True, timeout=(5, 15)) as r:
                print("[stream] connected")
                buf = b""
                for chunk in r.iter_content(8192):
                    buf += chunk
                    a = buf.find(b"\xff\xd8")
                    b = buf.find(b"\xff\xd9", a + 2) if a != -1 else -1
                    if a != -1 and b != -1:
                        jpg, buf = buf[a:b + 2], buf[b + 2:]
                        fr = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                        if fr is not None:
                            yield fr
        except Exception as e:
            print(f"[stream] dropped: {e} — reconnecting in 2s")
            time.sleep(2)


def webcam_frames(idx):
    cap = cv2.VideoCapture(idx)
    while cap.isOpened():
        ok, fr = cap.read()
        if ok:
            yield fr


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--camera", help="MJPEG URL, e.g. http://sidekick-cam.local")
    g.add_argument("--webcam", type=int)
    args = ap.parse_args()

    src = mjpeg_frames(args.camera) if args.camera else webcam_frames(args.webcam)
    n, t0, fps = 0, time.time(), 0.0
    print("reading stream — q to quit")
    for fr in src:
        n += 1
        if time.time() - t0 >= 1.0:
            fps = n / (time.time() - t0); n = 0; t0 = time.time()
            print(f"  {fps:4.1f} fps   {fr.shape[1]}x{fr.shape[0]}")
        cv2.putText(fr, f"{fps:.1f} fps  {fr.shape[1]}x{fr.shape[0]}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(fr, f"{fps:.1f} fps  {fr.shape[1]}x{fr.shape[0]}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
        cv2.imshow("cam test", fr)
        if (cv2.waitKey(1) & 0xFF) == ord("q"):
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
