"""STOP starts a fresh task. It does not delete the last one's findings.

Renamed on 2026-08-12 from test_storyboard_reset_cancels_pending_bursts, which
asserted the opposite. That behaviour lost a judge-CONFIRMED finding in the
17:31 pilot run: "drawing on whiteboard" confirmed at 17:45:14, STOP pressed
about forty seconds later to give a new instruction, and nothing in
attention_log.jsonl -- the burst was still inside its 45 s window and
`bursts.clear()` took it.

The reason the old assertion looked reasonable is that "cancel unfinished
stories" sounds like housekeeping. It is not: `Storyboard.open` hangs off the
flow's `noticed` emission, so a burst exists only AFTER the chirp has sounded
and the card is on the board. STOP voids the PLAN -- no further findings -- and
cannot un-tell the participant something it has already said.
"""
from __future__ import annotations

import json
import os
import time

import numpy as np

from session.storyboard import Storyboard


def _open_one(story, label="old event"):
    room = np.full((360, 640, 3), 120, np.uint8)
    story.open({"label": label, "all": [9]}, room, {9: True}, {}, 0)


def _wait_for_log(d, n=1, timeout=4.0):
    p = os.path.join(d, "attention_log.jsonl")
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(p) and len(open(p).read().strip().splitlines()) >= n:
            break
        time.sleep(0.05)
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def test_reset_writes_the_open_story_instead_of_dropping_it(tmp_path):
    story = Storyboard(feed_dir=str(tmp_path), offline=True)
    _open_one(story)

    story.reset()

    recs = _wait_for_log(str(tmp_path))
    assert [r["label"] for r in recs] == ["old event"]


def test_the_generation_bump_does_not_cancel_it(tmp_path):
    """`reset` raises `generation` and `_finalize` used to compare the burst's
    generation against it -- twice, once on entry and once after the narration
    call. Either check would discard a story flushed by reset itself."""
    story = Storyboard(feed_dir=str(tmp_path), offline=True)
    _open_one(story)

    story.reset()
    assert story.generation == 1

    recs = _wait_for_log(str(tmp_path))
    assert len(recs) == 1
    # Provenance, not a veto -- and the BURST's generation, the task it belongs
    # to, not whatever the board has moved on to. Read off `self` this was a
    # race: the thread publishes on either side of the bump.
    assert recs[0]["story_generation"] == 0


def test_it_is_marked_truncated(tmp_path):
    """Its length is when STOP was pressed, not when the event ended."""
    story = Storyboard(feed_dir=str(tmp_path), offline=True)
    _open_one(story)
    story.reset()
    assert _wait_for_log(str(tmp_path))[0]["truncated"] is True


def test_the_sentence_and_the_request_survive_the_wipe(tmp_path):
    """`reset` clears `describe` and `judge_agreed` immediately after flushing,
    and the finalize runs on a thread -- so it must read the snapshot flush took,
    not the fields that are about to be emptied. Without this the recovered card
    arrives with no sentence and attached to no request."""
    story = Storyboard(feed_dir=str(tmp_path), offline=True)
    story.request = "tell me if anyone draws on the whiteboard"
    story.plan_generation = 4
    story.describe = "A person is using a marker to write on a whiteboard."
    story.judge_agreed = True
    _open_one(story, "drawing on whiteboard")

    story.reset()
    assert story.describe == "", "the fixture is meant to race the wipe"

    rec = _wait_for_log(str(tmp_path))[0]
    assert "marker" in rec["note"]
    assert rec["request"] == "tell me if anyone draws on the whiteboard"
    assert rec["plan_generation"] == 4
    assert rec["judge_agreed"] is True


def test_the_board_counter_still_starts_over(tmp_path):
    story = Storyboard(feed_dir=str(tmp_path), offline=True)
    story.count = 3
    story.reset()
    assert story.count == 0
    assert story.bursts == []
