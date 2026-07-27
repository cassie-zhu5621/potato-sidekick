#!/usr/bin/env python3
"""
Exercise every path through session_flow with a fake clock and no hardware.

  python3 test_session_flow.py

Worth having as a test rather than as something to try on the robot: the awkward
paths -- STOP mid-scan, a transcript that arrives after a timeout, a tap during
the wrong state -- are exactly the ones that are tedious to trigger by hand and
the ones a participant will find in the first five minutes.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from session.session_flow import SessionFlow, transcript_usable
from robot import states as ST

CLOCK = [0.0]
fails = []


def flow():
    CLOCK[0] = 0.0
    return SessionFlow(now=lambda: CLOCK[0])


def run(f, events, label):
    trail = []
    for e in events:
        for kind, val in f.feed(e):
            trail.append((kind, val))
    return trail


def expect(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("--- the happy path ---")
f = flow()
run(f, ["ptt_down", "ptt_up", "transcript:find the blue mug"], "happy")
expect(f.state == "S3_ACK", f"usable transcript -> S3_ACK (got {f.state})")
expect(f.transcript == "find the blue mug", "transcript kept for the planner")
f.feed("arrived:S4_PLAN"); f.feed("arrived:S5_TRACK")
expect(f.state == "S5_TRACK" and f.screen == "planning",
       "holds at planning while the VLM is still deciding where to look")
f.feed("planned")
expect(f.screen == "tracking", "only says tracking once the direction is known")
f.feed("finding")
expect(f.state == "S7a" and f.noticed == 1, "finding -> S7a, count 1")
f.feed("arrived:S7b"); f.feed("ok")
expect(f.state == "S5_TRACK", "OK -> back to watching")

print("\n--- the waiting screen ---")
f = flow()
out = run(f, ["ptt_down"], "")
expect(("ui", "recording") in out, "PTT down -> recording screen")
expect(("rec", "start") in out, "recording actually starts")
out = run(f, ["ptt_up"], "")
expect(("ui", "waiting") in out, "PTT up -> waiting screen")
expect(("state", "S3_ACK") not in out, "does NOT nod before the words exist")

print("\n--- unusable requests go to S8 ---")
for bad, why in [("", "silence"), ("uh", "one syllable"),
                 ("...", "punctuation only"), ("ok", "single word")]:
    f = flow()
    run(f, ["ptt_down", "ptt_up", f"transcript:{bad}"], "")
    expect(f.state == "S8_ERROR", f"{why!r:22} -> S8_ERROR (got {f.state})")

print("\n--- a misread is NOT an error: it is retyped ---")
f = flow()
run(f, ["ptt_down", "ptt_up", "transcript:find the blue mug"], "")
expect(f.state == "S3_ACK", "the corrected text goes straight through")

print("\n--- STT timeout: 15s of nothing -> S8 ---")
f = flow()
run(f, ["ptt_down", "ptt_up"], "")
CLOCK[0] = 10.0
f.feed("tick")
expect(f.state == "S2_LISTEN", "still waiting at 10s")
CLOCK[0] = 16.0
f.feed("tick")
expect(f.state == "S8_ERROR", f"past {ST.STT_TIMEOUT_S:.0f}s -> S8_ERROR")

# busy may postpone the deadline; it may not postpone it forever.
f = flow()
run(f, ["ptt_down", "ptt_up"], "")
f.stt_busy = True
CLOCK[0] = 25.0
f.feed("tick")
expect(f.state == "S2_LISTEN", "a RUNNING transcription is not a timeout")
CLOCK[0] = 31.0
f.feed("tick")
expect(f.state == "S8_ERROR",
       f"but past the {ST.STT_HARD_TIMEOUT_S:.0f}s ceiling it errors anyway -- "
       f"a wedged worker must not strand a participant")

print("\n--- S7 ignored for 30s ---")
f = flow()
run(f, ["ptt_down", "ptt_up", "transcript:watch the door"], "")
f.feed("arrived:S4_PLAN"); f.feed("arrived:S5_TRACK"); f.feed("finding")
f.feed("arrived:S7b")
CLOCK[0] += 20
f.feed("tick")
expect(f.state == "S7b", "still beckoning at 20s")
CLOCK[0] += 15
out = f.feed("tick")
expect(f.state == "S5_TRACK", "past 30s -> back to watching, not escalating")
expect(f.noticed == 1, "the finding stays counted -- it is in the feed")

print("\n--- STOP works from everywhere ---")
for st_events in (["ptt_down"],
                  ["ptt_down", "ptt_up"],
                  ["ptt_down", "ptt_up", "transcript:find the mug"],
                  ["ptt_down", "ptt_up", "transcript:find the mug", "arrived:S4_PLAN"],
                  ["ptt_down", "ptt_up", "transcript:find the mug",
                   "arrived:S4_PLAN", "arrived:S5_TRACK"],
                  ["ptt_down", "ptt_up", "transcript:find the mug",
                   "arrived:S4_PLAN", "arrived:S5_TRACK", "finding"]):
    f = flow()
    run(f, st_events, "")
    before = f.state
    f.feed("stop")
    expect(f.state == "S1_IDLE" and f.transcript is None,
           f"STOP from {before:<12} -> S1_IDLE, task discarded")

print("\n--- tap only means 'not that' while watching ---")
f = flow()
run(f, ["ptt_down", "ptt_up", "transcript:find the mug"], "")
out = f.feed("tap")
expect(f.state == "S3_ACK", "tap during S3 is ignored")
f.feed("arrived:S4_PLAN"); f.feed("arrived:S5_TRACK")
out = f.feed("tap")
expect(f.state == "S6_FINETUNE", "tap during S5 -> S6")
out = f.feed("reaim:-30")
expect(("pan", -30.0) in out, "re-aim emits a pan target")
expect(f.state == "S5_TRACK", "the re-aim ITSELF returns to watching")

print("\n--- S6 waits for a direction; a late click still works ---")
f = flow()
run(f, ["ptt_down", "ptt_up", "transcript:find the mug",
        "arrived:S4_PLAN", "arrived:S5_TRACK", "tap"], "")
CLOCK[0] += 5
f.feed("tick")
expect(f.state == "S6_FINETUNE", "still asking at 5s -- does not race the clip")
out = f.feed("reaim:30")
expect(f.state == "S5_TRACK" and ("pan", 30.0) in out, "a direction at 5s lands")

f = flow()
run(f, ["ptt_down", "ptt_up", "transcript:find the mug",
        "arrived:S4_PLAN", "arrived:S5_TRACK", "tap"], "")
CLOCK[0] += 20
f.feed("tick")
expect(f.state == "S5_TRACK", "no direction in 15s -> watches anyway, not stuck")

f = flow()
run(f, ["ptt_down", "ptt_up", "transcript:find the mug",
        "arrived:S4_PLAN", "arrived:S5_TRACK"], "")
out = f.feed("reaim:-60")
expect(("pan", -60.0) in out and f.state == "S5_TRACK",
       "a nudge while already watching is accepted too")

print("\n--- PTT after STOP starts fresh ---")
f = flow()
run(f, ["ptt_down", "ptt_up", "transcript:find the mug", "stop"], "")
out = f.feed("ptt_down")
expect(("state", "S2_LISTEN") in out and f.transcript is None,
       "a new request, not a resume")

print("\n--- typed text: the researcher's fallback, usable from a dead session ---")
# The bug this covers: `typed` was routed as `transcript`, which only S2_LISTEN
# accepted. So the one input that exists to rescue a broken session was silently
# dropped in every state the session could actually be broken in.
for start, label in [([], "S1_IDLE"),
                     (["ptt_down", "ptt_up", "transcript:x"], "S8_ERROR")]:
    f = flow()
    run(f, start, "")
    expect(f.state == label, f"precondition: in {label}")
    out = f.feed("typed:watch the blue mug")
    expect(f.state == "S3_ACK", f"typed from {label} -> S3_ACK (got {f.state})")
    expect(("plan", "watch the blue mug") in out, "and it reaches the planner")

f = flow()
run(f, ["ptt_down", "ptt_up"], "")
f.feed("typed:watch the door")
expect(f.state == "S3_ACK", "typed during S2 corrects a misread, as before")

# Nonsense errors whatever it arrived on. What a participant can see is the
# robot, not the keyboard, so "it did not understand" has to look the same way
# every time.
for ev, who in [("typed", "researcher"), ("transcript", "participant")]:
    f = flow()
    if ev == "transcript":
        run(f, ["ptt_down", "ptt_up"], "")
    f.feed(f"{ev}:zz")
    expect(f.state == "S8_ERROR", f"nonsense from the {who} -> S8_ERROR")

print("\n--- noises are not requests ---")
# These pass the length and word-count tests and are plainly not requests. A
# participant who beeps at the robot must see it fail to understand, not watch
# it plan confidently against a noise.
for junk, why in [("beep beep beep", "an onomatopoeia, repeated"),
                  ("la la la", "singing"),
                  ("agaeirughsrihrsi", "keyboard mash, one word"),
                  ("asdfghjkl qwertyuiop", "keyboard mash, two words"),
                  ("ggggg hhhhh", "no vowels at all"),
                  ("test test", "the same word twice")]:
    ok, reason = transcript_usable(junk)
    expect(not ok, f"{junk!r:24} rejected -- {why} ({reason})")
    f = flow()
    f.feed(f"typed:{junk}")
    expect(f.state == "S8_ERROR", f"{junk!r:24} typed from idle -> S8_ERROR")

# ...and the rejection must not be so eager that it eats real requests.
for good in ["watch the roundtable area at my lab",
             "tell me when someone points at the whiteboard",
             "keep an eye on the door", "find the blue mug",
             "the strengths of the team", "look left"]:
    ok, reason = transcript_usable(good)
    expect(ok, f"{good[:44]!r:46} still accepted ({reason})")

# Typed text re-plans from EVERY state, including mid-watch. This is the wizard
# channel and it has to feel immediate; gating it on state made it read as broken.
for extra, label in [(["arrived:S4_PLAN"], "S4_PLAN"),
                     (["arrived:S4_PLAN", "arrived:S5_TRACK"], "S5_TRACK"),
                     (["arrived:S4_PLAN", "arrived:S5_TRACK", "tap"], "S6_FINETUNE")]:
    f = flow()
    run(f, ["ptt_down", "ptt_up", "transcript:find the mug"] + extra, "")
    expect(f.state == label, f"precondition: in {label}")
    out = f.feed("typed:watch the whiteboard instead")
    expect(f.state == "S3_ACK" and ("plan", "watch the whiteboard instead") in out,
           f"typed from {label} re-plans immediately")

print("\n--- PTT release must survive the screen it was pressed on ---")
# Mirrors the firmware latch. On hardware the recording screen has no buttons, so
# a release tied to the drawn button never fired and S2 hung with no armed
# timeout. Here: assert ptt_up is what arms the deadline at all.
f = flow()
run(f, ["ptt_down"], "")
CLOCK[0] = 60.0
f.feed("tick")
expect(f.state == "S2_LISTEN",
       "held button = still recording, no timeout (this is why a lost PTT_UP hung)")
out = run(f, ["ptt_up"], "")
expect(("rec", "stop") in out, "release stops the recorder")
CLOCK[0] = 69.0
f.feed("tick")
expect(f.state == "S2_LISTEN", "9s after release is still inside the 15s deadline")
CLOCK[0] = 76.0
f.feed("tick")
expect(f.state == "S8_ERROR", "and only then does the STT deadline run")

print("\n--- a cold recogniser is not a dead one ---")
# The first PTT of every session used to fail and the second work: loading the
# whisper model takes longer than STT_TIMEOUT_S, so the request was thrown away
# while it was being successfully transcribed.
f = flow()
run(f, ["ptt_down", "ptt_up"], "")
f.stt_busy = True
CLOCK[0] = 20.0
f.feed("tick")
expect(f.state == "S2_LISTEN", "no timeout while a transcription is RUNNING")
f.stt_busy = False
f.feed("transcript:watch the roundtable")
expect(f.state == "S3_ACK", "the late transcript is still accepted")
f = flow()
run(f, ["ptt_down", "ptt_up"], "")
CLOCK[0] = 20.0
f.feed("tick")
expect(f.state == "S8_ERROR", "but a recogniser that never answers still errors")

print("\n--- STOP reaches perception, not just the motors ---")
for st_events, label in [
        (["ptt_down", "ptt_up", "transcript:find the mug", "arrived:S4_PLAN"], "S4_PLAN"),
        (["ptt_down", "ptt_up", "transcript:find the mug",
          "arrived:S4_PLAN", "arrived:S5_TRACK"], "S5_TRACK")]:
    f = flow()
    run(f, st_events, "")
    out = f.feed("stop")
    expect(("idle", True) in out,
           f"STOP from {label} tells the loop to tear the watch-spec down")

print("\n--- the screen does not claim to be tracking early ---")
f = flow()
run(f, ["ptt_down", "ptt_up", "transcript:watch the roundtable",
        "arrived:S4_PLAN"], "")
expect(f.screen == "planning", "S4 -> planning")
f.feed("arrived:S5_TRACK")
expect(f.screen == "planning",
       "S5 begins (head holds the last swept angle) but still says planning")
f.feed("planned")
expect(f.screen == "tracking", "the VLM answered -> tracking")

f = flow()                         # a re-plan re-arms it
run(f, ["ptt_down", "ptt_up", "transcript:watch the door",
        "arrived:S4_PLAN", "arrived:S5_TRACK", "planned"], "")
f.feed("typed:watch the whiteboard instead")
f.feed("arrived:S4_PLAN"); f.feed("arrived:S5_TRACK")
expect(f.screen == "planning", "a re-plan puts it back to planning")

print("\n--- ignored 'not that' moves ON, not back to the same view ---")
f = flow()
run(f, ["ptt_down", "ptt_up", "transcript:find the mug",
        "arrived:S4_PLAN", "arrived:S5_TRACK", "planned", "tap"], "")
CLOCK[0] += 20
out = f.feed("tick")
expect(("pan_next", True) in out,
       "no answer in 15s -> ask for the NEXT-best angle, not the rejected one")
expect(f.state == "S5_TRACK", "and it goes back to watching")

print("\n--- transcript_usable thresholds ---")
for text, want in [("find the blue mug", True), ("", False), ("hm", False),
                   ("!?!?", False), ("look left", True)]:
    ok, why = transcript_usable(text)
    expect(ok == want, f"{text!r:22} usable={ok} ({why})")
ok, _ = transcript_usable("find the mug", no_speech_prob=0.9)
expect(not ok, "high no_speech_prob rejected even with good words")
ok, _ = transcript_usable("find the mug", avg_logprob=-2.0)
expect(not ok, "very low avg_logprob rejected -- hallucination")

print("\n" + "=" * 60)
print(f"{len(fails)} failure(s)" if fails else "all paths pass")
sys.exit(1 if fails else 0)
