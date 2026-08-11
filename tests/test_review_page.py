"""The review page is what the participant reads in protocol §6.

Two properties, and the first is the one that would rot silently.

WHAT MUST NOT BE ON IT. `judge_agreed`, the truth vector, relation ids and names,
`worth`, `plan_generation`, the card label -- every field the system used to
decide. RQ4 asks whether people can read the ROBOT; a page carrying the machinery
invites them to grade the machinery instead, and the leak would not look like a
bug, it would look like a slightly more informative page.

WHEN IT IS BUILT. On every report, from `feed.publish`. §6 follows the work phase
with no gap, so a page rebuilt by hand afterwards is a page that is one report
stale exactly when it is being read.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import session.feed as F
from session.review import build, build_index


REC = {
    "time": "21:59:07", "worth": 0.5, "why": "watch-spec",
    "note": "She reached out and began writing on the board.",
    "thumb": "thumb_x.jpg", "frame": "frame_x.jpg",
    "label": "looking at whiteboard", "shots": 3,
    "story": "gazing-at whiteboard, hands_on -> gathering",
    "truth": {"1": 1, "9": 1}, "request": "Tell me if someone draws.",
    "plan_generation": 3, "story_generation": 1, "judge_agreed": False,
}


def _session(tmp_path, recs=(REC,)):
    d = tmp_path / "e2e_20260808_215607"
    d.mkdir()
    with open(d / "attention_log.jsonl", "w") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    return str(d)


# ------------------------------------------------------------- the leak test --
def test_no_internal_field_reaches_the_page(tmp_path):
    page = open(build(_session(tmp_path))).read()
    for leak in ("judge_agreed", "plan_generation", "story_generation",
                 "watch-spec", "gazing-at", "looking at whiteboard", '"truth"'):
        assert leak not in page, f"{leak!r} is on the participant's screen"


def test_what_the_robot_said_is_on_the_page(tmp_path):
    page = open(build(_session(tmp_path))).read()
    assert REC["note"] in page
    assert REC["time"] in page
    assert REC["request"] in page          # theirs, and §6 cannot be asked without it
    assert REC["frame"] in page


def test_the_request_heads_each_group(tmp_path):
    """Sessions carry several briefs -- the card one, their own, anything
    re-spoken after a correction. A report means nothing beside the wrong words."""
    a = dict(REC, request="watch the plant", note="one", time="10:00:00")
    b = dict(REC, request="watch the door", note="two", time="10:05:00")
    page = open(build(_session(tmp_path, [a, a, b]))).read()
    assert page.count("You asked") == 2, "consecutive identical briefs repeated"


# --------------------------------------------------------- rating and keeping --
def test_every_report_gets_a_rating_and_a_keep(tmp_path):
    recs = [dict(REC, time=f"10:0{i}:00") for i in range(3)]
    page = open(build(_session(tmp_path, recs))).read()
    assert page.count('class="dots"') == 3
    assert page.count('type="checkbox"') == 3
    assert "Download CSV" in page and "Copy CSV" in page


def test_the_csv_carries_the_words_the_answers_are_about(tmp_path):
    """A rating detached from the report it rates is not data. ROWS travels with
    the page so the export is self-contained -- the log is not read again."""
    page = open(build(_session(tmp_path))).read()
    i = page.index("const ROWS=")
    rows = json.loads(page[i + len("const ROWS="):page.index(";", i)])
    assert rows[0]["n"] == REC["note"] and rows[0]["q"] == REC["request"]
    assert "'rating','keep'" in page or "'rating','keep'" in page.replace('"', "'")


# ----------------------------------------------------------------- freshness --
def test_publish_rebuilds_the_page(tmp_path):
    """The property the hook exists for."""
    import numpy as np
    d = tmp_path / "e2e_20260808_215607"
    d.mkdir()
    args = type("A", (), {"save": True, "feed_dir": str(d)})()
    F.publish(np.zeros((300, 900, 3), np.uint8), dict(REC), args, None)
    assert os.path.exists(d / "review.html")
    assert REC["note"] in open(d / "review.html").read()


def test_a_broken_page_does_not_lose_the_report(tmp_path):
    """The record is the study's data; the page is a convenience. If rendering
    ever throws, the line must already be on disk and must stay there."""
    import numpy as np
    d = tmp_path / "e2e_20260808_215607"
    d.mkdir()
    args = type("A", (), {"save": True, "feed_dir": str(d)})()
    real = F.__dict__.get("json")
    import session.review as R
    boom, R.build = R.build, lambda *a: (_ for _ in ()).throw(RuntimeError("x"))
    try:
        F.publish(np.zeros((300, 900, 3), np.uint8), dict(REC), args, None)
    finally:
        R.build = boom
    lines = open(d / "attention_log.jsonl").read().strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["note"] == REC["note"]
    assert real is not None


# --------------------------------------------------------------- the index --
def test_the_index_is_never_linked_from_a_review_page(tmp_path):
    """It lists every participant's session and their spoken briefs, and the
    review page is open on a machine in front of one of them."""
    page = open(build(_session(tmp_path))).read()
    assert "index.html" not in page
    assert "../" not in page


def test_the_index_says_who_it_is_for(tmp_path):
    _session(tmp_path)
    import session.review as R
    real, R.FEED = R.FEED, str(tmp_path)
    try:
        page = open(build_index([str(tmp_path / "e2e_20260808_215607")])).read()
    finally:
        R.FEED = real
    assert "researcher" in page.lower()
    assert "e2e_20260808_215607/review.html" in page
