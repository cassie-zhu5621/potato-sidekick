"""
watch_exec.py — executes a planner watch-spec over the per-frame truth vector.

Semantics (the contract with planner.py / relation_table.md):
  - a relation id is HELD at time t if it has been True for >= `persist` consecutive
    frames, and its last held-moment is within the entry's `within_s` window.
  - entry fields:  all  — every id held within the window
                   any  — at least one id held within the window
                   not  — NONE of these held within the window (suppression)
                   then — ids BECAME held in this order, all within the window
  - entries in `watch` are alternatives (OR): each can fire independently.
  - `single_ok` ids act as 1-id entries with the default window.
  - a fired entry enters `cooldown` seconds of habituation (same moment != news twice);
    holding a satisfied entry does NOT re-fire it — it must break, then re-form.

All timing is in SECONDS (fps-independent: M5 ~2fps and a 30fps webcam both work).
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class EntryStatus:
    label: str
    satisfied: bool
    cooling: bool
    cooldown_remaining_s: float
    detail: str          # human-readable progress, e.g. "held: 9 · waiting: 8"


class WatchExecutor:
    def __init__(self, spec: dict, persist: int = 2, cooldown: float = 15.0,
                 default_within: float = 2.0, tau_gap: float = 3.0, then_cd_mult: float = 1.0):
        self.persist, self.cooldown = persist, cooldown
        self.tau_gap = tau_gap            # THEN-gate: min onset gap to count as an ORDERED event
        self.then_cd_mult = then_cd_mult  # a true ordered 'then' gets a longer cooldown
        self.entries = []
        for c in spec.get("watch", []) or []:
            self.entries.append({
                "all": list(c.get("all", []) or []), "any": list(c.get("any", []) or []),
                "not": list(c.get("not", []) or []), "then": list(c.get("then", []) or []),
                "on": c.get("on"),                     # object this entry is about (v0.5 scoping)
                "within": float(c.get("within_s", default_within)),
                "label": c.get("label", "watch"),
            })
        # Gemini occasionally emits both watch:[{all:[9]}] and single_ok:[9].
        # They are the same trigger, and running both launches two Judge calls
        # with contradictory labels. Keep single_ok only when it adds a genuinely
        # new alternative.
        exact_singletons = {
            e["all"][0] for e in self.entries
            if len(e["all"]) == 1 and not e["any"] and not e["not"] and not e["then"]
        }
        for rid in spec.get("single_ok", []) or []:
            if rid in exact_singletons:
                continue
            self.entries.append({"all": [rid], "any": [], "not": [], "then": [], "on": None,
                                 "within": default_within, "label": f"single:{rid}"})
        # per-relation timing state
        self._streak: Dict[int, int] = {}
        self._last_held: Dict[int, float] = {}       # most recent time the id was held
        self._became: Dict[int, float] = {}          # when the CURRENT hold episode began
        # per-entry state
        self._fired_at = [-1e9] * len(self.entries)
        self._fired_cd = [cooldown] * len(self.entries)   # cooldown actually applied to last fire
        self._was_sat = [False] * len(self.entries)

    # ------------------------------------------------------------------ #
    def step(self, truth: Dict[int, bool], t: Optional[float] = None):
        """truth: {relation_id: bool} for THIS frame. Returns (fired, statuses)."""
        t = time.time() if t is None else t
        for rid, val in truth.items():
            if val:
                s = self._streak.get(rid, 0) + 1
                self._streak[rid] = s
                if s == self.persist:                 # hold episode starts NOW
                    self._became[rid] = t
                if s >= self.persist:
                    self._last_held[rid] = t
            else:
                self._streak[rid] = 0

        fired, statuses = [], []
        for i, e in enumerate(self.entries):
            sat, detail, ordered = self._satisfied(e, t)
            cooling = t - self._fired_at[i] < self._fired_cd[i]
            if sat and not self._was_sat[i] and not cooling:
                self._fired_at[i] = t
                # a genuinely ORDERED then gets a longer cooldown (rarer, more report-worthy)
                self._fired_cd[i] = self.cooldown * (self.then_cd_mult if (e["then"] and ordered)
                                                     else 1.0)
                fired.append(e)
                cooling = True
            self._was_sat[i] = sat
            remaining = (max(0.0, self._fired_cd[i] - (t - self._fired_at[i]))
                         if cooling else 0.0)
            statuses.append(EntryStatus(e["label"], sat, cooling, remaining, detail))
        return fired, statuses

    # ------------------------------------------------------------------ #
    def _held_within(self, rid: int, t: float, win: float) -> bool:
        return t - self._last_held.get(rid, -1e9) <= win

    def _satisfied(self, e: dict, t: float):
        win = e["within"]
        held = [r for r in e["all"] if self._held_within(r, t, win)]
        ok_all = len(held) == len(e["all"])
        ok_any = (not e["any"]) or any(self._held_within(r, t, win) for r in e["any"])
        ok_not = not any(self._held_within(r, t, win) for r in e["not"])
        ok_then, then_detail, ordered = True, "", False
        if e["then"]:
            times = [self._became.get(r, None) for r in e["then"]]
            recent = [x for x in times if x is not None and t - x <= win]
            all_recent = all(x is not None and t - x <= win for x in times)
            # THEN-gate: a REAL ordered event needs a visible onset gap (>= tau_gap) at each step,
            # not a 1-frame lead that is inside the perception jitter.
            ordered = (all_recent
                       and all(times[k + 1] - times[k] >= self.tau_gap
                               for k in range(len(times) - 1)))
            # A sequence is never silently weakened to AND. Reversed or simultaneous
            # relations are not the event the planner requested.
            ok_then = ordered
            tag = "ordered" if ordered else "…"
            then_detail = f" · seq {len(recent)}/{len(e['then'])} ({tag})"
        waiting = [r for r in e["all"] if r not in held]
        detail = (f"held {held}" if held else "") + (f" · waiting {waiting}" if waiting else "")
        if e["not"] and not ok_not:
            detail += " · BLOCKED by not()"
        detail += then_detail
        return (ok_all and ok_any and ok_not and ok_then), detail.strip(" ·"), ordered


if __name__ == "__main__":
    # quick self-test (full tests live in the sandbox suite)
    spec = {"watch": [{"all": [9, 8], "within_s": 2, "label": "focused work"},
                      {"then": [10, 6], "within_s": 10, "label": "arrival then chat"}],
            "single_ok": [3]}
    ex = WatchExecutor(spec, persist=2, cooldown=5)
    t0 = 0.0
    for k in range(8):
        truth = {i: False for i in range(1, 12)}
        if k >= 1: truth[10] = True            # 10 becomes held at k=2 (persist 2)
        if k >= 4: truth[6] = True             # 6 becomes held at k=5 -> sequence ok
        fired, st = ex.step(truth, t0 + k)
        print(k, [e["label"] for e in fired], "|", "; ".join(f"{s.label}:{int(s.satisfied)}" for s in st))
