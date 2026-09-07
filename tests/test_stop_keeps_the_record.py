"""The board's count is an inbox. The page's record is not.

Observed 2026-08-08. A finding fired, S7 played, the robot spoke it, the record
reached attention_log.jsonl and both jpgs reached disk -- and the NOTICED feed on
the page read 0. The record had been appended and then thrown away, by STOP.

The two halves of the feature had been built for different sessions. Every record
carries `request` and `plan_generation`, stamped so that "a session where the
participant asks twice" does not collapse into one undifferentiated list
(Storyboard.__init__) -- provenance that is only meaningful if records from
different requests coexist. On disk they did; on the page they never could. Nor
was the wipe escapable: PTT is accepted only from S1_IDLE and STOP is the only
transition into it, so ASKING A SECOND QUESTION REQUIRED DESTROYING THE ANSWER TO
THE FIRST.

The split now runs along what each surface is for:

  CoreS3   how much is waiting for you. STOP and OK both clear it, because both
           end the state of having something unseen. A counter that only climbs
           stops being read -- there is no way to tell one new thing from the
           same seven again.
  the page what it has found for me. Never cleared, and served from the log on
           disk rather than from process memory, so it survives a reload, a
           browser opened late, and a restart mid-session.

These are different windows onto the same events, not different events -- which
is what the older invariant in session/feed.py was guarding against, and why that
note now says so explicitly.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import noticebot_loop  # noqa: F401  -- act() is nested in main(); source-read below
from session.session_flow import SessionFlow


# ----------------------------------------------------------- the flow half ---
def _to_s7():
    """Drive a real SessionFlow to S7b with one finding on the board."""
    clock = [0.0]
    f = SessionFlow(now=lambda: clock[0])
    for e in ("ptt_down", "ptt_up", "transcript:watch the desk", "planned",
              "arrived:S4_PLAN", "arrived:S5A_SETTLE", "arrived:S5B_TRACK",
              "finding:someone at the desk", "arrived:S7a", "arrived:S7b"):
        f.feed(e)
    return f


def test_a_finding_puts_something_on_the_board():
    assert _to_s7().noticed == 1


def test_ok_clears_the_board_but_not_in_front_of_them():
    """Acknowledging is the whole point of the button: it means 'seen'. It still
    empties the board -- the count is an inbox, not a score -- but NOT on the
    press.

    Clearing on the press put "0 noticed" on the screen at the instant they
    acknowledged the report, which reads as the report being deleted rather than
    as an inbox being emptied. The nod now covers that moment, and the number
    goes when the nod lands and the screen has already moved to tracking, where
    nobody is reading a number."""
    f = _to_s7()
    out = f.feed("ok")
    assert f.noticed == 1, "the count was wiped while they were still looking at it"
    assert not any(k == "noticed" for k, _ in out)

    out = f.feed("arrived:S5B_TRACK")
    assert f.noticed == 0
    assert ("noticed", 0) in out, "the board is not told, so it keeps the number"


def test_stop_clears_the_board_too():
    f = _to_s7()
    out = f.feed("stop")
    assert f.noticed == 0 and ("noticed", 0) in out


def test_ok_does_not_abandon_the_task():
    """The difference between the two buttons, and the reason OK must not take
    STOP's clean-up path: OK goes back to watching, so nothing is void.

    It nods on the way. S7 v6 rests exactly where S5b watches from, so returning
    is no longer a visible movement and the acknowledgement had nothing left to
    carry it -- the board beeped and the robot did nothing. What matters here is
    unchanged: the task survives."""
    f = _to_s7()
    out = f.feed("ok")
    assert f.state == "S3_ACK"
    assert ("ack_then", "S5B_TRACK") in out, "the nod must be armed to land on watching"
    f.feed("arrived:S5B_TRACK")
    assert f.state == "S5B_TRACK"
    assert not any(k == "idle" for k, _ in out), (
        "OK emitted `idle`, which tears down the executor and cancels stories "
        "still collecting -- for a task that is still running.")


def test_stop_does_abandon_it():
    f = _to_s7()
    out = f.feed("stop")
    assert f.state == "S1_IDLE" and ("idle", True) in out


# ------------------------------------------------------------ the UI half ---
def _handler_body(anchor, span=2200):
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index(anchor)
    return src[i:i + span]


def test_the_count_reset_clears_nothing():
    """It fires on OK as well as STOP now, and OK abandons nothing: the story
    that produced the finding is usually still gathering its later panels."""
    body = _handler_body("if int(val) == 0:")
    body = body[:body.index("return")]
    for key in ("feed", "thumbs", "frames", "collecting"):
        assert f'STATE["{key}"] = ' not in body, (
            f'the noticed-0 handler clears STATE["{key}"] again. On OK that '
            f"destroys part of a live task; on STOP it is the 2026-08-08 "
            f"regression. Read this module's docstring first.")


def test_stop_still_drops_work_in_progress():
    """The spinner row is the display half of story.reset(); they must move
    together or the page advertises a card that will never arrive."""
    body = _handler_body('elif kind == "idle":')
    assert "story.reset()" in body
    assert 'STATE["collecting"] = []' in body


# ---------------------------------------------------------- the disk half ---
def test_the_page_is_served_from_the_log(tmp_path, monkeypatch):
    """Two records from DIFFERENT requests, both present, newest first."""
    from webui import server as UI
    log = tmp_path / "attention_log.jsonl"
    rows = [{"time": "10:00:01", "label": "a", "request": "watch the desk"},
            {"time": "10:04:12", "label": "b", "request": "watch the plant"}]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(UI, "feed_dir", lambda: str(tmp_path))

    out = _get_feed(UI)
    assert [r["label"] for r in out] == ["b", "a"], "newest first"
    assert {r["request"] for r in out} == {"watch the desk", "watch the plant"}, (
        "a record from an earlier request was dropped -- that is the bug")


def test_a_torn_last_line_costs_one_card_not_the_feed(tmp_path, monkeypatch):
    """The writer appends while the page polls. Refusing the whole response
    would blank a feed that is almost entirely intact."""
    from webui import server as UI
    log = tmp_path / "attention_log.jsonl"
    log.write_text(json.dumps({"label": "a"}) + "\n" + '{"label": "b", "tru')
    monkeypatch.setattr(UI, "feed_dir", lambda: str(tmp_path))
    assert [r["label"] for r in _get_feed(UI)] == ["a"]


def test_no_log_falls_back_to_memory(tmp_path, monkeypatch):
    """--save off writes no log, and that run should still show its findings."""
    from webui import server as UI
    monkeypatch.setattr(UI, "feed_dir", lambda: str(tmp_path))
    monkeypatch.setitem(UI.STATE, "feed", [{"label": "in-memory only"}])
    assert [r["label"] for r in _get_feed(UI)] == ["in-memory only"]


def _get_feed(UI):
    """Call the real /feed.json branch without standing up a socket.

    The branch is inside H.do_GET, so it is reached by giving a bare object the
    two attributes that branch touches -- `path` and `_send` -- rather than by
    starting a server. Testing the shipped code path matters here: the bug being
    guarded against was a handler reading the wrong source, so a reimplemented
    reader in the test would guard nothing."""
    sent = {}

    class Stub:
        path = "/feed.json"

        def _send(self, code, ctype, body):
            sent["body"] = body

    UI.H.do_GET(Stub())
    return json.loads(sent["body"].decode())
