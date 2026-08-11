"""
judge.py — the Gemini VLM JUDGMENT BRAIN (step ④ of the flow).

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
import json, os, re, hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from planning.event_frames import EVENT_OFFSETS_S
from planning.provider import call_json, model_name
from planning.spec_utils import viewpoint_free


def _frame_labels(n: int, event_window: bool = False) -> list[str]:
    """Per-image labels. Derived from the same tuple the frames were selected by,
    so the two cannot drift the way the prompt's typed-out version did.

    `event_window` HAS TO BE ASKED FOR. Two different sets of images reach this
    module -- the judge's five historical frames, and the storyboard's strip of
    panels -- and only the first has anything to do with EVENT_OFFSETS_S. Keyed
    on `n == 5` instead, a five-panel strip would be handed to the narrator
    labelled "t-4.0s .. onset", i.e. told that a story spanning up to 45 s of
    room is the four seconds BEFORE the moment it is describing.
    """
    if event_window and n == len(EVENT_OFFSETS_S):
        return ["t0_onset" if o == 0 else f"t{o:+.1f}s" for o in EVENT_OFFSETS_S]
    return [f"panel_{i}" for i in range(n)]

MODEL = model_name(os.environ.get("NOTICEBOT_GEMINI_JUDGE_MODEL"))

_RELATION_CLAIMS = {
    1: "gazing at", 2: "joint attention", 3: "eye contact", 4: "pointing at",
    5: "close proximity", 6: "F-formation", 7: "approaching or departing",
    8: "leaning toward", 9: "hands on or manipulating", 10: "gathering",
    11: "turn-taking with",
}


def confirmation_claim(entry: dict) -> str:
    """Turn a watch entry into an unambiguous claim for the VLM Judge.

    Appending ``on cup`` to a planner label such as ``holding cup`` produced
    ``holding cup on cup``, which Gemini reasonably interpreted as cup stacking.
    Use the authored label when it already names the target; otherwise say
    ``involving`` rather than composing an accidental spatial relation.
    """
    label = str(entry.get("label") or "").strip()
    if label.startswith("single:"):
        try:
            label = _RELATION_CLAIMS.get(int(label.split(":", 1)[1]), label)
        except ValueError:
            pass
    if not label:
        ids = list(entry.get("all") or entry.get("any") or entry.get("then") or [])
        label = _RELATION_CLAIMS.get(ids[0], "requested event") if ids else "requested event"
    on = entry.get("on")
    if on and str(on).lower() not in label.lower():
        label += f" involving {on}"
    return label


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


def _prompt(rel: str, taste: ReportabilityTaste, confirm: str = "", story: str = "",
            panels: int = 0, opening: str = "") -> str:
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
        # PANEL COUNT, NOT A GUESS FROM THE TEXT. This read `"→" in story` while
        # the storyboard joins with ASCII " -> ", so it was False for all 55
        # multi-panel strips in the record and every one of them was described
        # with the single-frame prompt below -- which states, in those words,
        # that the image is ONE INSTANT.
        #
        # Handed a montage of the same person at three moments and told it is a
        # single instant, the only coherent reading is three people. Reported
        # 2026-08-08 as "one person doing two things in a row is described as
        # two people"; the model was doing exactly what it had been told.
        multi = panels > 1 if panels else ("→" in story or " -> " in story)
        if multi:
            lines.append(f"\nThese are {panels or 'several'} SEPARATE FRAMES from one fixed camera,"
                         f" oldest first, a few seconds apart. Same place throughout: a person in"
                         f" more than one frame is THE SAME PERSON later, not another person. Count"
                         f" people WITHIN a frame, never across them. The first frame is the moment"
                         f" that was noticed; the rest are WHAT HAPPENED NEXT.")
            if opening:
                lines.append(f'What was noticed, already established: "{opening}" Do not restate'
                             f" it. CONTINUE from there and say what followed, in one sentence.")
            lines.append(f"The grounded sequence the system DETECTED, in order: {story}. Recount"
                         f" what happened across the frames as ONE continuous event. Describe ONLY"
                         f' what the frames actually show — do NOT invent a "then..." step that is'
                         f" not visible in any of them.")
        else:
            lines.append(f"\nThe image is a SINGLE frame — one instant, NOT a sequence. Detected here:"
                         f" {story}. Describe ONLY what is visible in this one frame. Do NOT narrate any"
                         f' before/after, arrival, or "then moved..." — there is no evidence for it.')
    if confirm:
        lines.append("The supplied images are ordered temporal evidence from the same view.")
    js = '{"axes": {"people":0-1,"relevance":0-1,"consequence":0-1,"continuity":0-1}, '
    js += ('"confirmed": true|false, "note": "<one English field note, <=16 words>", '
           '"feedback": "<one short English sentence for the user>"}')
    lines.append("All natural-language response fields must be written in English.")
    lines.append(f"\nReturn ONLY JSON: {js}")
    return "\n".join(lines)


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "axes": {
            "type": "object",
            "properties": {a: {"type": "number"} for a in AXES},
            "required": list(AXES),
            "additionalProperties": False,
        },
        "confirmed": {"type": "boolean"},
        "note": {"type": "string"},
        "feedback": {"type": "string"},
    },
    "required": ["axes", "confirmed", "note", "feedback"],
    "additionalProperties": False,
}


# ONE JOB: is this card worth reporting, and what would you say about it.
#
# WHAT WAS REMOVED, AND WHY. The previous schema also asked for four 0-1 axes
# (people / relevance / consequence / continuity) scored on "the shared moment",
# plus a note AND a feedback sentence. Downstream reads `selected_index`,
# `confirmed`, `feedback` and the per-card reason; `axes` and `why` were
# referenced ZERO times -- the model computed them, the parser unpacked them,
# and they were dropped.
#
# `continuity` was worse than unused: "a follow-up on something noticed before"
# is unanswerable from five images and a card list, because nothing about what
# was noticed before is in the request. A model asked to score information it
# has not been given cannot resolve it, only deliberate about it.
#
# The remaining split -- judge each card independently, THEN rank them by three
# criteria to pick exactly one -- is two different tasks in one call. Ranking is
# now mechanical (the most specific passing card, ties to the given order), so
# the model only judges.
# ONE QUESTION: do these five frames show something worth telling the person
# about, and what would you say.
#
# THE SUBJECT IS THE MOMENT, NOT THE CARD. Earlier versions asked the model to
# VERIFY each of CV's cards against the images -- "the precision half of the
# gate->VLM split" -- which meant N independent geometric adjudications plus a
# ranking, plus four 0-1 axes on the side. That is what a 40-278 s judge was
# spending its time on, against a planner at 5-17 s on the same model and the
# same five 1280x720 frames.
#
# The verification was defensible and it is being given up on purpose. What it
# bought, measured over 42 recorded calls: ONE case where CV claimed "gazing at
# AND drinking water" and the model passed only "hands on bottle", because the
# person was holding the bottle without drinking.
#
# It is affordable to lose because THE CARD LABEL NEVER REACHES THE PARTICIPANT.
# They get the spoken sentence and the robot's motion; the label goes to
# attention_log and the researcher's screen. A `describe` written from the
# images cannot inherit a wrong label, so a bad card is now a logging artefact
# rather than the robot announcing something that did not happen.
#
# The labels are still sent -- as a one-line hint, not as claims to adjudicate.
# Five frames of a room with no hint of what to look at is an invitation to
# describe the wrong corner.
GROUP_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "pass": {"type": "boolean"},
        "describe": {"type": "string"},
    },
    "required": ["pass", "describe"],
    "additionalProperties": False,
}


def pick_winner(entries) -> int:
    """Which of several coincident cards labels the story. -1 if there are none.

    NOT A JUDGEMENT, so it does not need the model and is not asked of it: most
    specific first (more relation ids = says more), ties broken by CV's own
    order, which order_coincident_candidates already fixed. Nothing it decides
    reaches the participant -- the card label is internal, and the sentence they
    would read is written later by the storyboard from the images.

    Lifted out of judge_candidate_group so the DEADLINE PATH can use the same
    rule. When the judge is too slow to wait for (ST.JUDGE_DEADLINE_S), the
    finding still has to name a card, and naming it by a second rule written
    beside the first is how the two would come to disagree about which event a
    session recorded.
    """
    if not entries:
        return -1

    def _specificity(i):
        c = entries[i]
        return len(set(c.get("all") or []) | set(c.get("any") or [])
                   | set(c.get("then") or []))

    return max(range(len(entries)), key=lambda i: (_specificity(i), -i))


ANCHOR_NOTE = """
WHICH object and WHICH person were settled before you were called, and the
request above has had its left/right/near/far words REMOVED because of it. The
robot pans its head, so those words name places in the ROOM; these frames do not
carry the head's angle, and geometry has already matched the request to one
specific object in view. YOU ARE CHECKING THE ACTION, NOT THE LOCATION. Never
fail a moment because something is on what looks like the wrong side of the
picture."""


def _when() -> str:
    """The frame times, read off EVENT_OFFSETS_S rather than retyped.

    They were retyped once and went stale: the prompt still announced
    "t-1.0s, t-0.5s, onset, t+0.5s, t+1.0s" for months after the window moved to
    -4.0 .. 0.0, so the judge was told two of its frames were the AFTERMATH of a
    moment when every one of them predates it. Asked what happened next, it had
    to invent it."""
    def _tag(offset):
        return "onset" if offset == 0 else f"t{offset:+.1f}s"
    return ("Same camera, five moments: " + ", ".join(_tag(o) for o in EVENT_OFFSETS_S)
            + ".\nAll of them are at or BEFORE the moment in question -- none show what "
              "happened after.")


def _group_prompt(candidates: list[dict], taste: ReportabilityTaste,
                  request: str = "") -> str:
    # Viewpoint-relative words are stripped from EVERYTHING the judge reads. See
    # spec_utils.viewpoint_free: the planner already resolved them against every
    # view it swept, the CV gate already enforced the box that came out, and this
    # stage has five frames and no pan angle -- so the only thing it can do with
    # the word is re-resolve it against the picture and get a different answer.
    noticed = ", ".join(viewpoint_free(c.get("label") or "") for c in candidates
                        if c.get("label"))
    about = (f'\nThis person generally cares about: "{taste.about.strip()}".'
             if taste.about.strip() else "")
    req = ""
    if (request or "").strip():
        asked = viewpoint_free(request.strip())
        req = (f'\nTHEY ASKED FOR: "{asked}"\n'
               f'Dictated and auto-transcribed, so read it for what was meant.')
        if asked != request.strip():
            req += ANCHOR_NOTE
    # WHICH PART OF THE REQUEST THIS MOMENT IS ABOUT.
    #
    # A participant names two things in one breath -- "looking at new people to
    # come, looking at people touches my plant" -- and the planner correctly
    # writes a card for each. They then fire at DIFFERENT moments, and only the
    # cards that fired are sent here. The request that comes with them is the
    # whole sentence.
    #
    # Observed 2026-08-09 21:14. `person_arrives` fired alone; the judge was
    # asked whether the frames show "what they asked for", read the whole
    # sentence, and answered about the other half:
    #
    #     "A person is standing in the room but they do not touch the plant."
    #
    # A true arrival would have been rejected for the plant's sake. The fix is to
    # say which part is being judged -- and, in the same breath, to keep the older
    # rule that the CV label is not a claim to rubber-stamp. Those are two
    # different questions and the last line separates them.
    scope = (f"THIS MOMENT WAS FLAGGED FOR ONE PART OF THAT REQUEST: "
             f"{noticed or 'something'}\n"
             "A request often names several things, and each is watched "
             "separately. Judge ONLY\nthe part named above. The others have their "
             "own moments and are not your concern\nhere -- failing this one "
             "because a different part did not happen is the error to\navoid. "
             "WHICH part is settled; WHETHER it happened is what you are for."
             if req else
             f"Motion detection thinks it saw: {noticed or 'something'}\n"
             "That is a hint about where to look, not a claim to check.")
    target = "THAT PART" if req else "WHAT THEY ASKED FOR"
    return f"""Decide whether these five frames are worth interrupting someone for.

{_when()}
{req}{about}

{scope}

`pass`: TRUE IF THE FRAMES SHOW {target}. That is the whole test.
A standing state counts -- "people reading books" is satisfied by someone
reading, it does not have to start or change while you watch.
Only fall back on "is this worth mentioning at all" when they asked for
nothing in particular. False if what they asked for is not there, or if the
frames show it only ambiguously -- cheap 2D geometry misjudges depth, so do
not pass a moment just because the hint says so.

`describe`: ONE short plain sentence about what is happening. ALWAYS WRITE IT,
including when pass is false -- then it is the reason, and it is the only trace
a rejection leaves. Written in English whatever language the request arrived in:
the robot's screen is English, and a reply in another language reads as a
different system answering.

Return only the requested JSON."""


def judge_candidate_group(jpeg: Optional[bytes | Sequence[bytes]], entries: Sequence[dict],
                          taste: ReportabilityTaste, model: str = MODEL,
                          request: str = "") -> dict:
    """Evaluate coincident cards in one Gemini call and select at most one winner."""
    images = ([jpeg] if isinstance(jpeg, (bytes, bytearray)) else list(jpeg or []))
    candidate_specs = []
    for index, entry in enumerate(entries):
        candidate_specs.append({
            "index": index,
            "label": str(entry.get("label") or f"candidate {index}"),
            "claim": confirmation_claim(entry),
            "all": list(entry.get("all") or []),
            "any": list(entry.get("any") or []),
            "not": list(entry.get("not") or []),
            "then": list(entry.get("then") or []),
            "on": entry.get("on"),
        })

    if not candidate_specs:
        return {"worth": 0.0, "why": "", "note": "No candidate cards.",
                "feedback": "", "axes": {a: 0.0 for a in AXES},
                "confirmed": False, "selected_index": -1,
                "candidate_results": []}

    if os.environ.get("SECONDATTN_OFFLINE") == "1" or not images:
        axes = _offline(images[0] if images else None,
                        candidate_specs[0]["claim"], taste)["axes"]
        rows = [{"index": item["index"], "confirmed": True,
                 "reason": "offline deterministic confirmation"}
                for item in candidate_specs]
        selected = 0
        note = f"[offline] {candidate_specs[0]['claim'][:64]}"
        feedback = f"I noticed: {candidate_specs[0]['claim'][:48]}"
    else:
        try:
            raw, _ = call_json(
                _group_prompt(candidate_specs, taste, request), GROUP_JUDGE_SCHEMA,
                images=images, labels=_frame_labels(len(images), event_window=True),
                model=model, max_output_tokens=768,
            )
            ok = bool(raw.get("pass", False))
            note = str(raw.get("describe", ""))[:160]
            # ONE verdict covers the group: the model judged the MOMENT, so every
            # card that pointed at it inherits that verdict. `candidate_results`
            # survives for the web UI, which shows a row per card.
            rows = [{"index": i, "confirmed": ok,
                     "reason": note or ("worth reporting" if ok else "not worth reporting")}
                    for i in range(len(candidate_specs))]
            # WHICH CARD LABELS THE STORY is now purely bookkeeping -- the model
            # is not asked, because nothing it could say would reach the person.
            # Most specific first (more relation ids = says more), ties to CV's
            # own order, which order_coincident_candidates already fixed.
            selected = pick_winner(candidate_specs) if ok else -1
            feedback = note
        except Exception as exc:
            rows = [{"index": item["index"], "confirmed": False,
                     "reason": f"judge error: {str(exc)[:80]}"}
                    for item in candidate_specs]
            selected = -1
            note = f"judge error: {str(exc)[:80]}"
            feedback = ""

    confirmed = selected >= 0
    # `axes`/`worth`/`why` are kept in the RETURN so callers do not change; the
    # model is no longer asked for them and nothing downstream reads them.
    axes = {a: 0.0 for a in AXES}
    return {"worth": 1.0 if confirmed else 0.0, "why": "", "note": note,
            "feedback": feedback, "axes": axes, "confirmed": confirmed,
            "selected_index": selected, "candidate_results": rows}


def judge(jpeg: Optional[bytes | Sequence[bytes]], graph, taste: ReportabilityTaste,
          delta_added=None, model: str = MODEL, confirm: str = "", story: str = "",
          panels: int = 0, opening: str = "") -> dict:
    """Judge one gated moment. Returns {worth, why, note, axes, confirmed}.
    `confirm`: optional relation claim (e.g. "a person gazing at the cup") — the VLM first
    VERIFIES it against the image (the precision half of the gate→VLM split for the
    designed-relation branch); unconfirmed moments come back with worth=0."""
    rel = relations_text(graph, delta_added) if graph is not None else ""
    images = ([jpeg] if isinstance(jpeg, (bytes, bytearray)) else list(jpeg or []))
    if os.environ.get("SECONDATTN_OFFLINE") == "1" or not images:
        seed_image = images[0] if images else None
        out = _offline(seed_image, story or rel, taste)
        if story:
            out["note"] = f"[offline] {story[:64]}"
        out["feedback"] = f"I noticed: {(confirm or story or rel or 'an event')[:48]}"
        out["confirmed"] = True
    else:
        try:
            labels = _frame_labels(len(images))
            raw, text = call_json(
                _prompt(rel, taste, confirm, story, panels, opening), JUDGE_SCHEMA,
                images=images, labels=labels, model=model, max_output_tokens=256,
            )
            out = {"axes": {a: float(raw.get("axes", {}).get(a, 0.0)) for a in AXES},
                   "note": str(raw.get("note", ""))[:120],
                   "feedback": str(raw.get("feedback", ""))[:160],
                   "confirmed": bool(raw.get("confirmed", not confirm))}
        except Exception as exc:
            out = {"axes": {a: 0.0 for a in AXES}, "note": f"Gemini error: {str(exc)[:60]}",
                   "feedback": "",
                   "confirmed": False}
    worth = taste.compose(out["axes"]) if out["confirmed"] else 0.0
    return {"worth": worth, "why": taste.why(out["axes"]), "note": out["note"],
            "feedback": out.get("feedback", ""),
            "axes": out["axes"], "confirmed": out["confirmed"]}


if __name__ == "__main__":
    os.environ["SECONDATTN_OFFLINE"] = "1"
    from perception.perceive import build_graph, Detection

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
