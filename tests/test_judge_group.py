"""One API call carries every card and all five frames.

That property is why the group judge exists -- N coincident cards used to mean N
requests -- and it survived the 2026-08-05 rewrite that changed what is being
asked inside that one call. See test_judge_prompt.py for the rewrite itself.
"""
from planning import judge as judge_module


def test_candidate_group_uses_one_call_for_all_cards_and_five_frames(monkeypatch):
    calls = []

    def fake_call(prompt, schema, **kwargs):
        calls.append((prompt, schema, kwargs))
        return ({"pass": True,
                 "describe": "Someone is handling the cup at the table."}, "{}")

    monkeypatch.delenv("SECONDATTN_OFFLINE", raising=False)
    monkeypatch.setattr(judge_module, "call_json", fake_call)
    frames = [f"frame-{i}".encode() for i in range(5)]
    entries = [
        {"all": [1], "on": "cup", "label": "looking at cup"},
        {"all": [9], "on": "cup", "label": "holding cup"},
    ]

    result = judge_module.judge_candidate_group(
        frames, entries, judge_module.ReportabilityTaste())

    assert len(calls) == 1, "two cards must not become two requests"
    assert calls[0][2]["images"] == frames
    # Derived from EVENT_OFFSETS_S, not typed out. Typed out, this list and the
    # sentence in the prompt drifted apart from the window they describe and
    # stayed wrong for months -- see test_judge_cannot_check_left_from_right.
    from planning.judge import _frame_labels
    assert calls[0][2]["labels"] == _frame_labels(5, event_window=True)
    # The labels go in as a one-line hint now, not as a JSON block of claims to
    # adjudicate -- but both cards must still be named, or the model is judging
    # five frames with no idea which part matters.
    assert "looking at cup" in calls[0][0]
    assert "holding cup" in calls[0][0]

    assert result["confirmed"] is True
    # ONE verdict on the moment; every card that pointed at it inherits it.
    assert len(result["candidate_results"]) == 2
    assert all(r["confirmed"] for r in result["candidate_results"])
    # One relation each, so neither is more specific -- the tie goes to CV's own
    # order, which order_coincident_candidates has already decided.
    assert result["selected_index"] == 0
    assert result["note"] == "Someone is handling the cup at the table."


def test_a_failed_moment_confirms_nothing(monkeypatch):
    def fake_call(*args, **kwargs):
        return ({"pass": False, "describe": ""}, "{}")

    monkeypatch.delenv("SECONDATTN_OFFLINE", raising=False)
    monkeypatch.setattr(judge_module, "call_json", fake_call)
    result = judge_module.judge_candidate_group(
        [b"image"], [{"all": [1], "label": "gaze"}],
        judge_module.ReportabilityTaste())

    assert result["confirmed"] is False
    assert result["selected_index"] == -1
    assert result["worth"] == 0.0
    assert result["candidate_results"][0]["confirmed"] is False


def test_a_judge_error_fails_closed(monkeypatch):
    """A call that raises must not leave the moment looking confirmed -- S7 is
    driven off `confirmed`, so failing open would move the robot on nothing."""
    def boom(*args, **kwargs):
        raise RuntimeError("503 unavailable")

    monkeypatch.delenv("SECONDATTN_OFFLINE", raising=False)
    monkeypatch.setattr(judge_module, "call_json", boom)
    result = judge_module.judge_candidate_group(
        [b"image"] * 5, [{"all": [1], "label": "gaze"}],
        judge_module.ReportabilityTaste())

    assert result["confirmed"] is False
    assert result["selected_index"] == -1
    assert "503" in result["candidate_results"][0]["reason"]
