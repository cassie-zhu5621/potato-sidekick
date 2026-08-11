"""An arrival is half-occluded by the doorway it arrives through.

Reported 2026-08-08: "people coming into the room also never triggers." The plan
by then was correct -- `[10] gathering`, no object -- and it still could not fire.

`gathering` asked for the person count to be IDENTICAL across every sample in a
1.5 s window: six of six at --cv-hz 4. Someone walking in is behind the door
frame for most of that, so MediaPipe finds them on some frames and not others.
The observed sequence in the log was 1, 1, 2, 1, 1. The window is then never
uniform, `_count_stable` never updates, and the relation is unreachable.

Simulated against the arriving person's per-frame detection probability p --
fraction of arrivals reported, and how long after they entered:

    p      unanimity        two-thirds
   0.85    99.8%  2.5 s     100%  1.2 s
   0.75    93.3%  4.5 s     100%  1.2 s
   0.65    66.4%  6.0 s     100%  1.5 s
   0.50    19.1%  7.5 s     98.4% 3.0 s

The same failure class as the hands-on clock earlier the same day: a rule that
resets on any single dropped detection, in a system where dropped detections are
the normal condition rather than the exception.
"""
from __future__ import annotations

import collections
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import perception.relations as rel
from perception.relations import PersonPose, RelationEngine

HZ, DT, NOSE = 4.0, 0.25, 0


def _engine(monkeypatch):
    monkeypatch.setattr(rel, "HeadPoseEstimator", lambda **k: None)
    monkeypatch.setattr(rel, "PoseEstimator", lambda **k: None)
    return RelationEngine(detector=None)


def _people(n):
    return [PersonPose(pid=i, pts={NOSE: (100 + 200 * i, 200)}, vis={NOSE: 1.0})
            for i in range(n)]


def _run(monkeypatch, counts_by_frame):
    """-> the frame index at which gathering first became true, or None."""
    e = _engine(monkeypatch)
    for k, n in enumerate(counts_by_frame):
        truth, _ = e.evaluate([], _people(n), [], (1280, 720), k * DT)
        if truth[10]:
            return k
    return None


def test_a_clean_arrival_is_reported(monkeypatch):
    """The baseline: nobody flickers."""
    assert _run(monkeypatch, [1] * 8 + [2] * 8) is not None


def test_an_arrival_seen_through_a_doorway_is_reported(monkeypatch):
    """THE REPORT. The second person is found on most frames, not all."""
    seq = [1] * 8 + [2, 1, 2, 2, 1, 2, 2, 2, 2, 2, 1, 2]
    assert _run(monkeypatch, seq) is not None, (
        "a single missed detection still cancels an arrival -- see the module "
        "docstring for what that costs at realistic detection rates")


def test_unanimity_would_have_missed_it(monkeypatch):
    """The old rule, stated as a property rather than reimplemented: the same
    sequence has no 1.5 s window in which every sample agrees on 2."""
    seq = [1] * 8 + [2, 1, 2, 2, 1, 2]
    win = int(1.5 * HZ)
    assert not any(all(c == 2 for c in seq[i:i + win])
                   for i in range(8, len(seq) - win + 1)), (
        "the fixture no longer reproduces the reported failure")


def test_one_stray_detection_does_not_invent_an_arrival(monkeypatch):
    """What the unanimity rule was buying, and the reason the threshold is two
    thirds rather than a bare majority: a single false pose in a quiet room must
    not be reported as someone coming in."""
    seq = [1] * 10 + [2] + [1] * 10
    assert _run(monkeypatch, seq) is None


def test_the_very_first_frame_is_not_stable(monkeypatch):
    """With a one-sample window any count trivially agrees with itself. Taking
    that as the baseline means a flicker on frame 1 sets the wrong reference and
    the next settled value fires a phantom arrival."""
    assert _run(monkeypatch, [2] + [1] * 12) is None


def test_someone_leaving_counts_too(monkeypatch):
    """`gathering` is a CHANGE in the stable count, in either direction -- the
    row is about who is present, not about growth."""
    assert _run(monkeypatch, [2] * 8 + [1] * 8) is not None
