"""
planner.py — the Gemini VLM PLANNER: context + independent spatial views -> watch-spec.

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
import hashlib, json, os
from pathlib import Path
from typing import Optional, Sequence

from planning.gemini_provider import DEFAULT_MODEL, call_json, model_name

MODEL = model_name()

_CATALOG_PATH = Path(__file__).resolve().parents[1] / "schemas" / "relation_catalog.v2.json"
_CATALOG = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
VOCAB_VERSION = _CATALOG["version"]
VOCAB = {row["id"]: f'{row["key"]} — {row["description"]}'
         for row in _CATALOG["relations"]}

# Object-arity of each relation: does it NEED a target object, MAY it take one, or is it people-only?
#   required = meaningless without an object  ->  the entry MUST name it in "on"
#   optional = target may be an object OR a person/robot  ->  the VLM decides from context
#   none     = about people only              ->  no "on"
OBJECT_ARITY = {row["id"]: row["object_arity"] for row in _CATALOG["relations"]}

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
                  '"view_index": <0-based image index>, "box": [x0, y0, x1, y1]} — one entry '
                  'per visible INSTANCE; box coordinates are fractions 0-1 of THAT view], '
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


def output_schema(grammar: str = "restricted") -> dict:
    entry_properties = {
        "all": {"type": "array", "items": {"type": "integer"}},
        "any": {"type": "array", "items": {"type": "integer"}},
        "not": {"type": "array", "items": {"type": "integer"}},
        "then": {"type": "array", "items": {"type": "integer"}},
        "on": {"type": ["string", "null"]},
        "within_s": {"type": "number"},
        "label": {"type": "string"},
    }
    entry = {
        "type": "object",
        "properties": entry_properties,
        "required": (["all", "within_s", "label"] if grammar == "restricted"
                     else ["within_s", "label"]),
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "watch": {"type": "array", "items": entry},
            "seen": {"type": "array", "items": {"type": "string"}},
            "boxes": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "tier": {"type": "string", "enum": ["focus", "context"]},
                    "view_index": {"type": "integer", "minimum": 0},
                    "box": {"type": "array", "items": {
                        "type": "number", "minimum": 0, "maximum": 1,
                    }, "minItems": 4, "maxItems": 4},
                },
                "required": ["label", "tier", "view_index", "box"],
                "additionalProperties": False,
            }},
            "detect": {"type": "array", "items": {"type": "string"}},
            "focus": {"type": "array", "items": {"type": "string"}},
            "single_ok": {"type": "array", "items": {"type": "integer"}},
            "duration_s": {"type": "number"},
            "why": {"type": "string"},
            "missing": {"type": ["string", "null"]},
        },
        "required": ["watch", "seen", "boxes", "detect", "focus", "single_ok",
                     "duration_s", "why", "missing"],
        "additionalProperties": False,
    }


def build_prompt(context: str, grammar: str = "restricted") -> str:
    by_id = {row["id"]: row for row in _CATALOG["relations"]}
    rows = "\n".join(
        f"  {i}. {d}; evidence: {' -> '.join(by_id[i]['evidence_chain'])}"
        for i, d in VOCAB.items()
    )
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
Never repeat an exact one-relation watch entry in "single_ok"; that would create two
identical candidates and two unnecessary Judge calls for the same moment.
Choose AT MOST 3 entries, ranked most important first. Conjunctions are rarer and more
meaningful than single relations — prefer them when the context genuinely pairs signals,
but a single relation is the right answer when it alone carries the news.
If this context needs a relation the vocabulary CANNOT express, name it in "missing".

Also decide which OBJECTS matter, working in this order:
1. If camera views are provided, they are INDEPENDENT spatial views labelled IMAGE 0..N-1,
   not a temporal sequence and not a contact sheet. First ENUMERATE every object actually visible
   across them and list them ALL in "seen". If no frame is given, infer
   the plausible objects from the context and put them in "seen".
2. Judge each object's RELEVANCE to the context. Put in "detect" only the lowercase nouns worth
   detecting here (people + the things the moment is about); the robot detects ONLY these, so leave
   out irrelevant furniture (e.g. a chair that is merely present) that would otherwise capture every
   relation. Put in "focus" the subset that is the POINT of the delegate: only focus objects can
   TRIGGER a report; the rest merely add context.
Also fill "boxes": box the FOCUS objects and the main CONTEXT objects only — you do NOT need to box
every tiny or background item (keep it to roughly the most relevant ~12 per view). For each, give
its label, tier, `view_index`, and a bounding box [x0,y0,x1,y1] as fractions 0-1 of THAT
individual view. Do NOT merge coordinates across views. Do NOT box ignored objects. Keep coordinates
short (2 decimals).

3. When a watch entry is about a SPECIFIC object, name it in that entry's "on" field (a value from
   detect) — e.g. hands-on THE desk, gaze-at THE monitor. Object requirement per relation:
   - ids 1, 8, 9, 11 REQUIRE "on" (gaze / lean / hands-on / turn-taking are meaningless without an object);
   - ids 2, 4, 7 MAY take "on" (joint-attention / pointing / approach — their target can be an object
     OR a person; decide from the context);
   - ids 3, 5, 6, 10 take NO "on" (eye-contact / proxemic / F-formation / gathering are about people).
Prefer common object names.

CONTEXT: "{context}"

Write every natural-language output field, including labels, why, and missing, in English.
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
    for i, box in enumerate(spec.get("boxes", []) or []):
        coords = box.get("box") if isinstance(box, dict) else None
        view_index = box.get("view_index") if isinstance(box, dict) else None
        if not (isinstance(view_index, int) and view_index >= 0):
            v.append(f"boxes[{i}].view_index: must be a non-negative integer")
        if not (isinstance(coords, list) and len(coords) == 4
                and all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in coords)):
            v.append(f"boxes[{i}].box: must be four numbers in [0,1]")
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
    ids = entry.get("all", []) + entry.get("then", [])
    if any(OBJECT_ARITY.get(r) == "required" for r in ids):
        entry["on"] = "laptop"
    return {"watch": [entry], "single_ok": [],
            "duration_s": 600, "why": f"[offline] {context[:40]}", "missing": None,
            "seen": ["person", "laptop", "cup", "dining table", "chair", "potted plant"],
            "boxes": [{"label": "person", "tier": "focus", "view_index": 0,
                       "box": [0.4, 0.3, 0.6, 0.9]}],
            "detect": ["person", "laptop", "cup", "dining table"], "focus": ["person"]}


def plan(context: str, jpeg: Optional[bytes | Sequence[bytes]] = None, model: str = MODEL,
         temperature: float = 0.0, grammar: str = "restricted") -> dict:
    """-> {"spec":..., "violations":[...], "raw": text, "grammar": grammar}."""
    if os.environ.get("SECONDATTN_OFFLINE") == "1":
        spec = _offline(context, grammar)
        return {"spec": spec, "violations": validate(spec, grammar),
                "raw": json.dumps(spec), "grammar": grammar}
    images = ([jpeg] if isinstance(jpeg, (bytes, bytearray))
              else list(jpeg or []))
    text = ""
    try:
        spec, text = call_json(
            build_prompt(context, grammar), output_schema(grammar), images=images,
            labels=[f"spatial_view_{i}" for i in range(len(images))], model=model,
            max_output_tokens=4096,
        )
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
