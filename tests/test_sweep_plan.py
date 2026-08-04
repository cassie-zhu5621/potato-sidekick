import numpy as np

from planning.sweep_plan import Sweep


def test_sweep_sends_independent_images_and_maps_view_boxes(tmp_path):
    sweep = Sweep(feed_dir=str(tmp_path))
    sweep.begin()
    sweep.offer(np.zeros((100, 200, 3), dtype=np.uint8), -30)
    sweep.offer(np.full((100, 200, 3), 255, dtype=np.uint8), 30)
    received = {}

    def fake_plan(context, images):
        received["context"] = context
        received["images"] = images
        return {"spec": {
            "seen": ["person", "laptop"], "detect": ["person", "laptop"],
            "focus": ["laptop"],
            "boxes": [{"label": "laptop", "tier": "focus", "view_index": 1,
                       "box": [0.1, 0.2, 0.6, 0.8]}],
            "watch": [],
        }, "violations": []}

    _, meta = sweep.plan("watch the laptop", fake_plan)
    assert len(received["images"]) == 2
    assert all(isinstance(image, bytes) for image in received["images"])
    assert meta["richest_pan"] == 30
    assert meta["shots"][0]["dets"] == []
    assert meta["shots"][1]["dets"][0]["box"] == [20.0, 20.0, 120.0, 80.0]
