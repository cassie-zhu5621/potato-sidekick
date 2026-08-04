#!/usr/bin/env python3
"""One-image integration smoke test for a real detector + MediaPipe.

This intentionally bypasses the robot, camera, planner and API services. It
answers one bring-up question: can this machine load the real local CV models,
run them on the same image, and produce inspectable evidence?

    .venv/bin/python perception/cv_smoke_test.py image.jpg \
        --vocab person,cup,laptop --output-dir session_feed/cv_smoke
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from perception.perceive import make_detector


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 2)


def _draw_label(cv2, image, text, origin, colour):
    x, y = origin
    cv2.putText(image, text, (max(0, int(x)), max(18, int(y))),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 2, cv2.LINE_AA)


def run_smoke(image_path, output_dir, vocabulary, conf=0.25, device=None,
              detector_kind="gdino"):
    """Run all real CV components once and return a JSON-serialisable report."""
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is missing; install requirements-cv.txt") from exc

    from perception.gaze import HeadPoseEstimator, draw_arrow, draw_pose_skeleton
    from perception.relations import PoseEstimator

    image_path = Path(image_path).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    frame = cv2.imread(str(image_path))
    if frame is None:
        raise ValueError(f"cannot read image: {image_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    overlay = frame.copy()

    timings = {}
    started = time.perf_counter()
    detector = make_detector(detector_kind, vocabulary, conf=conf, device=device)
    timings["detector_load_ms"] = _elapsed_ms(started)

    started = time.perf_counter()
    detections = detector.detect(frame)
    timings["detector_inference_ms"] = _elapsed_ms(started)

    pose = face = None
    try:
        started = time.perf_counter()
        pose = PoseEstimator()
        timings["pose_load_ms"] = _elapsed_ms(started)
        started = time.perf_counter()
        people = pose.estimate(frame)
        timings["pose_inference_ms"] = _elapsed_ms(started)

        started = time.perf_counter()
        face = HeadPoseEstimator()
        timings["face_load_ms"] = _elapsed_ms(started)
        started = time.perf_counter()
        rays = face.estimate(frame)
        timings["face_inference_ms"] = _elapsed_ms(started)
        face_count = face.last_face_count
    finally:
        if pose is not None:
            pose.close()
        if face is not None:
            face.close()

    for det in detections:
        x1, y1, x2, y2 = (int(round(v)) for v in det.box)
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (72, 204, 161), 3, cv2.LINE_AA)
        _draw_label(cv2, overlay, f"{det.label} {det.score:.2f}", (x1, y1 - 7),
                    (72, 204, 161))
    for person in people:
        if person.raw:
            draw_pose_skeleton(overlay, person.raw, (87, 225, 217), thick=4)
    diagonal = math.hypot(frame.shape[1], frame.shape[0])
    for ray in rays:
        draw_arrow(overlay, ray.origin, ray.point_at(0.25 * diagonal),
                   (234, 228, 136), thick=4)
        x1, y1, x2, y2 = (int(round(v)) for v in ray.face_box)
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (234, 228, 136), 2, cv2.LINE_AA)

    overlay_path = output_dir / "overlay.jpg"
    if not cv2.imwrite(str(overlay_path), overlay):
        raise RuntimeError(f"failed to write overlay: {overlay_path}")

    report = {
        "input": str(image_path),
        "image": {"width": int(frame.shape[1]), "height": int(frame.shape[0])},
        "detector": {
            "kind": detector_kind,
            "model": getattr(detector, "model_name", detector_kind),
            "device": getattr(detector, "device", device),
            "vocabulary": list(vocabulary),
            "detections": [
                {"label": d.label, "score": round(float(d.score), 6),
                 "box": [round(float(v), 2) for v in d.box]}
                for d in detections
            ],
        },
        "mediapipe": {
            "pose_count": len(people),
            "face_count": int(face_count),
            "head_ray_count": len(rays),
        },
        "timings_ms": timings,
        "outputs": {"overlay": str(overlay_path)},
    }
    report_path = output_dir / "result.json"
    report["outputs"]["report"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(
        description="run a detector + MediaPipe pose/face on one local image")
    parser.add_argument("image", help="local JPG/PNG test image")
    parser.add_argument("--vocab", default="person,cup,laptop,cell phone,chair",
                        help="comma-separated open-vocabulary detector labels")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--device", choices=["mps", "cpu", "cuda"], default=None)
    parser.add_argument("--detector", default="gdino",
                        choices=["gdino", "yoloworld", "yolo", "mock"])
    parser.add_argument("--output-dir", default="session_feed/cv_smoke")
    args = parser.parse_args()
    vocabulary = [part.strip() for part in args.vocab.split(",") if part.strip()]
    report = run_smoke(args.image, args.output_dir, vocabulary, conf=args.conf,
                       device=args.device, detector_kind=args.detector)
    labels = [d["label"] for d in report["detector"]["detections"]]
    print(json.dumps({
        "ok": True,
        "device": report["detector"]["device"],
        "detections": labels,
        "pose_count": report["mediapipe"]["pose_count"],
        "face_count": report["mediapipe"]["face_count"],
        "timings_ms": report["timings_ms"],
        "outputs": report["outputs"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
