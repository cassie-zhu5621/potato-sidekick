# Relation Table v2 — a VLM-composable vocabulary of socially meaningful relations
*(all computable from MediaPipe + an object detector; paper-bound file, keep in English)*

**v2 (2026-06-10):** added row 11 (turn-taking / control handoff) — the single strongest
finding of the v1 preliminary study's plan-time probe (14 mentions; 100% of pair-prog
runs). Notably, it corresponds to a taxonomy channel (interaction regulation — Ekman &
Friesen's "regulators") that v1 had left unsampled: the probe found a *channel* gap, not
a random omission. One row changed ⇒ the v1→v2 study comparison isolates its effect.

Architecture (2026-06-10, after supervisor discussion): **VLM-first**.
Context (a typed prompt for now; other robot-interaction modalities later) + the current
frame → the VLM understands the situation → selects, from the FIXED vocabulary below,
which relation or which CONJUNCTION of relations (e.g. 3+5, 2+7) is worth recording in
THIS context → CV then watches for a period, evaluating **T/F for all rows 1–10 every
frame** (the per-frame truth vector is itself logged data) and recording the moments when
the chosen conjunction is satisfied.

Design properties:
- The VLM does NOT run per frame: it re-plans only on context change, user input, or a
  periodic recheck. The cheap-runtime cost structure is preserved.
- The vocabulary is fixed and literature-grounded — the contribution is a
  **theory-grounded perceptual vocabulary** for VLM program synthesis (related work:
  ViperGPT, VisProg, Code-as-Policies, where the vocabulary is API calls instead).
- Only three perceptual primitives, all already in the repo: **F** = FaceLandmarker
  (head pose / gaze ray), **P** = PoseLandmarker (33 keypoints, multi-person),
  **D** = object detector (object slots).

## The vocabulary (rows 1–10)

| # | Relation | Geometric trigger (T/F test) | Prim. | Theoretical anchor | Triggerability | Status |
|---|----------|------------------------------|-------|--------------------|----------------|--------|
| 1 | **gazing-at** (person → object) | head-orientation 2D ray hits object box, ±12° | F+D | ？ | high | ✅ gaze.py |
| 2 | **joint-attention** (people → shared target) | ≥2 gaze rays converge in front of both (t>0); stronger when the convergence point falls in an object box | F(+D) | ？ | med | ✅ gaze.py |
| 3 | **eye-contact** (person → robot) | 3D face-forward within ±12° of the face→camera direction | F |  (equilibrium theory); Kendon 1967; Media Equation | high | ✅ gaze.py |
| 4 | **pointing / reaching** (person → object or person) | elbow→wrist ray, emitted only when the arm is extended (elbow ≥140°); ray hits target box | P+D | Proto-declarative pointing — Bates et al. 1975; Kita 2003 | high | ✅ gaze.py |
| 5 | **proxemic zone** (person ↔ person) | pelvis-centre distance normalised by mean shoulder width → intimate / personal / social / public; shoulder width as the scale proxy (no depth needed) | P | **Proxemics — Hall 1966** (The Hidden Dimension) | high | ⬜ |
| 6 | **F-formation / facing** (person ↔ person) | shoulder-line normals (body orientations) point toward each other and their o-spaces overlap | P | **Kendon 1990** (F-formations); CV precedent: Cristani et al. 2011 | med | ⬜ |
| 7 | **approach / depart** (transition) | normalised person↔target (object/person/camera) distance decreases/increases monotonically beyond a threshold within a ~3 s window | P(+D) | ？ | med | ⬜ |
| 8 | **lean-in / hover-over** (engagement posture) | torso pitch (mid-shoulder→mid-hip vs vertical) tilted >15° toward an object/desk, sustained | P(+D) | Postural immediacy / lean | med | ⬜ |
| 9 | **hands-on / manipulating** (person–object contact) | wrist keypoint inside an object box (or within 0.1×diag of its edge) sustained ≥1 s; wrist extended toward another person with object in hand = the *offering* variant | P+D | ？ | high | ⬜ |
| 10 | **gathering / co-presence** (group state) | person count crosses a threshold (0→1, 1→2, ≥3 forming a cluster: pairwise distances < social zone) | P or D | Gatherings / face engagements — **Goffman 1963** (Behavior in Public Places) | high | ⬜ |
| 11 | **turn-taking / control handoff** (person ↔ person, via artifact) | the person whose wrist is inside / nearest the shared artifact's box "holds control" (sustained ≥1 s); a HANDOFF = the controlling person changes | P+D | Turn-taking | med (needs cross-frame state, like row 7) | ⬜ |

✅ = implemented & unit-tested (gaze.py) ⬜ = to build (5/9/10 easy geometry; 6/7/8 medium; 7 needs cross-frame state)

## Technical grounding — every trigger is an ESTABLISHED vision task

The geometric triggers are not inventions: each row maps onto a CV task with its own
literature and benchmarks, computed from primitives that are themselves standard
(MediaPipe framework — Lugaresi et al. 2019; FaceMesh — Kartynnik et al. 2019;
BlazePose — Bazarevsky et al. 2020). This is the *technical* twin of the theory column.

| Row | Established CV task | Precedent |
|---|---|---|
| 1 gazing-at | head-pose estimation + gaze following | Murphy-Chutorian & Trivedi 2009 (TPAMI survey); Recasens et al., NeurIPS 2015 ("Where are they looking?") |
| 2 joint-attention | shared-attention inference in video | Fan et al., CVPR 2018 |
| 3 eye-contact | eye-contact / attended-target detection | Chong et al., CVPR 2020; Ye et al. 2015 |
| 4 pointing | pointing-gesture recognition (HRI) | Nickel & Stiefelhagen 2007, Image Vis. Comput. |
| 5 proxemic zone | proxemics recognition from images | Yang et al., CVPR 2012 |
| 6 F-formation | F-formation detection | Cristani et al., BMVC 2011; Setti et al. 2015 (GCFF) |
| 7 approach/depart | person tracking / trajectory analysis | standard multi-object tracking (mature) |
| 8 lean-in | keypoint-based posture analysis | standard pose-landmark geometry |
| 9 hands-on | hand–object contact detection | Shan et al., CVPR 2020 (100DOH) |
| 10 gathering | person detection / group detection | mature; group: Cristani et al. 2011 |
| 11 turn-taking | hand–object contact (per person) + identity over time | composition of Shan et al. 2020 + standard tracking |

**The third leg is empirical and ours to produce:** a detectability mini-study on the
actual rig — the point being that **a small, low-cost camera suffices** for all ten
relations (frame rate and resolution are properties of the current module, not of the
approach, and may improve with hardware). Per relation, N staged positive and N staged
negative trials at room distances → hit rate / false-alarm rate, reported as a "measured
reliability" column. The protocol and recording sheet live in TEST_PLAN_system.md (section 2). Failure conditions (side-on pointing,
distance limits) go into the paper's limitations — measured, not guessed.

## Why these ten — selection rationale (the claim, stated carefully)

We do NOT claim these ten rows are *sufficient* for all scenarios (unprovable for an open
set). The defensible claim has two parts:

**1. Channel coverage (structural argument).** Nonverbal-communication research organises
social behaviour into established channels — gaze/oculesics, gesture/deixis, proxemics,
posture/kinesics, object manipulation, and group spatial organisation (Knapp & Hall,
*Nonverbal Communication in Human Interaction*; Vinciarelli, Pantic & Bourlard 2009,
*Social Signal Processing: Survey of an Emerging Domain*, Image and Vision Computing —
their taxonomy of behavioural cue classes). The vocabulary samples **every channel of
that taxonomy that is detectable from vision alone** with our three primitives (no audio,
no touch, no physiology):

| Channel (taxonomy) | Rows |
|---|---|
| gaze / oculesics | 1 gazing-at, 2 joint-attention, 3 eye-contact |
| gesture / deixis | 4 pointing |
| proxemics (static / dynamic) | 5 zones, 7 approach-depart |
| posture / kinesics | 8 lean-in |
| object-directed action | 9 hands-on |
| group spatial organisation | 6 F-formation, 10 gathering |
| interaction regulation (v2) | 11 turn-taking / control handoff |

So the selection is per-channel sampling under a detectability constraint, not an ad-hoc
list. Each row additionally carries its own grounding (the table above).

**2. Coverage as an empirical question (the probe).** Sufficiency is delegated to data,
at three complementary moments:

- **Plan-time probe (implemented; what the preliminary study measured).** The planner
  schema includes a mandatory `missing` field — text-only, no perception: the VLM reads
  the context against the vocabulary and names what it cannot express. Catches gaps that
  are *anticipatable from the context* (e.g., pair-programming → turn-taking).
- **Confirm-time probe (planned, zero extra cost).** When a conjunction fires and the
  VLM verifies the frame, it is additionally asked whether the claimed relation is what
  is actually noteworthy in the image — catching *misattribution* gaps (geometry fired,
  but the real story is outside the vocabulary).
- **Audit sampling (optional, deferred — the plan-time probe is structurally sufficient for vocabulary revision).** Vocabulary blind spots never
  fire, so they never reach the VLM — a structural blind spot. Remedy: sparse audits —
  occasional random frames, or frames flagged by the T/F log as suspicious (people
  present but all rows F for a long stretch), sent to the VLM with "is anything
  report-worthy happening that the watch-spec missed?" Sampled, not always-on, so cheap.

A low missing-rate across probes is empirical coverage evidence; named gaps are concrete,
citable inputs for vocabulary revision. The vocabulary is versioned and editable by
design — the contribution is the mechanism (VLM composing watch-specs over a
theory-grounded vocabulary), not this particular ten.

## Composition grammar — an open question, decided by the study

Two grammars are implemented in planner.py and compared as arms of the preliminary study:

- **restricted** — `watch` = up to 3 entries, each an AND over 1–3 ids with a time
  window; entries are alternatives (OR). Simple, easy to validate, easy to execute.
- **free** — entries may combine `all` (AND), `any` (OR), `not` (suppression), `then`
  (ordered sequence). More expressive — sequences in particular are socially meaningful
  (an F-formation *forming* is `10 then 6`, not `10 and 6`) — but a larger error surface.

Decision rule: if the free arm rarely uses `any/not/then`, or uses them without
face-valid need, ship restricted and cite the runs; if `then` appears often and sensibly,
the executor gains sequence support.

## Conjunction semantics (the watch-spec the VLM emits)

```json
{
  "watch": [
    {"all": [3, 5], "within_s": 2.0, "label": "someone seeks the robot up close"},
    {"all": [2, 4], "within_s": 2.0, "label": "showing something to each other"}
  ],
  "single_ok": [9],
  "duration_s": 600,
  "why": "context says they are assembling hardware together; handling and showing matter"
}
```

- `all` + `within_s`: every member relation must have been T within the window, each with
  persist ≥2 frames (strict same-frame conjunction fails at ~2 fps).
- When a conjunction is satisfied → record (frame + a slice of the T/F vector ±N s around
  the moment + label). **The same conjunction over the same participants enters a 60 s
  cooldown** (RelationGate reused with conjunctions as keys).
- CV logs the full 1–10 truth vector every frame → `relation_log.jsonl` (analysis data in
  itself: it shows how a conjunction *forms*, e.g. an F-formation assembling).

## Example conjunctions (few-shot for the planner prompt)

- **5(→personal) + 3** — someone approaches the robot and looks at it → seeking interaction
- **7(approach) + 1(gazing at the same object)** — walking toward what you look at → intentional act (Michalowski's engagement escalation)
- **2 + 4** — joint attention + pointing → "look at this" (the full joint-attention package of developmental psychology)
- **10(1→2) + 6** — a second person arrives and a face-to-face formation settles → a conversation begins (Kendon's F-formation forming)
- **9 + 8** — hands-on + leaning-in → focused manipulation; also usable as a NEGATIVE rule (do not record / do not interrupt)

## Mapping onto existing code (a re-arrangement, not a rewrite)

| New component | Source |
|---|---|
| T/F evaluators (rows 1–4) | gaze.py as-is; rows 5–10 each ~20–40 lines of new geometry |
| watch-spec executor | attention_demo's RelationGate + extract_relations, re-keyed to conjunctions |
| VLM planner (new) | judge.py-style anthropic call, new prompt + schema; offline mode as usual |
| recording / feed / web UI | publish / serve_ui / render reused unchanged |
| optional confirm step | judge's `confirm` kept: when a conjunction fires, the VLM may double-check the image (precision) |
