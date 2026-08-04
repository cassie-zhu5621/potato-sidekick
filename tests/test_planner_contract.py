from planning.planner import output_schema, validate


def _spec():
    return {
        "watch": [{"all": [9], "on": "laptop", "within_s": 2, "label": "use laptop"}],
        "seen": ["person", "laptop"],
        "boxes": [{"label": "laptop", "tier": "focus", "view_index": 2,
                   "box": [0.1, 0.2, 0.8, 0.9]}],
        "detect": ["person", "laptop"], "focus": ["laptop"],
        "single_ok": [], "duration_s": 600, "why": "the user asked", "missing": None,
    }


def test_independent_view_box_contract_is_valid():
    assert validate(_spec()) == []
    box_schema = output_schema()["properties"]["boxes"]["items"]
    assert "view_index" in box_schema["required"]


def test_out_of_range_box_is_rejected():
    spec = _spec()
    spec["boxes"][0]["box"][3] = 56.0
    assert any("numbers in [0,1]" in error for error in validate(spec))
