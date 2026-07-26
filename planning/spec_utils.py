"""
spec_utils.py — the relevance layer: how a compiled plan reaches the detector.

Three things the VLM's tiering has to do, and each one lives here:

  _expand              a planner noun -> every label a detector might emit for it
                       ("mug" also matches COCO's "cup"). Without this the
                       whitelist silently drops the object it was asked to watch.
  _FilteredDetector    wraps any detector so the live `relevance` dict decides
                       what survives. Open-vocab models get re-prompted through
                       set_vocab; closed COCO YOLO gets filtered. `person` is
                       never dropped -- people drive every social relation.
  _focus_ok            the far end of the same idea: a gaze or a point may only
                       OPEN a story when it lands on a FOCUS object. Without it,
                       "someone looked at something" fires on every chair in the
                       room.

`spec_summary` formats a compiled watch-spec for the panel and the log.

Lifted out of attention_system.py unchanged. The spec these implement is
docs/RELEVANCE_THEN_SPEC.md.
"""
from __future__ import annotations



COCO_SYNONYMS = {
    "desk": {"dining table"}, "table": {"dining table"},
    "monitor": {"tv"}, "screen": {"tv"}, "display": {"tv"}, "television": {"tv"},
    "bag": {"backpack", "handbag", "suitcase"}, "handbag": {"handbag"}, "backpack": {"backpack"},
    "phone": {"cell phone"}, "mobile": {"cell phone"}, "cellphone": {"cell phone"},
}

def _expand(labels):
    """planner nouns -> the set of lowercase labels that satisfy them (forgiving for closed YOLO)."""
    out = set()
    for w in labels or []:
        w = str(w).strip().lower()
        if not w:
            continue
        out.add(w)
        out |= COCO_SYNONYMS.get(w, set())
    return out

class _FilteredDetector:
    """Wraps a detector; drops any box whose label is not in relevance['allow'] (when set).
    `allow` is a live set, so re-planning updates the whitelist with no detector rebuild.
    'person' is always kept — people are the subject of every social relation."""
    def __init__(self, base, relevance):
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "rel", relevance)
        object.__setattr__(self, "_ver", -1)          # last set_vocab version applied

    def detect(self, image):
        rel = self.rel
        # open-vocab detectors (yoloworld / gdino) expose set_vocab -> re-prompt when the plan
        # changes the object set. Closed COCO YOLO has no set_vocab -> the whitelist below does it.
        want = rel.get("want_classes")
        if want and rel.get("classes_ver", 0) != self._ver and hasattr(self.base, "set_vocab"):
            try:
                self.base.set_vocab(want)
                object.__setattr__(self, "_ver", rel["classes_ver"])
                print(f"[relevance] detector re-prompted -> {sorted(want)}")
            except Exception as ex:
                print(f"[relevance] set_vocab failed: {ex}")
        dets = self.base.detect(image)
        allow = rel.get("allow")
        if not allow:
            return dets
        return [d for d in dets if str(d.label).lower() in allow or d.label == "person"]

    def __getattr__(self, k):
        return getattr(object.__getattribute__(self, "base"), k)

def _focus_ok(e, viz, focus):
    """Focus gate at story-open: an object gaze/point entry may only OPEN a story when its target
    is the right object. If the entry names `on`, that specific object is required; otherwise any
    focus object. Person-centric entries (approach/gather/joint/prox/F-form/turn) and truth-only
    object relations (lean/hands — no viz target) pass through. Empty target set -> allow all."""
    on = e.get("on")
    want = _expand([on] if isinstance(on, str) else on) if on else set(focus)
    if not want:
        return True
    ids = set(e.get("all", [])) | set(e.get("any", [])) | set(e.get("then", []))
    if not (ids & {1, 4}):                     # no gaze/point target to verify -> people/lean/hands: allow
        return True
    dets = viz.get("dets", [])
    targets = set()
    for _, h in viz.get("hits", []):
        di = h.get("det") if isinstance(h, dict) else None
        if isinstance(di, int) and 0 <= di < len(dets):
            targets.add(str(dets[di].label).lower())
    return bool(targets & want)

def spec_summary(spec):
    out = []
    for c in spec.get("watch", []) or []:
        bits = []
        if c.get("all"):  bits.append("+".join(map(str, c["all"])))
        if c.get("any"):  bits.append("any(" + ",".join(map(str, c["any"])) + ")")
        if c.get("then"): bits.append("then(" + "→".join(map(str, c["then"])) + ")")
        if c.get("not"):  bits.append("not(" + ",".join(map(str, c["not"])) + ")")
        out.append((" ".join(bits), c.get("label", "")))
    return out