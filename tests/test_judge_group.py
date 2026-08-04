from planning import judge as judge_module


def _axes(value=0.5):
    return {name: value for name in judge_module.AXES}


def test_candidate_group_uses_one_call_for_all_cards_and_five_frames(monkeypatch):
    calls = []

    def fake_call(prompt, schema, **kwargs):
        calls.append((prompt, schema, kwargs))
        return ({
            "axes": _axes(),
            "candidates": [
                {"index": 0, "confirmed": True, "reason": "visible gaze"},
                {"index": 1, "confirmed": True, "reason": "visible hand contact"},
            ],
            "selected_index": 1,
            "note": "Both are visible; manipulation is more specific.",
            "feedback": "I can see someone handling the cup.",
        }, "{}")

    monkeypatch.delenv("SECONDATTN_OFFLINE", raising=False)
    monkeypatch.setattr(judge_module, "call_json", fake_call)
    frames = [f"frame-{i}".encode() for i in range(5)]
    entries = [
        {"all": [1], "on": "cup", "label": "looking at cup"},
        {"all": [9], "on": "cup", "label": "holding cup"},
    ]

    result = judge_module.judge_candidate_group(
        frames, entries, judge_module.ReportabilityTaste())

    assert len(calls) == 1
    assert calls[0][2]["images"] == frames
    assert calls[0][2]["labels"] == [
        "t-1.0s", "t-0.5s", "t0_onset", "t+0.5s", "t+1.0s"]
    assert '"label": "looking at cup"' in calls[0][0]
    assert '"label": "holding cup"' in calls[0][0]
    assert result["confirmed"] is True
    assert result["selected_index"] == 1
    assert len(result["candidate_results"]) == 2


def test_candidate_group_rejects_invalid_or_unconfirmed_selection(monkeypatch):
    def fake_call(*args, **kwargs):
        return ({
            "axes": _axes(),
            "candidates": [
                {"index": 0, "confirmed": False, "reason": "not visible"},
            ],
            "selected_index": 0,
            "note": "No supported card.",
            "feedback": "",
        }, "{}")

    monkeypatch.delenv("SECONDATTN_OFFLINE", raising=False)
    monkeypatch.setattr(judge_module, "call_json", fake_call)
    result = judge_module.judge_candidate_group(
        [b"image"], [{"all": [1], "label": "gaze"}],
        judge_module.ReportabilityTaste())

    assert result["confirmed"] is False
    assert result["selected_index"] == -1
    assert result["worth"] == 0.0
