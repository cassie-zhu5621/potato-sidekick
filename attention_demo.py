"""
attention_demo.py — FULL-SYSTEM test of the gaze/joint-attention branch (fixed camera).

The whole designed-relation pipeline, end to end:

    frame -> detector (object slots) + gaze/arm rays (direction primitive)
          -> 4 designed relations  (#5 gazing-at, #6 joint-attention, #4 pointing-at, eye-contact)
          -> RelationGate           (debounce flicker + habituate repeats — Gricean Quantity)
          -> VLM judge w/ CONFIRM   (verifies the cheap 2D claim against the image, then
                                     scores reportability + writes the field note)
          -> REPORT                 (banner + console + optional jsonl, ready for the rig's nod)

This is robot_demo's brain with the attention relations swapped in for the generic
structural gate; once it behaves on a fixed camera, the same step() drops into the
SCAN->LOOK loop (per-pose gates, orient to the gaze target = JOIN the attention).

Run:
    python attention_demo.py --offline                    # no API key, fake judge
    python attention_demo.py --offline --serve            # + web UI at http://localhost:8000
    export ANTHROPIC_API_KEY=sk-...
    python attention_demo.py --serve --save               # real VLM; feed persisted to disk
    python attention_demo.py --camera http://<ip>/ --detector gdino
"""

from __future__ import annotations
import argparse, json, math, os, threading, time
import cv2
import numpy as np

from perceive import Detection, SceneGraph, make_detector
from judge import judge as run_judge, ReportabilityTaste
from gaze import (HeadPoseEstimator, ArmRayEstimator, gazing_at, pointing_at,
                  joint_attention, eye_contact, draw_text, draw_box, draw_arrow,
                  draw_circle, draw_panel, C_GREEN, C_RED, C_YELLOW, C_ORANGE,
                  C_MAGENTA, C_CYAN, C_WHITE)


# --------------------------------------------------------------------------- #
# the gate: a relation INSTANCE must be stably present, then it fires ONCE
# --------------------------------------------------------------------------- #
class RelationGate:
    """key = (relation, subject_label, target_label), e.g. ("gazing-at","person","cup").

    persist  : consecutive frames the key must be present (debounce ray/detector flicker;
               TUNE from the flicker rates measured in TEST_PLAN_gaze.md).
    cooldown : seconds before the SAME key may fire again (habituation — same attention
               repeated is not news; Gricean Quantity).
    A key absent in a frame resets its streak: 'stably present' means consecutively."""

    def __init__(self, persist: int = 3, cooldown: float = 60.0):
        self.persist, self.cooldown = persist, cooldown
        self.streak: dict = {}
        self.fired_at: dict = {}

    def step(self, keys_present) -> list:
        now = time.time()
        keys_present = set(keys_present)
        for k in list(self.streak):
            if k not in keys_present:
                self.streak[k] = 0
        fired = []
        for k in keys_present:
            self.streak[k] = self.streak.get(k, 0) + 1
            if (self.streak[k] == self.persist
                    and now - self.fired_at.get(k, -1e9) >= self.cooldown):
                self.fired_at[k] = now
                fired.append(k)
        return fired


# --------------------------------------------------------------------------- #
# per-frame relation extraction -> {key: payload} for the gate + viz + judge
# --------------------------------------------------------------------------- #
def extract_relations(rays, arms, dets, wh, tol_deg=12.0):
    """Returns {key: payload}; payload = (claim text for VLM confirm, viz dict)."""
    rels = {}
    for h in gazing_at(rays, dets, tol_deg=tol_deg):
        t = dets[h["det"]].label
        rels[("gazing-at", "person", t)] = (
            f"a person gazing at the {t}",
            {"box": dets[h["det"]].box, "label": t, "color": C_GREEN})
    for h in pointing_at(arms, dets):
        t = dets[h["det"]].label
        rels[("pointing-at", "person", t)] = (
            f"a person pointing at / reaching toward the {t}",
            {"box": dets[h["det"]].box, "label": t, "color": C_ORANGE})
    for r in rays:
        if eye_contact(r):
            rels[("eye-contact", "person", "robot")] = (
                "a person looking directly at the camera (at the robot)",
                {"box": r.face_box, "label": "EYE CONTACT", "color": C_WHITE})
            break                                   # one eye-contact event per frame
    for c in joint_attention(rays, wh):
        # name the shared target if a detection box contains the convergence point
        px, py = c["point"]
        t = next((d.label for d in dets
                  if d.box[0] <= px <= d.box[2] and d.box[1] <= py <= d.box[3]), "something")
        rels[("joint-attention", "people", t)] = (
            f"two people looking at the same thing ({t})",
            {"point": c["point"], "label": t, "color": C_MAGENTA})
        break
    return rels


def frame_source(camera: str):
    """Yields frames. Webcam index -> VideoCapture. http URL -> the M5 MJPEG workflow
    (same as web_demo/rig.py): ONE requests connection, background reader, always hand
    over the FRESHEST frame — VideoCapture-on-URL would serve seconds-old buffered frames.
    Remember: the M5 serves ONE viewer at a time (close cam_test/browser streams first)."""
    if camera.isdigit():
        cap = cv2.VideoCapture(int(camera))
        if not cap.isOpened():
            raise SystemExit(f"cannot open webcam {camera}")
        while True:
            ok, fr = cap.read()
            if ok:
                yield fr
            else:
                time.sleep(0.05)
    else:
        import requests
        latest = [None]

        def reader():
            while True:                                  # auto-reconnect (hotspot IP hiccups)
                try:
                    s = requests.Session(); s.trust_env = False
                    with s.get(camera.rstrip("/") + "/", stream=True, timeout=(5, 15)) as r:
                        buf = b""
                        for ch in r.iter_content(8192):
                            buf += ch
                            i = buf.find(b"\xff\xd8")
                            j = buf.find(b"\xff\xd9", i + 2) if i != -1 else -1
                            if i != -1 and j != -1:
                                latest[0] = cv2.imdecode(
                                    np.frombuffer(buf[i:j + 2], np.uint8), 1)
                                buf = buf[j + 2:]
                except Exception as e:
                    print(f"[m5] stream dropped ({e}); reconnecting…")
                    time.sleep(1.0)

        threading.Thread(target=reader, daemon=True).start()
        t0 = time.time()                             # startup probe (same contract as rig.py):
        while latest[0] is None:                     # fail loud, not a silent black window
            if time.time() - t0 > 15:
                raise SystemExit(f"No frames from {camera} in 15s — is the M5 streaming on this "
                                 "network? (IP changes on the hotspot; ONE viewer at a time — "
                                 "close cam_test/robot_demo/browser streams first)")
            time.sleep(0.1)
        print(f"[camera] M5 stream live ({latest[0].shape[1]}x{latest[0].shape[0]})")
        seen = None
        while True:
            fr = latest[0]
            if fr is None or fr is seen:
                time.sleep(0.02); continue
            seen = fr
            yield fr


def mini_graph(key) -> SceneGraph:
    """One-edge scene graph so judge.relations_text() gets the structural grounding."""
    rel, a, b = key
    return SceneGraph(nodes={f"{a}1": a, f"{b}1": b},
                      edges=[(f"{a}1", rel.replace("-", "_"), f"{b}1")])


# --------------------------------------------------------------------------- #
# shared render + report publishing (used by this demo AND attention_robot.py)
# --------------------------------------------------------------------------- #
def render(fr, rays, arms, dets, rels, gate, persist, banner=None, status=""):
    """Draw the full neon overlay onto fr (in place) and return it."""
    H, W = fr.shape[:2]
    panel = []
    for d in dets:
        x1, y1, x2, y2 = map(int, d.box)
        cv2.rectangle(fr, (x1, y1), (x2, y2), (160, 160, 160), 1)
        draw_text(fr, d.label, (x1, y1 - 6), (200, 200, 200), 0.55, 1)
    for a in arms:
        draw_arrow(fr, a.origin, a.point_at(0.5 * math.hypot(W, H)), C_ORANGE)
    for r0 in rays:
        draw_arrow(fr, r0.origin, r0.point_at(0.6 * math.hypot(W, H)), C_YELLOW)
    for key, (claim, viz) in rels.items():
        armed = gate.streak.get(key, 0) >= persist
        cool = time.time() - gate.fired_at.get(key, -1e9) < gate.cooldown
        state = "seen" if cool and armed else f"{gate.streak.get(key, 0)}/{persist}"
        panel.append((f"{key[0].upper()}  {key[2]}  [{state}]", viz["color"]))
        if "box" in viz:
            draw_box(fr, viz["box"], viz["color"])
        else:
            draw_circle(fr, viz["point"], 22, viz["color"])
    draw_panel(fr, panel)
    if banner and time.time() < banner[0]:
        draw_text(fr, banner[1], (16, H - 52), banner[2], 0.95, 2)
    draw_text(fr, status or f"{len(rays)} face  {len(arms)} arm  {len(dets)} obj",
              (12, H - 16), C_WHITE, 0.65, 2)
    return fr


def publish(fr, rec, args, UI):
    """One confirmed REPORT -> disk (--save) + web feed (--serve, in-memory thumbs)."""
    H, W = fr.shape[:2]
    if args.save:
        os.makedirs(args.feed_dir, exist_ok=True)
        cv2.imwrite(os.path.join(args.feed_dir, rec["frame"]), fr)
        cv2.imwrite(os.path.join(args.feed_dir, rec["thumb"]),
                    cv2.resize(fr, (192, max(1, int(192 * H / W)))))
        with open(os.path.join(args.feed_dir, "attention_log.jsonl"), "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    if UI is not None:
        tjpg = cv2.imencode(".jpg", cv2.resize(fr, (192, max(1, int(192 * H / W)))))[1].tobytes()
        with UI.LOCK:
            UI.STATE["thumbs"][rec["thumb"]] = tjpg
            for old in list(UI.STATE["thumbs"])[:-60]:
                UI.STATE["thumbs"].pop(old, None)
            # full-resolution frame (the comic strip) viewable in the browser, last ~20 in memory
            UI.STATE.setdefault("frames", {})[rec["frame"]] = cv2.imencode(".jpg", fr)[1].tobytes()
            for old in list(UI.STATE["frames"])[:-20]:
                UI.STATE["frames"].pop(old, None)
            UI.STATE["feed"].append(rec)
            UI.STATE["feed"] = UI.STATE["feed"][-60:]


