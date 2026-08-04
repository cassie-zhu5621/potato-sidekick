"""Select ordered temporal evidence around a CV candidate onset."""
from __future__ import annotations

EVENT_OFFSETS_S = (-1.0, -0.5, 0.0, 0.5, 1.0)


def select_temporal_frames(buffer, onset: float):
    snapshot = list(buffer)
    if not snapshot:
        return []
    return [min(snapshot, key=lambda item: abs(item[0] - (onset + offset)))[1]
            for offset in EVENT_OFFSETS_S]
