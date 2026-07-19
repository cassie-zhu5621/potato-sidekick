"""
planner.py — the VLM PLANNER: context (+ optionally a frame) -> a watch-spec.

The VLM-first half of the new architecture (see relation_table.md). SINGLE SOURCE for the
planner prompt and watch-spec schema, used by BOTH the preliminary study and the future
live system — whatever the study tunes is exactly what ships.

TWO GRAMMARS (a study arm, not a design decision — see planner_study.py):
  restricted : watch = up to 3 entries; each entry = AND over 1-3 relation ids + a time
               window. Entries are alternatives (OR). Simple, easy to validate & execute.
  free       : each entry may combine  all (AND) / any (OR) / not (suppression) /
               then (ordered sequence)  — more expressive, bigger error surface.
Both schemas carry a "missing" field: if the context needs a relation the vocabulary
lacks, the model must SAY SO (the empirical coverage probe for the table).

Offline mode (SECONDATTN_OFFLINE=1): deterministic fake specs, keyless plumbing runs.
"""

from __future__ import annotations
import base64, hashlib, json, os
from typing import Optional

# Default = sonnet: the preliminary study's numbers are sonnet numbers, so the live system
# should match them. Try haiku later as a cost experiment (override via env var).
MODEL = os.environ.get("SECONDATTN_PLANNER_MODEL", "claude-sonnet-4-6")

# One line per row — the planner sees THIS summary of relation_table.md. Keep the wording
# in sync with the table; these strings are part of what the study studies.
VOCAB_VERSION = "v2.1"  # v2.1 (2026-06-14): disambiguate gathering(10)=group-size-change vs approach(7)=
                        #   single person toward a place; rebalanced examples so 'comes to my desk' picks 7.
                        #   (v3 reserved for the big survey-driven scenario update.)
                        # v2 (2026-06-10): +row 11, from the plan-time probe (turn-taking, 14 mentions)
VOCAB = {
    1:  "gazing-at — a person's head orientation is directed at an object",
    2:  "joint-attention — two or more people look at the same target",
    3:  "eye-contact — a person looks directly at the robot/camera",
    4:  "pointing/reaching — an extended arm is directed at an object or person",
    5:  "proxemic-zone — two people are within close interpersonal distance (Hall's intimate/personal zone)",
    6:  "F-formation — two people stand/sit facing each other in a conversational formation",
    7:  "approach/depart — a person is moving toward or away from an object, a person, or the robot",
    8:  "lean-in — a person leans their torso toward an object or work surface (engagement posture)",
    9:  "hands-on — a person's hand is on / manipulating an object",
    10: "gathering — the NUMBER of co-present people changes / a group forms (use this for group size, NOT for one person; for a single person coming toward a place or object, use approach(7))",
    11: "turn-taking — control of a shared artifact (keyboard, tool, object) passes from one person to another",
}

# Object-arity of each relation: does it NEED a target object, MAY it take one, or is it people-only?
#   required = meaningless without an object  ->  the entry MUST name it in "on"
#   optional = target may be an object OR a person/robot  ->  the VLM decides from context
#   none     = about people only              ->  no "on"
OBJECT_ARITY = {
    1: "required", 8: "required", 9: "required", 11: "required",   # gaze / lean / hands / turn-taking
    2: "optional", 4: "optional", 7: "optional",                    # joint-attn / pointing / approach
    3: "none", 5: "none", 6: "none", 10: "none",                    # eye-contact / prox / F-form / gathering
}

# Deliberately ABSTRACT (id-composition -> meaning), with varied shapes (single, pair,
# triple), so the examples neither leak answers to the study scenarios nor anchor the
# model to one composition size.
_FEWSHOT_SHAPES = """Examples of watchable moments (compositions and their meanings):
- [7] alone — a person approaches a specific place or object (e.g. comes to a desk)
- [5, 3] — close to the robot AND looking at it — seeking interaction
- [2, 4] — joint attention AND pointing — showing something to each other
- [10, 6, 1] — the group grows (someone joins) AND a face-to-face formation AND gazing at the same thing — an introduction
Compose freely: singles, pairs, or triples — whatever the context actually calls for.
Pick the MOST SPECIFIC relation for the context: 'comes to my desk' is approach(7), not gathering(10)."""

_SCHEMA_COMMON = ('"seen": [<ALL object nouns you can see in the frame right now, before any '
                  'filtering — for debugging what the planner perceived>], '
                  '"boxes": [{"label": "<object>", "tier": "focus"|"context", '
                  '"box": [x0, y0, x1, y1]}  — one entry per visible INSTANCE you can localize; '
                  'box as fractions 0-1 of the WHOLE image (if the image is a grid of several '
                  'camera views, box each instance in whatever view it appears)], '
                  '"detect": [<lowercase object nouns worth detecting for THIS context; the robot '
                  'detects ONLY these, so omit irrelevant furniture>], '
                  '"focus": [<the subset of detect that is the POINT of this delegate; ONLY focus '
                  'objects can trigger a report, the rest merely enrich. "person" is almost always here>], '
                  '"single_ok": [<ids that alone are worth recording, may be empty>], '
                  '"duration_s": <how long to keep this plan, 60-14400>, '
                  '"why": "<one sentence: why these, for this context>", '
                  '"missing": <null, or "<a relation this context needs that the vocabulary '
                  'does not contain>">')

SCHEMA_RESTRICTED = ('{"watch": [{"all": [<1-3 relation ids, ALL must hold>], '
                     '"on": "<optional: the focus object this entry is ABOUT, from detect; omit if it '
                     'is about people>", '
                     '"within_s": <0.5-10>, "label": "<short name>"}], ' + _SCHEMA_COMMON + "}")

SCHEMA_FREE = ('{"watch": [{'
               '"all": [<ids that must ALL hold>] (optional), '
               '"any": [<ids of which AT LEAST ONE must hold>] (optional), '
               '"not": [<ids that must NOT hold — suppression>] (optional), '
               '"then": [<ids that must occur IN THIS ORDER — a sequence>] (optional), '
               '"on": "<optional: the focus object this entry is ABOUT, from detect; omit if about people>", '
               '"within_s": <0.5-30>, "label": "<short name>"}], ' + _SCHEMA_COMMON + "}")


def build_prompt(context: str, grammar: str = "restricted") -> str:
    rows = "\n".join(f"  {i}. {d}" for i, d in VOCAB.items())
    if grammar == "free":
        compose = ("Each watch entry may combine fields: \"all\" (AND), \"any\" (OR), "
                   "\"not\" (must be absent), \"then\" (ordered sequence within the window). "
                   "Use the SIMPLEST expression that captures the moment; do not use an "
                   "operator you do not need. \"single_ok\" is a TOP-LEVEL field only — "
                   "never put it inside a watch entry; a single relation that alone is "
                   "worth recording belongs in the top-level \"single_ok\" list.")
        schema = SCHEMA_FREE
    else:
        compose = ("Each watch entry is an AND over its ids, true when all of them hold "
                   "within the time window.")
        schema = SCHEMA_RESTRICTED
    return f"""You are the attention planner of a small camera robot placed in a shared space.
It cannot see everything at once and must not report everything it sees. Given the CONTEXT,
decide what is worth WATCHING FOR, composed from this fixed vocabulary of detectable
relations:

{rows}

{_FEWSHOT_SHAPES}

{compose}
Entries in "watch" are ALTERNATIVES (OR): a moment is recorded when any one entry fires.
Choose AT MOST 3 entries, ranked most important first. Conjunctions are rarer and more
meaningful than single relations — prefer them when the context genuinely pairs signals,
but a single relation is the right answer when it alone carries the news.
If this context needs a relation the vocabulary CANNOT express, name it in "missing".

Also decide which OBJECTS matter, working in this order:
1. If a camera frame is provided, first ENUMERATE every object you can actually see in it and list
   them ALL in "seen" (this is the debug view of what you perceived). If no frame is given, infer
   the plausible objects from the context and put them in "seen".
2. Judge each object's RELEVANCE to the context. Put in "detect" only the lowercase nouns worth
   detecting here (people + the things the moment is about); the robot detects ONLY these, so leave
   out irrelevant furniture (e.g. a chair that is merely present) that would otherwise capture every
   relation. Put in "focus" the subset that is the POINT of the delegate: only focus objects can
   TRIGGER a report; the rest merely add context.
Also fill "boxes": box the FOCUS objects and the main CONTEXT objects only — you do NOT need to box
every tiny or background item (keep it to roughly the most relevant ~12 per view). For each, give
its label, its tier ("focus" or "context"), and a bounding box [x0,y0,x1,y1] as fractions 0-1 of the
WHOLE image. If the image is a grid/contact-sheet of several camera views, box each instance in
whichever view it appears (coordinates still over the whole image). Do NOT box ignored objects. Keep
coordinates short (2 decimals).

3. When a watch entry is about a SPECIFIC object, name it in that entry's "on" field (a value from
   detect) — e.g. hands-on THE desk, gaze-at THE monitor. Object requirement per relation:
   - ids 1, 8, 9, 11 REQUIRE "on" (gaze / lean / hands-on / turn-taking are meaningless without an object);
   - ids 2, 4, 7 MAY take "on" (joint-attention / pointing / approach — their target can be an object
     OR a person; decide from the context);
   - ids 3, 5, 6, 10 take NO "on" (eye-contact / proxemic / F-formation / gathering are about people).
Prefer common object names.

CONTEXT: "{context}"

Return ONLY JSON, exactly this schema — use ONLY the fields shown, no additional fields,
no markdown fences:
{schema}"""


# --------------------------------------------------------------------------- #
# validation — the VLM's output must be executable; reject, don't repair silently
# --------------------------------------------------------------------------- #
_RESTRICTED_FIELDS = {"all", "on", "within_s", "label"}
_FREE_FIELDS = {"all", "any", "not", "then", "on", "within_s", "label"}


def validate(spec: dict, grammar: str = "restricted") -> list:
    """Returns a list of violation strings (empty = valid)."""
    v = []
    w = spec.get("watch")
    if not isinstance(w, list) or not w:
        return ["watch: missing or empty"]
    if len(w) > 3:
        v.append(f"watch: {len(w)} entries (>3)")
    allowed = _FREE_FIELDS if grammar == "free" else _RESTRICTED_FIELDS
    ws_hi = 30 if grammar == "free" else 10
    seen = set()
    for i, c in enumerate(w):
        if not isinstance(c, dict):
            v.append(f"watch[{i}]: not an object"); continue
        extra = set(c) - allowed
        if extra:
            v.append(f"watch[{i}]: fields {sorted(extra)} not allowed in {grammar} grammar")
        ops = {f: c.get(f, []) for f in ("all", "any", "not", "then")}
        if grammar == "restricted":
            if not isinstance(ops["all"], list) or not (1 <= len(ops["all"]) <= 3):
                v.append(f"watch[{i}]: 'all' must list 1-3 ids"); continue
        else:
            if not any(ops[f] for f in ("all", "any", "then")):
                v.append(f"watch[{i}]: needs at least one of all/any/then"); continue
        for f, ids in ops.items():
            if not isinstance(ids, list):
                v.append(f"watch[{i}].{f}: not a list"); continue
            if len(ids) > 3:
                v.append(f"watch[{i}].{f}: {len(ids)} ids (>3)")
            if any(r not in VOCAB for r in ids):
                v.append(f"watch[{i}].{f}: unknown id in {ids}")
            if len(set(ids)) != len(ids):
                v.append(f"watch[{i}].{f}: duplicate ids {ids}")
        key = (frozenset(ops["all"]), frozenset(ops["any"]),
               frozenset(ops["not"]), tuple(ops["then"]))
        if key in seen:
            v.append(f"watch[{i}]: duplicate entry")
        seen.add(key)
        # object-arity: relations 1/8/9/11 need an object; 3/5/6/10 forbid one
        ent_ids = ops["all"] + ops["any"] + ops["then"]
        has_on = bool(c.get("on"))
        arities = {OBJECT_ARITY.get(r) for r in ent_ids if r in OBJECT_ARITY}
        if "required" in arities and not has_on:
            v.append(f"watch[{i}]: relation {sorted(r for r in ent_ids if OBJECT_ARITY.get(r)=='required')}"
                     " needs a target object but 'on' is missing")
        if arities and arities <= {"none"} and has_on:
            v.append(f"watch[{i}]: 'on'={c.get('on')!r} set but relations are people-only")
        ws = c.get("within_s", 2.0)
        if not (isinstance(ws, (int, float)) and 0.5 <= ws <= ws_hi):
            v.append(f"watch[{i}]: within_s {ws} out of [0.5,{ws_hi}]")
    for r in spec.get("single_ok", []) or []:
        if r not in VOCAB:
            v.append(f"single_ok: unknown id {r}")
    for fld in ("seen", "detect", "focus"):            # relevance layer (lenient: optional)
        val = spec.get(fld)
        if val is not None and (not isinstance(val, list)
                                or not all(isinstance(x, str) for x in val)):
            v.append(f"{fld}: must be a list of strings")
    d = spec.get("duration_s", 600)
    if not (isinstance(d, (int, float)) and 60 <= d <= 14400):
        v.append(f"duration_s {d} out of [60,14400]")
    if not str(spec.get("why", "")).strip():
        v.append("why: empty")
    return v


def canonical(spec: dict):
    """Entry content only (ignores within_s/labels/why) — for consistency metrics."""
    out = set()
    for c in spec.get("watch", []):
        if isinstance(c, dict):
            out.add((frozenset(c.get("all", []) or []), frozenset(c.get("any", []) or []),
                     frozenset(c.get("not", []) or []), tuple(c.get("then", []) or [])))
    return frozenset(out)


def ops_used(spec: dict) -> set:
    """Which operators beyond plain AND does a spec use? (study metric, free arm)."""
    used = set()
    for c in spec.get("watch", []):
        if isinstance(c, dict):
            for f in ("any", "not", "then"):
                if c.get(f):
                    used.add(f)
    return used


def _offline(context: str, grammar: str) -> dict:
    h = hashlib.md5((grammar + context).encode()).hexdigest()
    a, b, c = (int(h[i:i+2], 16) % len(VOCAB) + 1 for i in (0, 2, 4))
    if a == b:
        b = b % 10 + 1
    entry = {"all": sorted({a, b}), "within_s": 2.0, "label": "[offline] combo"}
    if grammar == "free" and int(h[6], 16) % 2:
        entry = {"then": sorted({a, b}), "not": [c] if c not in (a, b) else [],
                 "within_s": 5.0, "label": "[offline] sequence"}
    return {"watch": [entry], "single_ok": [c] if c not in (a, b) else [],
            "duration_s": 600, "why": f"[offline] {context[:40]}", "missing": None,
            "seen": ["person", "laptop", "cup", "dining table", "chair", "potted plant"],
            "boxes": [{"label": "person", "tier": "focus", "box": [0.4, 0.3, 0.6, 0.9]}],
            "detect": ["person", "laptop", "cup", "dining table"], "focus": ["person"]}


def plan(context: str, jpeg: Optional[bytes] = None, model: str = MODEL,
         temperature: float = 0.0, grammar: str = "restricted") -> dict:
    """-> {"spec":..., "violations":[...], "raw": text, "grammar": grammar}."""
    if os.environ.get("SECONDATTN_OFFLINE") == "1":
        spec = _offline(context, grammar)
        return {"spec": spec, "violations": validate(spec, grammar),
                "raw": json.dumps(spec), "grammar": grammar}
    import anthropic
    client = anthropic.Anthropic()
    content = []
    if jpeg is not None:
        content.append({"type": "image", "source": {"type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(jpeg).decode()}})
    content.append({"type": "text", "text": build_prompt(context, grammar)})
    msg = client.messages.create(model=model, max_tokens=8000, temperature=temperature,
                                 messages=[{"role": "user", "content": content}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    try:
        s, e = text.index("{"), text.rindex("}") + 1
        spec = json.loads(text[s:e])
        return {"spec": spec, "violations": validate(spec, grammar),
                "raw": text, "grammar": grammar}
    except Exception as ex:
        return {"spec": None, "violations": [f"parse-fail: {ex}"], "raw": text,
                "grammar": grammar}


if __name__ == "__main__":
    os.environ.setdefault("SECONDATTN_OFFLINE", "1")
    for g in ("restricted", "free"):
        print(f"--- {g}")
        print(json.dumps(plan("two of us are assembling a robot arm this afternoon",
                              grammar=g), indent=2))
