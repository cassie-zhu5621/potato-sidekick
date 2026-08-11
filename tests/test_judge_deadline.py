"""Waiting for the judge buys one thing, and it is not the sentence.

Measured 2026-08-08: the group judge normally answers in 2.1 s, but during a
service-side slowdown the same call -- identical images, identical token counts
(5863-5869 across the day), identical model and tier -- took 12-19 s, while the
planner slowed in step. Nothing about the request had changed, so nothing about
the request could fix it.

WHAT THE WAIT IS FOR. The judge answers `pass` and `describe`, and only `pass` is
on the critical path:

  * S7 plays a SOUND EFFECT, not speech. Nothing the judge writes is spoken.
  * the sentence on the feed card comes from Storyboard's own narration of the
    finished strip (session/storyboard.py, `run_judge`), which happens later and
    is unaffected.
  * the winning card is chosen by `pick_winner` -- most relation ids, ties to
    CV's order -- which is arithmetic over the spec and never asked of the model.

So a deadline costs the pass/fail gate and nothing the participant can perceive.
A 19 s S7, by contrast, points at a moment that ended long ago, which reads as
the robot being WRONG rather than slow.

WHAT IS NOT GIVEN UP: the judge is not cancelled. It lands late and its verdict
is published beside the finding it did not gate, so "how often did the deadline
let something through" is a number in the record rather than a risk taken on
faith. These tests hold that half in place -- it is the half with nothing
visible depending on it, and therefore the half that would quietly rot.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import noticebot_loop  # noqa: F401  -- the loop body is read as source below
from planning.judge import pick_winner
from robot import states as ST


# ------------------------------------------------------------ the deadline --
def test_the_default_waits_and_sits_in_the_gap():
    """The distribution is bimodal -- 16 calls at or under 8.1 s, 17 at or over
    10.6 s, nothing between -- so 10 s is the one threshold that does not cut
    through cases that are alike.

    IT WAS 0 FOR AN AFTERNOON. The case for 0 was that any mid-range threshold
    gates half the findings and not the other half, leaving a session's three
    events under two regimes. What it weighed that against was a false-report
    rate assumed near zero. The dry run showed otherwise, and structurally:
    there is no depth anywhere in this system, so a person standing in front of
    a plant and a person touching one are the same picture to a wrist-in-box
    test. A VLM on five frames can tell them apart; nothing in perception can.
    """
    assert ST.JUDGE_DEADLINE_S == 10.0, (
        f"{ST.JUDGE_DEADLINE_S}s. 0 removes the gate that catches projection "
        f"overlap read as contact; anything inside a mode cuts through cases "
        f"that are alike. Use --judge-deadline for a run that wants something "
        f"else, rather than moving the default.")


def test_zero_is_still_a_meaningful_setting():
    """Not the default any more, but it has to keep working: it is the run that
    answers "what does the CV gate do on its own", and it is the fallback if the
    service congests badly enough that the gate costs more than it catches.

    The comparison is `>=`, so 0 fires on the first pass rather than after one
    more loop iteration -- and, more to the point, so that 0 means what it says
    rather than 'one tick'."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index('waiting = candidate_gate.get("awaiting")')
    assert ">= judge_deadline" in src[i:i + 260], (
        "a strict > means judge_deadline=0 still waits for a loop tick")


def test_the_deadline_is_fixed_for_a_whole_session():
    """Read once at startup. Changed mid-session, the data has two regimes in
    it -- which is the problem it exists to remove, reintroduced."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    assert src.count("judge_deadline = (ST.JUDGE_DEADLINE_S") == 1


def test_the_judge_still_runs_when_it_is_not_waited_for():
    """Its verdict is the measurement of what skipping it costs. Not calling it
    would turn a known error rate into an unknown one, for no saving -- it is
    off the critical path either way."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index("judge deadline 0")
    assert "the judge still runs" in src[i:i + 200]
    # and nothing skips the thread on the strength of the deadline
    j = src.index("threading.Thread(target=confirm_candidate_group")
    assert "judge_deadline" not in src[j - 400:j]


# --------------------------------------------------- one rule, two callers --
def test_the_winner_is_the_most_specific_card():
    entries = [{"all": [9]}, {"all": [1, 9]}, {"all": [3]}]
    assert pick_winner(entries) == 1


def test_ties_go_to_cvs_own_order():
    """order_coincident_candidates already fixed it; re-deciding here would put
    a second opinion in front of one that was made deliberately."""
    assert pick_winner([{"all": [9]}, {"all": [3]}]) == 0


def test_any_and_then_count_too():
    """Reading only `all` scored every card 0 and made the tie-break the whole
    rule -- the same bug this file's ancestor caught in the judge path."""
    assert pick_winner([{"all": [9]}, {"any": [1, 3], "then": [9]}]) == 1


def test_nothing_to_pick_is_not_a_pick():
    assert pick_winner([]) == -1


def test_both_paths_call_the_same_function():
    """The deadline path and the judge path must not name different events for
    the same moment. Two rules written beside each other is how that happens."""
    import inspect
    from planning import judge as J
    assert "pick_winner(candidate_specs)" in inspect.getsource(J.judge_candidate_group)
    loop = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = loop.index("judge past")
    assert "pick_winner(waiting[\"entries\"])" in loop[i - 900:i + 200]


# ------------------------------------------- the late verdict is not lost ---
def _worker_body():
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index("def _confirm_candidate_group")
    return src[i:src.index("\n    try:", i)]


def test_a_late_judge_does_not_fire_a_second_time():
    body = _worker_body()
    i = body.index('candidate.get("fired_early")')
    branch = body[i:body.index("return", i)]
    assert "confirmed_findings" not in branch, (
        "the late verdict re-enters the fire path -- S7 would play twice for "
        "one moment, and the feed would carry it twice")


def test_a_late_judge_records_whether_it_agreed():
    """The whole justification for the deadline is that its cost is measurable.
    If the disagreement is not written down, it is not."""
    body = _worker_body()
    i = body.index('candidate.get("fired_early")')
    branch = body[i:body.index("return", i)]
    assert "publish_judgment" in branch
    assert "WOULD HAVE REJECTED" in branch, (
        "a disagreement is recorded in the same words as an agreement, so the "
        "number the deadline was justified by cannot be recovered from the log")


def test_the_slot_is_released_on_the_normal_path_too():
    """Otherwise the first slow judge leaves `awaiting` set forever and every
    later candidate trips the deadline the moment it is sent."""
    body = _worker_body()
    assert 'candidate_gate["awaiting"] = None' in body


def test_the_deadline_respects_a_replan():
    """A candidate from a discarded plan must not be reported just because it
    was in flight when the plan changed."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index("judge past")
    block = src[i - 900:i]
    assert 'waiting["generation"] != ctxd.get("plan_generation")' in block


# ------------------------------------------------- the verdict is a column --
def test_the_verdict_lands_on_the_record_not_only_the_screen():
    """With the gate off the critical path, its verdict is the only measure of
    what that costs -- so it has to be in attention_log.jsonl. A `[confirm]`
    line is not a record; it scrolls."""
    import inspect
    from session import storyboard as SB
    src = inspect.getsource(SB.Storyboard._finalize)
    assert '"judge_agreed": self.judge_agreed' in src

    loop = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = loop.index('candidate.get("fired_early")')
    assert "story.judge_agreed = agreed" in loop[i:i + 900]


def test_an_unanswered_judge_is_none_not_false():
    """None (had not answered yet) and False (would have rejected) are different
    findings, and collapsing them would make the false-report rate look worse
    than it is by exactly the number of slow calls."""
    from session import storyboard as SB
    sb = object.__new__(SB.Storyboard)
    SB.Storyboard.__init__.__wrapped__ if False else None
    import inspect
    src = inspect.getsource(SB.Storyboard.__init__)
    assert "self.judge_agreed = None" in src


# ---------------------------------------------------------- the two clocks --
def test_the_deadline_compares_one_clock_with_itself():
    """`sent_at` and the loop's `now` must be the same clock.

    They were not: `now = time.time()` (~1.78e9) against
    `sent_at = time.monotonic()` (seconds since boot). Every difference was
    astronomical, so the deadline fired on the first tick whatever it was set
    to. The 0 default LOOKED correct -- it fires immediately by design -- and
    --judge-deadline 10 was a knob connected to nothing. A default that hides a
    broken setting is the worst arrangement of the two.
    """
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index('candidate["sent_at"] =')
    assert "time.time()" in src[i:i + 60], src[i:i + 60]

    # ... and the comparison side is the loop's wall-clock `now`
    j = src.index("now - waiting[\"sent_at\"]")
    assert "now = time.time()" in src[:j]


def test_no_deadline_means_no_frame_window_either():
    """`due` is onset + 1.0 s because the JUDGE wants five frames spanning the
    onset. The report never needed them, and that second sits on top of the
    ~1.25 s the CV gate already spends on sustain + persist. 'As if the step did
    not exist' has to include its waiting."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index("fire_now = judge_deadline <= 0")
    block = src[i:i + 700]
    assert 'ui_events.append("finding")' in block, (
        "with no deadline the finding still waits for the judge's frame window")
    assert "pick_winner(entries)" in block, "and it must name the card the same way"


def test_firing_at_the_gate_is_not_also_fired_by_the_deadline():
    """One moment, one S7. The deadline block and the gate block both end in a
    `finding`, so exactly one of them may claim a given candidate."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index('waiting = candidate_gate.get("awaiting")')
    assert 'not waiting.get("fired_early")' in src[i:i + 220]


def test_the_judge_still_gets_its_frames_when_nobody_waits():
    """The candidate stays in the queue and is still sent -- the verdict is the
    measurement of what firing on the CV gate alone costs, so skipping the call
    would trade a known error rate for an unknown one and save nothing."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index("fire_now = judge_deadline <= 0")
    assert "event_candidates.append" in src[i:i + 1400]
