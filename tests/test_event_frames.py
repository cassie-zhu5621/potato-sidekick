from planning.event_frames import select_temporal_frames


def test_selects_five_ordered_frames_around_onset():
    buffer = [(t / 10.0, f"frame-{t}".encode()) for t in range(0, 31)]
    assert select_temporal_frames(buffer, 1.5) == [
        b"frame-5", b"frame-10", b"frame-15", b"frame-20", b"frame-25"
    ]


def test_empty_buffer_returns_no_evidence():
    assert select_temporal_frames([], 1.0) == []
