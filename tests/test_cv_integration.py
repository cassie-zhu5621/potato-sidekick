#!/usr/bin/env python3
"""Real-model test; run explicitly with CV_TEST_IMAGE set."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from perception.cv_smoke_test import run_smoke


@pytest.mark.integration
@pytest.mark.parametrize("detector_kind", ["gdino", "yoloworld"])
def test_open_vocab_detector_mediapipe_one_image(tmp_path, detector_kind):
    image = os.environ.get("CV_TEST_IMAGE")
    if not image:
        pytest.skip("set CV_TEST_IMAGE to a local image with a visible person and face")
    image_path = Path(image)
    assert image_path.is_file(), image_path

    report = run_smoke(
        image_path,
        tmp_path,
        vocabulary=["person", "space suit", "helmet", "space shuttle"],
        conf=0.20,
        detector_kind=detector_kind,
    )
    labels = [d["label"] for d in report["detector"]["detections"]]
    assert "person" in labels
    assert report["mediapipe"]["pose_count"] >= 1
    assert report["mediapipe"]["face_count"] >= 1
    assert Path(report["outputs"]["overlay"]).is_file()
    assert Path(report["outputs"]["report"]).is_file()
    for det in report["detector"]["detections"]:
        x1, y1, x2, y2 = det["box"]
        assert 0.0 <= det["score"] <= 1.0
        assert x2 > x1 and y2 > y1
