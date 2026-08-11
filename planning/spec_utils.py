"""
spec_utils.py — the relevance layer: how a compiled plan reaches the detector.

Three things the VLM's tiering has to do, and each one lives here:

  prompt_terms         a planner noun -> every name worth ASKING an open-vocab
                       detector for. Wide on purpose: recall is the scarce thing.
  canonical            any label -> the one name its synonym group is known by,
                       applied to every detection as it arrives so the width
                       above never reaches the layers that key on the string.
  _expand              a planner noun -> the set of CANONICAL labels that satisfy
                       it. Nearly always one name now; what is left is the
                       one-way category widening ("bag" accepts a backpack).
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

import copy
import re


# SYNONYMS EARN THEIR KEEP AT THE PROMPT AND ARE SPENT AT THE DOOR.
#
# An open-vocabulary detector finds only what it is asked for, so asking for
# several names of one thing -- "phone", "cell phone", "smartphone" -- is free
# recall, and recall is the scarce thing: a phone in a hand is small and
# half-occluded, and every missed frame restarts a sustain clock. That argues for
# a WIDE prompt.
#
# But a wide prompt means the answer can come back under any of those names, and
# two of the layers downstream are keyed on the exact string:
#
#   * `_focus_ok` compares the entry's `on` against the detected label. A box
#     labelled "smartphone" against `on: "phone"` is refused -- silently, every
#     frame, for as long as the plan lives.
#   * RelationEngine keys its hands-on clock on (pid, label). A detector that
#     flickers between "phone" and "smartphone" is two clocks, each restarting,
#     and 1.0 s of continuous contact never accumulates on either.
#
# So the widening happens ONCE, at the prompt, and is undone ONCE, on receipt:
# every detection is canonicalised to one name per group before anything else
# sees it (_FilteredDetector.detect). Downstream, matching is exact again, which
# is what makes it possible to say what an entry means.
#
# This also subsumes the COCO problem it replaces. A closed detector cannot be
# prompted, but it still says "cell phone", and canonicalising on receipt maps
# that to "phone" just the same. One mechanism, both detectors.
#
# MEMBERSHIP IS A CLAIM THAT TWO WORDS NAME THE SAME OBJECT, and it is enforced
# everywhere: put "book" and "notebook" in one group and a plan can no longer
# tell them apart. Prefer leaving a word out.
_SYNONYM_GROUPS = {
    "phone": {"cell phone", "cellphone", "mobile", "mobile phone", "smartphone"},
    "cup": {"mug", "coffee cup", "coffee mug"},
    "desk": {"table", "dining table", "desk table"},
    "monitor": {"tv", "screen", "display", "television", "computer monitor"},
    "plant": {"potted plant", "houseplant", "pot plant"},
    "couch": {"sofa"},
    "bottle": {"water bottle"},
}

# CANONICAL FORM. Built once; every alias points at its group's name, and the
# name points at itself so the lookup needs no special case.
_CANON = {}
for _name, _aliases in _SYNONYM_GROUPS.items():
    _CANON[_name] = _name
    for _a in _aliases:
        _CANON[_a] = _name

# A DIFFERENT RELATION, AND NOT SYMMETRIC. "bag" is a category: a request for it
# is satisfied by a backpack, while a request for a backpack is not satisfied by
# a handbag. So these are never canonicalised -- collapsing them would destroy
# the distinction the one-way direction exists to keep -- and are widened only
# when a PLANNER NOUN is being matched.
_HYPERNYMS = {
    "bag": {"backpack", "handbag", "suitcase"},
    "drink": {"cup", "bottle", "wine glass", "can"},
}


def canonical(label):
    """A detected or requested label -> the one name its group is known by."""
    w = str(label).strip().lower()
    return _CANON.get(w, w)


def _verbatim(nouns):
    """planner nouns -> themselves, lowercased. The narrow prompt.

    What `--no-synonym-prompt` asks for: exactly the words the plan used, and
    nothing invented. Kept beside prompt_terms so the two are read together --
    the only difference between them is width, and which one is right is a
    question about this detector on this rig rather than about the code.
    """
    return {str(w).strip().lower() for w in (nouns or []) if str(w).strip()}


def prompt_terms(nouns):
    """planner nouns -> every surface form worth asking an open-vocab model for.

    Only the prompt is widened. Asking for five names of one object costs a
    little inference time and buys frames in which it is found; nothing
    downstream has to cope, because the answers are canonicalised on the way in.
    """
    out = set()
    for w in nouns or []:
        w = str(w).strip().lower()
        if not w:
            continue
        out.add(w)
        c = _CANON.get(w)
        if c:
            out.add(c)
            out |= _SYNONYM_GROUPS.get(c, set())
        out |= _HYPERNYMS.get(w, set())
    return out


COCO_SYNONYMS = _HYPERNYMS      # the old public name; kept for any outside reader


# ------------------------------------------------ words the judge cannot check --
# A word here names a place RELATIVE TO A VIEWPOINT. The planner resolves it once,
# at plan time, against every view it swept -- "the right board" becomes one box in
# one view -- and the CV gate then requires the wrist to land inside THAT box. By
# the time a candidate reaches the judge the word has been checked by geometry.
#
# The judge, though, is handed five frames and no pan angle, so it cannot recover
# which way the head was pointing. It does not decline: it re-resolves the word
# against THE PICTURE, which is a different question with a different answer.
#
# Measured 2026-08-08 on judge_group_20260808_183055 -- five frames of someone
# drawing on the board the plan named "right whiteboard", which sits at x 0.00-0.47
# of that view because the head had panned toward it:
#
#     "draws on the right board"                  pass=False, twice
#         "drawing on the LEFT whiteboard, while the whiteboard on the right
#          remains untouched"
#     "draws on the board"                        pass=True, twice
#     "draws on the right board" + a paragraph telling it the head turns and to
#     ignore left/right                           pass=False, twice
#
# THE THIRD ROW IS WHY THIS IS A WORD LIST AND NOT A SENTENCE IN THE PROMPT. Told
# plainly to disregard the word, the model still refused, and still narrated the
# empty board as the one that had been asked about. The word has to be gone.
_VIEWPOINT_WORDS = (
    "left", "right", "left-hand", "right-hand", "leftmost", "rightmost",
    "far", "near", "nearer", "nearest", "farther", "further", "farthest",
    "furthest", "other", "opposite", "closer", "closest",
)
_W = "|".join(_VIEWPOINT_WORDS)
_TAIL = r"(?:\s+hand)?(?:\s+side)?"

# Ordered. Each removes as little as it can while leaving a sentence behind: the
# text is going into a prompt, and a mangled one costs more than the word did.
_VIEWPOINT_SUBS = (
    # "...on the left side of the table" -> "...on the table". The prepositional
    # phrase is doing the deixis; the object it attaches to is not.
    (re.compile(r"\b(on|to|at|in)\s+the\s+(?:%s)%s\s+of\s+" % (_W, _TAIL), re.I),
     r"\1 "),
    # "...the person on the left" -- nothing follows, so the whole phrase goes or
    # the sentence ends on a preposition.
    (re.compile(r"[,\s]+(?:on|to|at|in)\s+the\s+(?:%s)%s(?=\s*(?:[,.;!?]|$))" % (_W, _TAIL),
                re.I), ""),
    # "the right board" -> "the board". The common case, and the measured one.
    (re.compile(r"(?<!each )\b(?:%s)%s\s+(?=\w)" % (_W, _TAIL), re.I), ""),
)


def viewpoint_free(text):
    """A request or label -> the same thing with viewpoint-relative words removed.

    Only for text shown to the JUDGE. The planner must keep every word: resolving
    them is its job, and it is the one stage that sees more than one view.

    Deliberately blunt. "the right board" -> "the board", which is exactly the
    claim geometry has already narrowed to one object; a judge that passes it is
    saying the ACTION is real, which is the half it can see. It is not a spelling
    correction, so nothing tries to keep the sentence pretty.

    NOT REMOVED: "in front of", "behind", "on top of". Those are relations between
    two things in the scene and hold from any viewpoint -- and "in front of" is the
    depth confusion the judge exists to catch, so deleting it would remove the
    question rather than the ambiguity.
    """
    out = str(text or "")
    for pattern, repl in _VIEWPOINT_SUBS:
        out = pattern.sub(repl, out)
    return re.sub(r"\s+([,.;!?])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()


def _expand(labels):
    """planner nouns -> the set of CANONICAL labels that satisfy them.

    An OR, as it always was: any one of these counts. Aliases no longer appear
    because canonical() resolved them at the detector; what remains is the
    category widening, which cannot be resolved that way without losing the
    distinction it exists to preserve.
    """
    out = set()
    for w in labels or []:
        w = str(w).strip().lower()
        if not w:
            continue
        out.add(canonical(w))
        out |= {canonical(s) for s in _HYPERNYMS.get(w, set())}
    return out


def _canonicalised(d):
    """A detection with its label resolved to the group name, or unchanged.

    THE ONE PLACE ALIASES DIE. Every box in the system comes through
    _FilteredDetector.detect, so doing it here means nothing downstream -- the
    focus gate, the hands-on clock, the whitelist, the overlay, the storyboard --
    has to know that "smartphone" and "phone" were ever two words. Resolving it
    in each of them instead is how they would come to disagree.

    Returns the object untouched when there is nothing to rename, so the common
    case allocates nothing and any extra fields a detector attaches survive.
    """
    canon = _CANON.get(str(d.label).strip().lower())
    if canon is None or canon == d.label:
        return d
    out = copy.copy(d)
    out.label = canon
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
        dets = [_canonicalised(d) for d in self.base.detect(image)]
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
    object relations (lean/hands — no viz target) pass through. Empty target set -> allow all.

    Both sides are canonical by the time they meet: the labels because
    _canonicalised renamed them at the detector, `on` because _expand does."""
    on = e.get("on")
    want = _expand([on] if isinstance(on, str) else on) if on else set(focus)
    if not want:
        return True
    ids = set(e.get("all", [])) | set(e.get("any", [])) | set(e.get("then", []))
    object_directed = ids & {1, 4, 7, 8, 9, 11}
    if not object_directed:
        return True
    dets = viz.get("dets", [])
    targets = set()
    allowed_hits = set()
    if 1 in ids: allowed_hits.add("gazing-at")
    if 4 in ids: allowed_hits.add("pointing-at")
    if 8 in ids: allowed_hits.add("lean-in")
    for kind, h in viz.get("hits", []):
        if kind not in allowed_hits:
            continue
        di = h.get("det") if isinstance(h, dict) else None
        if isinstance(di, int) and 0 <= di < len(dets):
            targets.add(str(dets[di].label).lower())
    if 9 in ids:
        targets.update(str(label).lower() for _, label in viz.get("handson", []))
    if 11 in ids:
        targets.update(str(row[0]).lower() for row in viz.get("handoff", []))
    if 7 in ids:
        targets.update(str(label).lower() for label in viz.get("approach", []))
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
