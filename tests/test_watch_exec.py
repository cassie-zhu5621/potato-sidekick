from types import SimpleNamespace

from perception.watch_exec import WatchExecutor, order_coincident_candidates
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


def test_the_cooldown_is_the_only_thing_holding_a_satisfied_entry_back():
    ex = WatchExecutor(
        {"watch": [{"all": [9], "label": "holding cup"}]},
        persist=1, cooldown=15, default_within=2,
    )
    on = {i: i == 9 for i in range(1, 12)}
    off = {i: False for i in range(1, 12)}

    fired, status = ex.step(on, 0.0)
    assert [entry["label"] for entry in fired] == ["holding cup"]
    assert status[0].cooldown_remaining_s == 15.0

    fired, status = ex.step(on, 5.0)
    assert fired == []
    assert status[0].cooldown_remaining_s == 10.0

    # THE COOLDOWN ENDS -> IT FIRES, whether or not the relation ever let go.
    #
    # This asserted the opposite until 2026-08-12: "holding through expiry must
    # NOT fire automatically; the user explicitly chose fresh-edge semantics"
    # (2026-08-04). Cassie reversed that decision on her reasoning about the
    # room -- an actor does not stand there repeating themselves, they finish and
    # leave, so the release requirement protected nothing real. What it did do
    # was let one accidental trigger by somebody sitting nearby latch the entry
    # for as long as their hand stayed put, locking out the genuine event at that
    # spot afterwards. The page showed it as "held . release to rearm".
    #
    # The break at t=8 below is therefore incidental now; see
    # test_a_hand_that_never_lets_go_still_reports_again for the case without it.
    ex.step(off, 8.0)
    ex.step(off, 11.0)
    fired, _ = ex.step(on, 12.0)
    assert fired == []       # still cooling -- deferred, not discarded
    fired, status = ex.step(on, 16.0)
    assert [entry["label"] for entry in fired] == ["holding cup"]
    assert status[0].cooldown_remaining_s == 15.0

    # And that fire starts a cooldown of its own, like any other.
    ex.step(off, 22.0)
    ex.step(off, 25.0)
    fired, status = ex.step(on, 26.0)
    assert fired == []
    fired, status = ex.step(on, 32.0)
    assert [entry["label"] for entry in fired] == ["holding cup"]


def test_coincident_candidates_prefer_specificity_then_planner_order():
    gaze = {"all": [1], "label": "gaze"}
    hands = {"all": [9], "label": "hands"}
    combo = {"all": [1, 9], "label": "gaze while holding"}
    sequence = {"then": [7, 9], "label": "approach then hold"}
    assert order_coincident_candidates([gaze, hands]) == [gaze, hands]
    assert order_coincident_candidates([gaze, combo, sequence]) == [sequence, combo, gaze]


def test_lean_focus_ignores_unrelated_gaze_hit():
    entry = {"all": [8], "on": "laptop"}
    viz = {
        "dets": [SimpleNamespace(label="laptop"), SimpleNamespace(label="cup")],
        "hits": [("gazing-at", {"det": 0}), ("lean-in", {"det": 1})],
    }
    assert not _focus_ok(entry, viz, {"laptop"})
