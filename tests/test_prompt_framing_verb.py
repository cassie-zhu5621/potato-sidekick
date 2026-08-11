"""The request's own watching verb is addressed to the robot, not to the scene.

Observed 2026-08-08 on a real session. The participant asked:

    "Looking at people holding a phone."

and the planner compiled `gazing-at(1) AND hands-on(9) on phone`. Its own `why`
shows the double read: "people looking at and holding a phone". "Looking at" was
consumed twice -- once correctly, as the instruction to the robot, and once again
as a thing the PERSON had to be doing.

WHY THIS IS WORTH A TEST RATHER THAN A NOTE. The failure is invisible from the
outside and points the wrong way. The plan looks richer, not wrong; the web UI
shows a tidy two-relation conjunction; nothing errors. What actually happened is
that the moment got rarer -- a conjunction fires only when every conjunct holds
in the same instant, so someone holding a phone while looking anywhere else no
longer counts. In a 15-minute session with three scripted events, that reads to
the participant as a robot that does not work, and to us as an event that
"failed to trigger" for reasons we would go looking for in the CV layer.

The rule cannot be "never use gazing-at": the same verb is content when it
belongs to a person in the room ("tell me when someone looks at the whiteboard").
So the prompt carries a deletion test rather than a blocklist, and these tests
hold that test in place -- including both halves, because a rule that only ever
strips would break the second case in the same silent way.

These are prompt-text assertions. They cannot show that the model obeys; they
show that the instruction is still being sent. The obeying is checked by replay
against recorded calls (robot/tools/provider_ab.py).
"""
import pytest

from planning.planner import build_prompt


def _flat(s):
    return " ".join(s.split())


@pytest.fixture
def p():
    return _flat(build_prompt("Looking at people holding a phone."))


def test_the_frame_is_named_as_the_robots_own_verb(p):
    assert "says who is watching" in p
    for verb in ("Look at", "watch for", "keep an eye on", "tell me when"):
        assert verb in p, f"{verb!r} is not listed as framing"


def test_the_rule_is_a_test_not_a_ban(p):
    """A blocklist on gazing-at would fix this request and break the next one."""
    assert "Test it by deletion" in p
    assert "if what remains still describes a scene" in p


def test_both_halves_are_shown(p):
    # the one that must be stripped ...
    assert "people holding a phone" in p and "Plan hands-on(9) ALONE" in p
    # ... and the one that must NOT be, or the rule overshoots
    assert "someone looks at the whiteboard" in p
    assert "gazing-at(1) on whiteboard is correct" in p


def test_the_reason_given_is_the_real_cost(p):
    """Told only 'do not do this', a model weighs it against everything else in a
    long prompt. Told what it breaks, it has something to trade off against."""
    assert "makes the requested moment" in p and "rarer" in p


def test_the_frame_rule_precedes_the_request(p):
    """Ordering matters: the rule is how to READ the context, so it has to arrive
    before the context does. After it, it is a correction to a reading already
    made."""
    assert p.index("Test it by deletion") < p.index('CONTEXT: "')


def test_the_rule_travels_with_every_request(p):
    """Not conditional on the wording -- the planner cannot know in advance which
    requests contain a framing verb, and a rule that appeared only when a
    keyword matched would miss "notice if anyone picks up a phone"."""
    for ctx in ("", "plants", "Tell me when someone looks at the whiteboard."):
        assert "Test it by deletion" in _flat(build_prompt(ctx))
