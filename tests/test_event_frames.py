"""The judge's evidence window: five frames, two seconds, all already captured.

The window used to straddle the onset (-1.0 .. +1.0). That put a full second on
the critical path -- the loop could not send the request until the +1.0 frame had
been taken -- in front of a call whose median is 2.1 s.

Narrowing to three frames was the other candidate and was measured instead of
argued: replaying one recorded call four times at each width gave medians of
2.3 s (5 frames), 2.0 s (3), 1.8 s (1). Inside the run-to-run spread, and worth
nothing at all during the service-side slowdowns that motivated the deadline,
where the same five frames went from 2.1 s to 12-19 s with identical token
counts.

So the window MOVED rather than shrank. Same count, same 2.0 s span, same
parallax -- and parallax is the whole point: nothing in this system measures
depth, so a person in front of a plant and a person touching one are the same
picture to a wrist-in-box test, and only their different motion against the
object over a couple of seconds separates them. That is the false report the
2026-08-08 dry run found, and the reason the judge sits in front of S7 at all.
"""
from planning.event_frames import EVENT_OFFSETS_S, select_temporal_frames


def test_the_window_is_entirely_in_the_past():
    """The property the change exists for. One positive offset and the loop is
    back to waiting for a frame that has not happened yet."""
    assert max(EVENT_OFFSETS_S) <= 0.0, EVENT_OFFSETS_S
    assert EVENT_OFFSETS_S[-1] == 0.0, "the onset itself has to be in there"


def test_the_span_reaches_back_past_the_debounce():
    """THE ONSET IS NOT WHEN THE EVENT HAPPENED. It is when the CV gate finished
    being sure, and for a change-type relation the two are seconds apart:
    gathering debounces the person count over 1.5 s at two-thirds agreement, so
    it fires 2-3 s after somebody walked in.

    Observed 2026-08-08 -- someone entered, crossed the room and stopped at the
    wall panel; the five frames at -2.0 .. 0.0 showed her already at the panel
    with her back turned, and the judge correctly said that is not an arrival.
    Three times. The window has to be older than the slowest debounce in the
    grammar or it cannot contain the event it is asked about."""
    span = max(EVENT_OFFSETS_S) - min(EVENT_OFFSETS_S)
    assert span >= 4.0, (
        f"{span}s does not reach past gathering's 1.5 s window plus the walk "
        f"that precedes it")


def test_five_frames_evenly_spaced():
    gaps = [round(b - a, 6) for a, b in zip(EVENT_OFFSETS_S, EVENT_OFFSETS_S[1:])]
    assert len(EVENT_OFFSETS_S) == 5
    assert len(set(gaps)) == 1, gaps


def test_selects_five_ordered_frames_up_to_the_onset():
    buffer = [(t / 10.0, f"frame-{t}".encode()) for t in range(0, 51)]
    assert select_temporal_frames(buffer, 5.0) == [
        b"frame-10", b"frame-20", b"frame-30", b"frame-40", b"frame-50"
    ]


def test_nearest_match_rather_than_exact():
    """The deque is filled at ~10 Hz on its own thread, so there is no frame at
    precisely t-1.5. Refusing to answer without one would mean refusing to judge
    whenever the camera hiccupped."""
    buffer = [(0.0, b"a"), (2.9, b"b"), (4.1, b"c")]
    out = select_temporal_frames(buffer, 4.0)
    assert out[-1] == b"c"            # onset -> nearest is 4.1
    assert out[0] == b"a"             # -4.0 -> nearest is 0.0


def test_a_short_buffer_still_answers():
    """Early in a run the deque holds less than the window. Every offset then
    resolves to the same few frames, which is a thin judgement rather than no
    judgement -- and the alternative is dropping the first finding of a session."""
    assert len(select_temporal_frames([(0.0, b"only")], 0.0)) == 5


def test_empty_buffer_returns_no_evidence():
    assert select_temporal_frames([], 1.0) == []


def test_the_loop_no_longer_waits_for_the_window():
    """`due` was onset + 1.0 because the last frame had not been captured yet.
    With the window historical it must be the onset itself, or the second is
    still being spent for nothing."""
    import noticebot_loop
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index('"entries": entries, "onset": now,')
    assert '"due": now,' in src[i:i + 80], src[i:i + 80]


def test_the_camera_buffer_outlives_the_window():
    """Nearest-match always returns something, so a deque shorter than the
    window fails silently: the oldest offset quietly resolves to whatever is
    still in memory, and the judge is shown a narrower event than it was asked
    about with nothing saying so."""
    import noticebot_loop
    src = noticebot_loop.__loader__.get_source("noticebot_loop")
    i = src.index("self.event_frames = deque(maxlen=")
    maxlen = int(src[i:].split("maxlen=")[1].split(")")[0])
    seconds = maxlen * 0.1          # the capture thread appends at ~10 Hz
    assert seconds >= abs(min(EVENT_OFFSETS_S)) + 2.0, (
        f"the deque holds {seconds:.1f}s and the window reaches "
        f"{abs(min(EVENT_OFFSETS_S)):.1f}s back -- too little slack")
