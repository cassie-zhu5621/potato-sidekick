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
  - a fired entry enters `cooldown` seconds of habituation (same moment != news twice).
    THE COOLDOWN IS THE ONLY THING THAT HOLDS IT BACK. A satisfied entry re-fires
    as soon as its cooldown ends, whether or not the relation ever broke.

    It used to also require a fresh rising edge — break, then re-form. Removed on
    2026-08-12 at Cassie's decision, on her reasoning about what actually happens
    in the room: an actor does not stand there doing the same thing, they finish
    and leave, so nothing real is protected by demanding a release. What the
    requirement DID do was let one accidental trigger — somebody sitting nearby
    doing something else — latch the entry for as long as their hand stayed put,
    and the genuine event at that spot afterwards could never fire. The page
    showed this as an entry stuck on "held · release to rearm".

All timing is in SECONDS (fps-independent: M5 ~2fps and a 30fps webcam both work).
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def order_coincident_candidates(entries):
    """One moment, one deterministically ordered Judge batch.

    Prefer the more specific composed rule; ties preserve Planner order, which
    is already defined as most-important-first. This keeps arbitration
    deterministic while all entries still share one VLM call.
    """
    def specificity(entry):
        ids = (set(entry.get("all", [])) | set(entry.get("any", []))
               | set(entry.get("then", [])) | set(entry.get("not", [])))
        return (1 if entry.get("then") else 0, len(ids))

    return [entry for _, entry in sorted(
        enumerate(entries), key=lambda pair: (-specificity(pair[1])[0],
                                               -specificity(pair[1])[1], pair[0]))]


def _entry_sig(e):
    """A watch entry's identity, stable across `dict(e)` copies.

    Deliberately excludes `within` and `label`: the judge and the storyboard
    both rewrite the label on their copy (the participant-facing sentence), and
    a card re-worded downstream is still the same card to watch state.
    """
    return (tuple(e.get("all") or []), tuple(e.get("any") or []),
            tuple(e.get("not") or []), tuple(e.get("then") or []),
            (e.get("on") or "").strip().lower())


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
        self.blocked = []            # entries whose relation held on the wrong object
        self._rejects = [0] * len(self.entries)   # consecutive judge rejections

    # ------------------------------------------------------------------ #
    def step(self, truth: Dict[int, bool], t: Optional[float] = None, ok=None):
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
        self.blocked = []
        for i, e in enumerate(self.entries):
            sat, detail, ordered = self._satisfied(e, t)
            # THE OBJECT FILTER IS PART OF THE CONDITION, not a review of the
            # decision. `ok` is the caller's per-entry test -- in the live loop,
            # `_focus_ok`: did the contact/gaze land on the object THIS card
            # names. It used to run AFTER step() returned, and by then the damage
            # was done: `_fired_at` had started a 15 s cooldown for a card that
            # never fired.
            #
            # The relations are global -- truth[9] is "a hand is on SOMETHING" --
            # so a hand resting on the wrong object holds that bit true, the edge
            # never returns, and the card sits in a cooldown it never earned.
            # Reported 2026-08-12 as "there is a suppressed cooldown and it is
            # cooling forever". Folded in here, a wrong object simply means the
            # entry is not satisfied, which is what it always meant.
            if sat and ok is not None and not ok(e):
                sat = False
                self.blocked.append(e)
            # THE SITUATION CHANGED, so a run of rejections about it is over.
            if not sat:
                self._rejects[i] = 0
            cooling = t - self._fired_at[i] < self._fired_cd[i]
            # NO EDGE REQUIRED -- see the module docstring. `sat and not cooling`
            # is the whole rule.
            if sat and not cooling:
                self._fired_at[i] = t
                # a genuinely ORDERED then gets a longer cooldown (rarer, more report-worthy)
                self._fired_cd[i] = self.cooldown * (self.then_cd_mult if (e["then"] and ordered)
                                                     else 1.0)
                fired.append(e)
                cooling = True
            remaining = (max(0.0, self._fired_cd[i] - (t - self._fired_at[i]))
                         if cooling else 0.0)
            statuses.append(EntryStatus(e["label"], sat, cooling, remaining, detail))
        return fired, statuses

    def _index_of(self, entry) -> int:
        """Which of our entries is this -- by CONTENT, not by identity.

        `fired` hands out the executor's own dicts, but nothing downstream keeps
        them: the loop immediately does `[dict(e) for e in ...]` so the judge and
        the storyboard can annotate a candidate without writing into watch state.
        By the time that copy comes back to `recool`, `e is entry` is false for
        every entry and it silently returned False.

        Reported 2026-08-12 as "suppressed still shows 14 s" -- the refund had
        been written, tested against the original object, and never once ran in
        the live loop. Matching on the entry's content survives the copy.

        Duplicates cannot be ambiguous here: the planner de-duplicates on this
        same signature, so two entries with it would be the same card twice.
        """
        for i, e in enumerate(self.entries):
            if e is entry:
                return i
        sig = _entry_sig(entry)
        for i, e in enumerate(self.entries):
            if _entry_sig(e) == sig:
                return i
        return -1

    def recool(self, entry, seconds, backoff=False):
        """Shorten the cooldown a fire is currently serving. -> did it apply.

        A CANDIDATE THAT REACHED NOBODY IS NOT A DELIVERED REPORT. The full
        cooldown exists so the same moment is not announced twice; a moment the
        judge threw out, or one the busy gate dropped, was never announced at
        all, and the relation that produced it is usually still true -- somebody
        standing near the plant they are not touching. Charging fifteen seconds
        means the real contact seconds later is refused as well.

        Not a refund, though, and this became the load-bearing part on
        2026-08-12 when firing stopped requiring a fresh edge. Setting the
        cooldown to zero now means the entry fires again on the very NEXT frame,
        into the same busy gate or at the same judge that just said no, once per
        frame until something changes. A short cooldown is the only throttle
        left.

        `backoff` doubles it per consecutive rejection, capped at the normal
        cooldown: 4 -> 8 -> 15 -> 15. Without it a relation that is continuously
        true and continuously wrong -- leaning on the whiteboard while the card
        says drawing on it -- would occupy the judge every four seconds for as
        long as the person stood there, and the gate it occupies is the one every
        other card has to pass through. The counter resets in `step` the moment
        the entry stops being satisfied, because that is the situation changing.
        """
        i = self._index_of(entry)
        if i < 0:
            return False
        if backoff:
            self._rejects[i] += 1
            seconds = min(self.cooldown, seconds * (2 ** (self._rejects[i] - 1)))
        self._fired_cd[i] = float(seconds)
        return True

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
