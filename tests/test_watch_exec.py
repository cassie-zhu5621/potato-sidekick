from types import SimpleNamespace

from perception.watch_exec import WatchExecutor
from planning.spec_utils import _focus_ok


def _sequence(order):
    ex = WatchExecutor(
        {"watch": [{"then": [10, 6], "within_s": 5, "label": "arrival then chat"}]},
        persist=1, cooldown=10, tau_gap=1,
    )
    fired = []
    for t, rid in enumerate(order):
        truth = {i: False for i in range(1, 12)}
        truth[rid] = True
        new, _ = ex.step(truth, float(t) * 2.0)
        fired.extend(new)
    return fired


def test_then_requires_the_requested_order():
    assert [e["label"] for e in _sequence([10, 6])] == ["arrival then chat"]
    assert _sequence([6, 10]) == []


def test_then_does_not_degrade_to_simultaneous_and():
    ex = WatchExecutor(
        {"watch": [{"then": [10, 6], "within_s": 5, "label": "sequence"}]},
        persist=1, tau_gap=1,
    )
    truth = {i: i in (6, 10) for i in range(1, 12)}
    fired, _ = ex.step(truth, 0.0)
    assert fired == []


def test_hands_on_focus_uses_the_actual_target_label():
    entry = {"all": [9], "on": "laptop"}
    viz = {"dets": [], "handson": [(1, "cup")]}
    assert not _focus_ok(entry, viz, {"laptop"})
    viz["handson"] = [(1, "laptop")]
    assert _focus_ok(entry, viz, {"laptop"})


def test_single_ok_does_not_duplicate_an_exact_watch_entry():
    ex = WatchExecutor({
        "watch": [{"all": [9], "on": "cup", "label": "holding cup"}],
        "single_ok": [9],
    })
    assert [entry["label"] for entry in ex.entries] == ["holding cup"]


def test_lean_focus_ignores_unrelated_gaze_hit():
    entry = {"all": [8], "on": "laptop"}
    viz = {
        "dets": [SimpleNamespace(label="laptop"), SimpleNamespace(label="cup")],
        "hits": [("gazing-at", {"det": 0}), ("lean-in", {"det": 1})],
    }
    assert not _focus_ok(entry, viz, {"laptop"})
