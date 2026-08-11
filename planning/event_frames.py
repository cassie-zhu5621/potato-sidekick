"""Select ordered temporal evidence around a CV candidate onset.

FIVE FRAMES, AND ALL OF THEM ALREADY IN THE PAST.

The count is not the cost. Measured 2026-08-08 by replaying one recorded call
four times at each width, same images, same cards, same provider:

    5 frames   3.3  2.1  2.0  2.3     median 2.3 s
    3 frames   2.0  2.0  1.6  2.6     median 2.0 s
    1 frame    1.8  2.4  1.5  1.7     median 1.8 s

0.3 s of a 2.3 s call, which is inside the run-to-run spread. And it buys
nothing at all in the case that motivated a deadline: the same five frames that
answer in 2.1 s took 12-19 s during a service-side slowdown, with identical
token counts. Queueing does not care how many images are in the queue.

WHAT THE WIDTH DOES BUY IS DEPTH. Nothing in this system measures it: relations
are geometry on 2D boxes, so a person standing IN FRONT OF a plant and a person
touching one are the same picture -- the false report found in the 2026-08-08
dry run, and the reason the judge is in front of S7 at all. Across a two-second
span those two cases move differently against the object, and that parallax is
the only depth evidence anywhere in the pipeline. Narrowing the window spends
precision to buy latency that is not there.

SO THE WINDOW MOVED INSTEAD OF SHRINKING. It used to straddle the onset
(-1.0 .. +1.0), which meant the loop had to WAIT a full second for the +1.0
frame to be captured before it could even send the request -- a second on the
critical path, every time, ahead of a call that usually takes two. The offsets
are now entirely historical, so the frames exist the moment the CV gate fires
and the request goes out immediately.

AND IT REACHES BACK FOUR SECONDS, NOT TWO, because "the onset" is not when the
event happened. It is when the CV gate finished being sure. Those are different
times, and for the relations that are about a CHANGE the gap swallows the whole
event:

    gathering debounces the person count over 1.5 s and needs two thirds of the
    window to agree, so it fires 2-3 s after somebody actually walked in.

Observed 2026-08-08. Someone entered, crossed the room, and stopped at the wall
panel; gathering fired; the five frames at -2.0 .. 0.0 showed her standing at
the panel with her back turned, and the judge said -- correctly -- that this is
not a person coming into a room. It was right three times in a row about the two
seconds it was shown. The arrival had finished before the window began.

Four seconds covers the debounce and the walk. It costs nothing: every frame is
still historical, so nothing waits, and a wider span is MORE parallax rather
than less. For a configuration question ("is that hand on the plant") the extra
reach simply shows the room before the hand arrived, which is context rather
than noise.

WHAT IS STILL LOST is the follow-through: the judge sees up to the onset, never
through it. For an ordered event ("someone came over, then left") the leaving
half has not happened yet. The `then` grammar is where that would show, and it
is worth knowing before reading a rejection of one.
"""
from __future__ import annotations

# Relative to the onset, oldest first. All <= 0: see the module docstring.
EVENT_OFFSETS_S = (-4.0, -3.0, -2.0, -1.0, 0.0)


def select_temporal_frames(buffer, onset: float):
    """-> one jpeg per offset, oldest first, nearest-in-time to each.

    `buffer` is the camera's (t, jpeg) deque. Nearest-match rather than exact:
    the deque is filled at ~10 Hz on a thread of its own, so there is no frame
    at precisely t-1.5, and refusing to answer without one would mean refusing
    to judge whenever the camera hiccupped.
    """
    snapshot = list(buffer)
    if not snapshot:
        return []
    return [min(snapshot, key=lambda item: abs(item[0] - (onset + offset)))[1]
            for offset in EVENT_OFFSETS_S]
