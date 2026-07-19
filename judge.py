"""
judge.py — the VLM JUDGMENT BRAIN (step ④ of the flow).

Runs ONLY on candidates the cheap gate let through, ONCE per event. It looks at the real
IMAGE (its strength) with the stable relation structure as grounding ("here is what is
structurally new"), and returns:
    { worth, why, note }
where `note` is the human-readable field note (the deliverable).

Taste here is NOT the old 9 Berlyne dimensions. Novelty/surprise is already the gate's job
(Event Segmentation). What's left for the VLM is REPORTABILITY — which already-an-event
moments are worth recounting to THIS person. So taste is a small, legible, editable set of
reportability axes, grounded in tellability (Labov 1972; Bruner 1991 'breach of canonical
script') and news values (Galtung & Ruge 1965; Harcup & O'Neill). The user edits these axes
in real time (see ReportabilityTaste.nudge) — "compilable taste", and the same channel by
which they consume the feed.

Offline mode (SECONDATTN_OFFLINE=1) returns deterministic fake scores so the whole pipeline
runs end-to-end with no API key — for wiring tests and for you to plug your rig into.
"""

from __future__ import annotations
import os, re, json, base64, hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

MODEL = os.environ.get("SECONDATTN_JUDGE_MODEL", "claude-haiku-4-5")


# --------------------------------------------------------------------------- #
# the compilable reportability taste
# --------------------------------------------------------------------------- #
# axis -> (rubric shown to the VLM, default weight)
AXES = {
    "people":      ("people / social presence — someone is here, involved, interacting", 1.0),
    "relevance":   ("relevance to what this person cares about / their things / this space", 1.0),
    "consequence": ("consequence or trouble — something with stakes (a spill, left-behind, broken, changed)", 1.0),
    "continuity":  ("a follow-up on something noticed before (a thread continuing)", 0.5),
}
# words that nudge an axis up/down when the user talks to it
_AXIS_WORDS = {
    "people":      ["people", "person", "someone", "social", "human", "faces", "gather", "together"],
    "relevance":   ["relevant", "mine", "my", "matters", "important", "us"],  # not "care": collides with "care about X"
    "consequence": ["trouble", "problem", "spill", "broken", "left", "stakes", "wrong", "mess", "consequence"],
    "continuity":  ["follow", "again", "continue", "update", "progress", "thread", "ongoing"],
}
_POS = ["more", "love", "like", "want", "care", "yes", "good", "keep"]
_NEG = ["less", "no", "not", "stop", "ignore", "avoid", "don't", "dont", "fewer", "hate"]


@dataclass
class ReportabilityTaste:
    weights: Dict[str, float] = field(default_factory=lambda: {a: w for a, (_, w) in AXES.items()})
    about: str = ""                       # free-text lean, e.g. "the robotics corner"
    lo: float = 0.0
    hi: float = 2.0
    lr: float = 0.4

    def compose(self, scores: Dict[str, float]) -> float:
        num = sum(self.weights.get(a, 0.0) * scores.get(a, 0.0) for a in AXES)
        den = sum(abs(self.weights.get(a, 0.0)) for a in AXES) or 1.0
        return num / den

    def why(self, scores: Dict[str, float]) -> str:
        ranked = sorted(((self.weights.get(a, 0.0) * scores.get(a, 0.0), a) for a in AXES),
                        reverse=True)
        return ", ".join(a for _, a in ranked[:2])

    def nudge(self, sentence: str) -> dict:
        """Real-time compile: a user sentence -> a delta on the axis weights (and 'about').
        'more people, less clutter' / 'I care about the robotics corner'."""
        toks = re.findall(r"[a-z']+", sentence.lower())
        delta = {}
        for i, tok in enumerate(toks):
            for a, words in _AXIS_WORDS.items():
                if tok in words:
                    val = 1.0
                    for w in reversed(toks[max(0, i - 4):i]):
                        if w in _NEG: val = -1.0; break
                        if w in _POS: val = 1.0; break
                    delta[a] = val
        for a, dv in delta.items():
            self.weights[a] = max(self.lo, min(self.hi, self.weights[a] + self.lr * dv))
        # a "(I) care about X" with no axis word sets the free-text lean
        m = re.search(r"(?:care about|interested in|watch|about)\s+(.*)", sentence.lower())
        if m and not delta:
            self.about = m.group(1).strip(" .")
        return delta


# --------------------------------------------------------------------------- #
# relation structure -> short text grounding for the prompt
# --------------------------------------------------------------------------- #
def relations_text(graph, delta_added=None) -> str:
    """Compact 'what's here / what's new' line from the scene graph (+ optional event delta)."""
    def fmt(e): return f"{graph.nodes.get(e[0], e[0])} {e[1]} {graph.nodes.get(e[2], e[2])}"
    if delta_added:
        new = "; ".join(fmt(m) for m, _ in delta_added[:6])
        return f"new structure: {new}" if new else ""
    return "; ".join(fmt(e) for e in graph.edges[:10])


# --------------------------------------------------------------------------- #
# the judge
# --------------------------------------------------------------------------- #
def _offline(jpeg: bytes, rel: str, taste: ReportabilityTaste) -> dict:
    h = hashlib.md5((rel + taste.about).encode() + (jpeg[:1500] if jpeg else b"")).hexdigest()
    scores = {a: (int(h[i*3:i*3+3], 16) % 1000) / 1000.0 for i, a in enumerate(AXES)}
    return {"axes": scores, "note": f"[offline] {rel[:48]}" or "a quiet moment"}


def _prompt(rel: str, taste: ReportabilityTaste, confirm: str = "", story: str = "") -> str:
    lines = [
        "You are the noticing companion's judgment brain for a camera placed in a shared space.",
        "This moment already passed a novelty gate (something structurally changed), so do NOT",
        "re-judge novelty. Judge how REPORTABLE it is — worth recounting to the person — on these",
        "axes, each 0.0-1.0:",
    ]
    for a, (rubric, _) in AXES.items():
        lines.append(f"  - {a}: {rubric}")
    if taste.about.strip():
        lines.append(f'The person especially cares about: "{taste.about.strip()}".')
    if rel:
        lines.append(f"\nStructured grounding — {rel}")
    if confirm:
        lines.append(f'\nFIRST, verify against the image: does it actually show "{confirm}"?'
                     " The geometry is a cheap 2D estimate and can be fooled by depth (a ray"
                     " passing IN FRONT of an object is not attention to it). If the image does"
                     ' not support it, set "confirmed": false and say why in the note.')
    if story:
        multi = "→" in story
        if multi:
            lines.append(f"\nThe image is a comic strip (left-to-right) of a short story. The grounded"
                         f" sequence the system DETECTED, in order: {story}. Recount briefly what"
                         f" happened ACROSS the panels. Describe ONLY what the panels actually show —"
                         f' do NOT invent a "then..." step that is not visible in any panel.')
        else:
            lines.append(f"\nThe image is a SINGLE frame — one instant, NOT a sequence. Detected here:"
                         f" {story}. Describe ONLY what is visible in this one frame. Do NOT narrate any"
                         f' before/after, arrival, or "then moved..." — there is no evidence for it.')
    js = '{"axes": {"people":0-1,"relevance":0-1,"consequence":0-1,"continuity":0-1}, '
    js += '"confirmed": true|false, ' if confirm else ''
    js += '"note": "<one field note, <=16 words>"}'
    lines.append(f"\nReturn ONLY JSON: {js}")
    return "\n".join(lines)


def judge(jpeg: Optional[bytes], graph, taste: ReportabilityTaste,
          delta_added=None, model: str = MODEL, confirm: str = "", story: str = "") -> dict:
    """Judge one gated moment. Returns {worth, why, note, axes, confirmed}.
    `confirm`: optional relation claim (e.g. "a person gazing at the cup") — the VLM first
    VERIFIES it against the image (the precision half of the gate→VLM split for the
    designed-relation branch); unconfirmed moments come back with worth=0."""
    rel = relations_text(graph, delta_added) if graph is not None else ""
    if os.environ.get("SECONDATTN_OFFLINE") == "1" or jpeg is None:
        out = _offline(jpeg, story or rel, taste)       # offline: narrate from the trace if present
        if story:
            out["note"] = f"[offline] {story[:64]}"
        out["confirmed"] = True
    else:
        import anthropic
        client = anthropic.Anthropic()
        b64 = base64.standard_b64encode(jpeg).decode()
        msg = client.messages.create(
            model=model, max_tokens=200,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": _prompt(rel, taste, confirm, story)}]}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        try:
            s, e = text.index("{"), text.rindex("}") + 1
            raw = json.loads(text[s:e])
            out = {"axes": {a: float(raw.get("axes", {}).get(a, 0.0)) for a in AXES},
                   "note": str(raw.get("note", ""))[:120],
                   "confirmed": bool(raw.get("confirmed", True))}
        except Exception:
            out = {"axes": {a: 0.0 for a in AXES}, "note": f"parse-fail: {text[:40]}",
                   "confirmed": False}
    worth = taste.compose(out["axes"]) if out["confirmed"] else 0.0
    return {"worth": worth, "why": taste.why(out["axes"]), "note": out["note"],
            "axes": out["axes"], "confirmed": out["confirmed"]}


if __name__ == "__main__":
    os.environ["SECONDATTN_OFFLINE"] = "1"
    import sys, os as _os
    sys.path.insert(0, _os.path.dirname(__file__))
    from perceive import build_graph, Detection

    dets = [Detection("person", (40, 430, 470, 1180)), Detection("cup", (500, 900, 620, 1040)),
            Detection("laptop", (980, 250, 1500, 1140)), Detection("desk", (0, 0, 1600, 1200), 0.9)]
    g = build_graph(dets, (1600, 1200))
    taste = ReportabilityTaste()
    print("default judge:", judge(None, g, taste))

    print("\nuser says: 'more people, less consequence'")
    taste.nudge("more people, less consequence")
    print("weights now:", {k: round(v, 2) for k, v in taste.weights.items()})
    print("judge:", judge(None, g, taste))

    print("\nuser says: 'I care about the robotics corner'")
    taste.nudge("I care about the robotics corner")
    print("about =", repr(taste.about))
