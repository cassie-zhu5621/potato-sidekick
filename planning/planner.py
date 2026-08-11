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

from planning.provider import DEFAULT_MODEL, call_json, model_name
# NOT this module's own `canonical`, which canonicalises a whole spec.
from planning.spec_utils import canonical as _canon_label

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

PREFER ONE RELATION PER ENTRY. An AND is not a description, it is a FILTER: every id you
add removes the frames where that one happens to be false, and they multiply. One person
doing one thing to one object is ONE relation.

  * NEVER add gazing-at(1) to a contact relation. Somebody with their hands on a thing is
    already looking at it, so the AND adds no information and subtracts every frame where
    the head is turned — which, for anyone reaching sideways or standing close to a large
    surface, is most of them. "drawing on a whiteboard" is hands-on(9) on the whiteboard.
    It is NOT hands-on(9) AND gazing-at(1).
  * The same goes for leaning(8), proximity(5) and approach(7) bolted onto a contact. If
    the hand is on the object, all three are nearly implied and none is reliable.
  * WANT BOTH? WRITE TWO ENTRIES. Separate entries are alternatives — either one fires.
    One entry with two ids needs both at once. Those are opposite behaviours, and the
    looser one is almost always what was meant.

Compose inside one entry only when the request names two things that must genuinely
COINCIDE and neither implies the other — "two people close together AND facing each
other", where either alone would be a different event.

Pick the MOST SPECIFIC relation for the context: 'comes to my desk' is approach(7) ON the desk, not gathering(10).
But 'comes INTO THE ROOM' is gathering(10) with no "on" — nobody is approaching a thing, the room simply has one more person in it."""

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

"on" MUST NAME SOMETHING A DETECTOR CAN PUT A BOX AROUND: a thing with edges and a
characteristic appearance. These are NOT that, however natural they sound:

  door, doorway, entrance, exit, room, corner, wall, floor, ceiling, background,
  area, space, side, hallway, outside

Most of them are OPENINGS or REGIONS -- an absence, or a part of the building. No
detector returns them, so an entry naming one in "on" is refused on every frame,
for as long as the plan lives, and nothing says why. Prefer a piece of furniture
that stands where the place is ("the desk", "the whiteboard") if the location
really is the point.

AND AN ARRIVAL IS NOT AN OBJECT EVENT AT ALL. "someone comes in", "people
arriving", "when somebody enters" are about WHO IS PRESENT changing, which is
gathering(10) and takes no "on" whatsoever. Reaching for a door to represent a
doorway is the trap: it converts a question about people into a question about a
thing that cannot be seen. approach(7) is for arriving AT SOMETHING DETECTABLE,
and it must NAME that thing: "someone coming to the desk" is approach(7) on the
desk. NEVER write approach(7) with no "on" -- a 7 without an object is checked
against every focus object at once and passes none of them, so the card is
suppressed on every frame and the moment is never reported. If you cannot name
what is being approached, the moment you mean is gathering(10).

THE REQUEST IS SPOKEN TO THE ROBOT, so the request's OWN watching verb says who
is watching -- the robot -- and is NOT part of the moment to plan for. "Look at",
"watch for", "keep an eye on", "tell me when", "notice when" are all framing.
Strip that frame before you read the moment.

Test it by deletion: if what remains still describes a scene, the verb was the frame.
  "Looking at | people holding a phone" leaves "people holding a phone" -- a complete
  moment -- so the leading "looking at" was framing. Plan hands-on(9) ALONE.
  Adding gazing-at(1) here is the mistake: it makes the PERSON look at the phone a
  requirement, which is not what was asked.
  "Tell me when | someone looks at the whiteboard" leaves "someone looks at the
  whiteboard" -- still a moment, and its "looks at" has a person as its subject and a
  thing as its object, so THAT one is content. gazing-at(1) on whiteboard is correct.
The difference is whose eyes: the robot's (frame, drop it) or a person's in the room
(content, keep it).

A wrongly kept frame verb is not a harmless extra. Every id you AND together is one
more thing that must hold in the SAME instant, so it makes the requested moment
rarer -- often much rarer. The person is not told their request was widened; they
just watch a robot that never reports, and conclude it is broken.

CONTEXT: "{context}"

THE CONTEXT IS USUALLY DICTATED AND AUTOMATICALLY TRANSCRIBED, so it may contain
mis-hearings: wrong but similar-sounding words, missing articles, broken grammar.
Read it for what the person plainly meant and plan for that. A word that makes no
sense in a room with a camera is almost always a mis-transcription of one that does
-- "a prison, drinking" is "a person drinking". Do NOT ask for clarification, do
NOT refuse, and do NOT return an empty plan because the wording is odd; commit to
the most plausible reading and note the assumption in "why". If a request is truly
unreadable, still return a valid plan for the most likely intent.

Write every natural-language output field, including labels, why, and missing, in English.
Return ONLY JSON, exactly this schema — use ONLY the fields shown, no additional fields,
no markdown fences. Every field must have its NATIVE type: "watch" is a JSON array of
objects, never a string containing JSON. Do not encode any part of the answer twice.
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
        # SAY WHAT IT ACTUALLY WAS. "missing or empty" was reported for a `watch`
        # that was neither -- it was a 2 kB STRING holding the entire spec, JSON
        # encoded twice. The message described the case the author had in mind
        # rather than the value in hand, and cost an hour aimed at the wrong
        # layer. unwrap_double_encoded() now repairs that shape before this runs,
        # so anything still arriving here is a different problem and should be
        # able to say so itself.
        if w is None:
            return ["watch: missing"]
        if isinstance(w, list):
            return ["watch: empty list"]
        preview = repr(w)[:80]
        return [f"watch: expected a list, got {type(w).__name__} {preview}"]
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
        # `on` IS PART OF WHAT MAKES A CARD DISTINCT.
        #
        # Without it, two cards watching the same relation on different objects
        # were called duplicates and the whole plan was thrown out. Observed
        # 2026-08-09 21:35 on "Look at people drawing on the white board. Look at
        # people touching my plants." -- which is the shape the study asks every
        # participant for, two things named in one breath:
        #
        #     watch[0]  all=[9]  on='whiteboard'
        #     watch[1]  all=[9]  on='plant'     -> "watch[1]: duplicate entry"
        #
        # Both retries produced the same (correct) shape, so both failed, and the
        # session went to S8 and back to idle with nothing to watch. Of the three
        # pairs the card offers, this was the only one that could never compile --
        # and it is the pair the sweep aims at best, since both objects sit in one
        # station.
        #
        # Canonical, because the same object comes back under different names
        # between attempts (`plant` then `potted plant` in that very audit). A
        # real duplicate -- same relation, same object -- is still caught.
        key = (frozenset(ops["all"]), frozenset(ops["any"]),
               frozenset(ops["not"]), tuple(ops["then"]),
               _canon_label(c.get("on") or ""))
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


def repair_bare_approach(spec: dict) -> dict:
    """approach(7) with no `on` -> gathering(10). Announced, never silent.

    A BARE APPROACH CAN NEVER FIRE. `_focus_ok` treats 7 as object-directed, so
    with no `on` it falls back to "approaching any FOCUS object" -- and a person
    walking into a room is approaching neither the plant nor another person. The
    card sits in the plan looking correct and is suppressed on every frame:

        [gate] comes to room suppressed (gaze/point not on a focus object)

    THE PROMPT ASKS FOR THIS ALREADY and has now failed at it twice. "Coming into
    the room" is `gathering(10)` -- who is present changed -- and the request
    "Looking at people coming to the room" still compiled to bare 7 on
    2026-08-09, because a few-shot line forty-eight lines earlier reads "'comes
    to my desk' is approach(7)" and the two sentences look alike. Both texts are
    fixed; this is the floor under them, because arrivals are one of the study's
    three staged events and a prompt that has slipped twice may slip again on a
    wording nobody has tried yet.

    Not a rejection. Refusing the plan would send the session to S8 and cost the
    participant their brief, which is a worse answer to a card that is merely
    mislabelled -- the moment the model wanted to watch for is exactly the moment
    gathering(10) describes.
    """
    for c in (spec or {}).get("watch", []) or []:
        if not isinstance(c, dict):
            continue
        ids = list(c.get("all") or [])
        if ids == [7] and not (c.get("on") or "").strip() and not c.get("any") \
                and not c.get("then"):
            c["all"] = [10]
            print(f"[planner] bare approach(7) {c.get('label', '')!r} -> "
                  f"gathering(10): a 7 with no object can never pass the focus "
                  f"gate, and an arrival is a change in who is present")
    return spec


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


def unwrap_double_encoded(spec):
    """Repair a spec whose whole body was returned as a STRING in one field.

    Observed on a real run (2026-08-05, request garbled by Whisper into
    "Expecting a prison, drinking, a holding a bottle"):

        {"watch": "{\\"watch\\": [{...}], \\"seen\\": [...], \\"detect\\": [...]}"}

    -- the entire answer JSON-encoded a second time and stuffed into `watch`.
    Both the first attempt and the retry did it, so it is a stable response to a
    confusing prompt, not a flake. The whole session went to S8 over a reply that
    contained a perfectly good plan.

    THE PROMPT WILL KEEP BEING CONFUSING. Requests arrive through Whisper, and
    "a person drinking" becoming "a prison, drinking" is an ordinary Tuesday. A
    pipeline that cannot survive one malformed envelope around correct content
    will keep ending sessions on transcription noise.

    Only unwraps when it is unambiguous: a string that parses to a dict which
    itself carries a `watch`. Anything else is returned untouched, so a genuinely
    empty plan still fails validation and still reaches S8.
    """
    if not isinstance(spec, dict):
        return spec
    inner = spec.get("watch")
    if not isinstance(inner, str):
        return spec
    try:
        parsed = json.loads(inner)
    except Exception:
        return spec
    if isinstance(parsed, dict) and isinstance(parsed.get("watch"), list):
        merged = dict(spec)
        merged.update(parsed)          # the inner copy is the real answer
        print("[planner] repaired a double-encoded reply "
              "(whole spec was a string inside 'watch')")
        return merged
    if isinstance(parsed, list):       # just the list, encoded
        merged = dict(spec)
        merged["watch"] = parsed
        print("[planner] repaired a double-encoded 'watch' list")
        return merged
    return spec


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
        # Repair before validating, never after: validate() reports what it was
        # handed, so an unrepaired envelope surfaces as a violation about the
        # CONTENTS ("watch: missing or empty") and sends the next person looking
        # at the model's judgement instead of at its packaging.
        spec = unwrap_double_encoded(spec)
        spec = repair_bare_approach(spec)
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
