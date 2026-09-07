"""The two emergency controls, and what they replaced.

A session is one shot. When an actor plays the scene and the CV does not fire,
the choice is between a void session and the researcher taking over. Added
2026-08-12 in place of a panel for hand-editing the watch entries: repairing the
SPEC mid-session asks the researcher to think in relation ids with an actor
mid-scene and a participant watching, and what is wanted at that moment is not a
better spec, it is THIS, noticed, now.

  notice this NOW   -> the flow's `finding` event: S7a performs the notice and a
                       story opens, collects keyframes and is narrated into the
                       feed. Skips the trigger and the CONFIRMATION judge, which
                       are the two things that just failed.
  look around again -> the REPLAN_PERIOD_S sweep, on demand, by the same path.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import noticebot_loop  # noqa: F401  -- read as source
import webui.server as W
from session.session_flow import SessionFlow


def _watching():
    """A flow parked in S5B_TRACK with a request on record."""
    f = SessionFlow(now=lambda: 0.0)
    f.state, f.transcript = "S5B_TRACK", "tell me if anyone draws on the board"
    f._planned_at = 0.0
    return f


def _kinds(out):
    return [k for k, _ in out]


# ------------------------------------------------------------- notice NOW --
def test_the_forced_finding_performs_and_records():
    out = _watching().feed("finding")
    assert "noticed" in _kinds(out), "no `noticed` means no story is ever opened"
    assert ("state", "S7a") in out, "the participant must see it notice"


def test_it_does_not_go_near_the_confirmation_judge():
    """The whole point: the trigger and the judge are what failed."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index("if force_now:")
    block = src[i:i + 1200]
    assert "ui_events.append(\"finding\")" in block
    for word in ("confirm_candidate", "run_judge", "candidate_gate"):
        assert word not in block, f"{word} is exactly what this path exists to skip"


def test_the_previous_judges_sentence_does_not_travel():
    """`describe` is read by Storyboard._finalize as the opening line. Left
    standing from the last real finding, the forced card wears its words."""
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index("if force_now:")
    block = src[i:src.index("if resweep_now:", i)]
    assert "story.describe = \"\"" in block
    assert "story.judge_agreed = None" in block


def test_the_button_is_a_one_shot_flag_the_loop_clears():
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index('UI.STATE.get("pending_finding")')
    assert 'UI.STATE["pending_finding"] = False' in src[i:i + 200]


# ----------------------------------------------------------- look again --
def test_resweep_replans_on_the_request_already_on_record():
    f = _watching()
    out = f.feed("resweep")
    assert ("plan", "tell me if anyone draws on the board") in out
    assert ("state", "S4_PLAN") in out
    assert f.plan_pending is True


def test_resweep_does_not_re_acknowledge():
    """S3_ACK is "I heard you" and nobody has said anything. The robot decided
    this itself -- straight to the sweep."""
    assert ("state", "S3_ACK") not in _watching().feed("resweep")


def test_resweep_is_refused_before_anything_has_been_asked():
    f = SessionFlow(now=lambda: 0.0)
    out = f.feed("resweep")
    assert ("state", "S4_PLAN") not in out
    assert f.plan_pending is False


def test_resweep_is_refused_while_a_sweep_is_already_out():
    f = _watching()
    f.plan_pending = True
    assert ("state", "S4_PLAN") not in f.feed("resweep")


def test_it_takes_the_same_path_as_the_timer():
    """A researcher pressing this must produce a robot that behaves exactly as
    it would have on its own, or the session stops being an instance of the
    design under study."""
    import inspect
    src = inspect.getsource(SessionFlow.feed)
    i = src.index('if ev == "resweep":')
    assert "self._replan(" in src[i:i + 900]


# ------------------------------------------------ and the panel it replaced --
def test_the_hand_edit_panel_is_gone():
    page = W.PAGE
    for gone in ("id=editor", "renderEditor", "revert to plan", "+ card"):
        assert gone not in page, f"{gone} is a leftover of the EDIT panel"
    for gone in ("spec_watch", "pending_spec", "spec_error"):
        assert gone not in W.STATE, gone


def test_the_spec_route_is_gone():
    src = open(os.path.join(ROOT, "webui", "server.py")).read()
    assert 'self.path == "/spec"' not in src
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    assert "raw_spec" not in src


def test_both_routes_exist():
    src = open(os.path.join(ROOT, "webui", "server.py")).read()
    assert '"/finding", "/resweep"' in src
    assert "onclick=\"force()\"" in src and "onclick=\"resweep()\"" in src
