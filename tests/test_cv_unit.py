#!/usr/bin/env python3
"""Fast CV contract tests: no checkpoint loading, camera or network."""
from __future__ import annotations

import os
import sys
from types import MethodType, SimpleNamespace

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from perception.perceive import (
    Detection,
    GroundingDinoDetector,
    MockDetector,
    StaticLatch,
    _grounding_dino_detections,
    _normalise_vocabulary,
    build_graph,
    make_detector,
)


def test_detection_geometry_contract():
    det = Detection("cup", (10, 20, 30, 60), 0.75)
    assert det.cx == 20
    assert det.cy == 40
    assert det.w == 20
    assert det.h == 40
    assert det.area == 800


def test_vocabulary_preserves_multiword_labels_and_order():
    assert _normalise_vocabulary([" Person ", "cell   phone", "person", ""]) == [
        "person", "cell phone"
    ]


def test_grounding_dino_text_labels_keep_full_phrase():
    result = {
        "boxes": [[1, 2, 30, 40]],
        "scores": [0.91],
        "text_labels": ["cell phone"],
    }
    assert _grounding_dino_detections(result, ["person", "cell phone"]) == [
        Detection("cell phone", (1.0, 2.0, 30.0, 40.0), 0.91)
    ]


def test_grounding_dino_numeric_label_maps_back_to_vocabulary():
    result = {"boxes": [[0, 1, 2, 3]], "scores": [0.7], "labels": [1]}
    detections = _grounding_dino_detections(result, ["person", "robot arm"])
    assert detections[0].label == "robot arm"


def test_mock_factory_needs_no_ml_dependencies():
    detector = make_detector("mock", ["person"])
    assert isinstance(detector, MockDetector)
    assert detector.detect(None) == []


def test_grounding_dino_mps_failure_retries_once_on_cpu():
    class FakeModel:
        def __init__(self):
            self.moves = []

        def to(self, device):
            self.moves.append(device)
            return self

    detector = GroundingDinoDetector.__new__(GroundingDinoDetector)
    detector.device = "mps"
    detector.model = FakeModel()
    calls = []

    def fake_detect(self, _pil, _image_hw, _torch):
        calls.append(self.device)
        if self.device == "mps":
            raise RuntimeError("unsupported MPS operator")
        return [Detection("person", (0, 0, 2, 2), 0.9)]

    detector._detect = MethodType(fake_detect, detector)
    result = detector.detect(np.zeros((2, 2, 3), dtype=np.uint8))
    assert calls == ["mps", "cpu"]
    assert detector.model.moves == ["cpu"]
    assert result[0].label == "person"


def test_yolo_world_factory_keeps_dynamic_vocabulary(monkeypatch):
    class FakeYOLO:
        def __init__(self, weights):
            self.weights = weights
            self.classes = []

        def set_classes(self, vocabulary):
            self.classes = list(vocabulary)

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYOLO))
    detector = make_detector("yoloworld", [" Person ", "cell   phone"])
    assert detector.model_name == "yolov8s-world.pt"
    assert detector.model.classes == ["person", "cell phone"]
    assert detector.vocab == ["person", "cell phone"]

    detector.set_vocab(["robot arm", "person", "robot arm"])
    assert detector.model.classes == ["robot arm", "person"]
    assert detector.vocab == ["robot arm", "person"]


def test_scene_graph_filters_low_confidence_and_keeps_inside_relation():
    graph = build_graph([
        Detection("coin", (20, 20, 30, 30), 0.9),
        Detection("box", (10, 10, 50, 50), 0.9),
        Detection("noise", (70, 70, 90, 90), 0.1),
    ], (100, 100), min_score=0.3)
    assert "noise1" not in graph.nodes
    assert ("coin1", "inside", "box1") in graph.edges


def test_static_latch_reinjects_temporarily_missed_furniture():
    latch = StaticLatch(forget=2)
    chair = Detection("chair", (10, 10, 40, 60), 0.8)
    assert latch.apply([chair], (100, 100)) == [chair]
    assert latch.apply([], (100, 100)) == [chair]
    assert latch.apply([], (100, 100)) == [chair]
    assert latch.apply([], (100, 100)) == []
