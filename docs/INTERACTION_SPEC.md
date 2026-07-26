# Interaction spec — triggers, CoreS3 screen, and who decides what

Captured 2026-07-26 from the design decisions. This is the contract the state
machine, the CoreS3 firmware and the developer web UI all implement. Where a
decision is still open it says so explicitly rather than being guessed at.

Motion, LED and sound are already specified in `hardware/states.py`. This
document covers the two channels that were still missing: **what causes a state
change**, and **what the participant sees on the CoreS3**.

---

## The two-audience rule

Every surface in this system belongs to exactly one audience, and that decides
what may appear on it:

| surface | audience | may show |
|---|---|---|
| the robot itself | participant | motion, LED, sound |
| CoreS3 screen | participant | one state, in plain words, with at most two buttons |
| developer web UI | researcher | everything — feed, poses, plan, overrides |

The CoreS3 is *not* a debug display. Anything a participant cannot act on does
not belong there — a screen that explains the system changes what the
participant believes the robot is, which is the thing the study measures.

---

## Transitions

| from | to | trigger |
|---|---|---|
| S1_IDLE | S2_LISTEN | **PTT pressed** (green, CoreS3) |
| S2_LISTEN | *holds* | **PTT released** → screen becomes `waiting...`, still facing the person |
| S2_LISTEN | S3_ACK | a **usable transcript** exists |
| S2_LISTEN | S8_ERROR | the transcript is **unusable** (silence, gibberish, timeout) |
| S3_ACK | S4_PLAN | automatic — nothing here waits any more |
| S4_PLAN | S5_TRACK | **automatic — no human confirm.** S6 exists to correct a bad choice, so a confirm step here would only add a decision the participant does not need to make |
| S5_TRACK | S6_FINETUNE | **body tap** on the touch sensor = "not that one" |
| S6_FINETUNE | S5_TRACK | automatic, after the researcher has re-aimed pan |
| S7b | S5_TRACK | **OK pressed** (green), or **30 s with no response** |
| *any* | S1_IDLE | **STOP pressed** (red) — always available |

Notes on two of these:

**S6 does not re-plan.** The tap says "wrong direction", not "wrong task". The
watch-spec stays; only the direction changes. Re-planning would discard the
request the participant already made, and make a correction feel like starting
over.

**S7's 30 s timeout is not a failure path.** Being ignored is a normal outcome —
the person is busy, which is the whole premise of notice delegation. The finding
is already in the feed, so the robot returns to watching rather than escalating.
That is the difference between a colleague and an alarm.

---

## CoreS3 screen, per state

Two button slots. Green is always affirmative, red is always STOP; nothing else
ever occupies those positions, so a participant never has to read a button to
know what it does.

Nine screens for eight states — `waiting` has no state of its own.

| screen | state | text | buttons |
|---|---|---|---|
| `idle` | S1_IDLE | `idle` | **PTT** (green, large) · **STOP** (red, large) |
| `recording` | S2, button held | *no text* — a level bar | none |
| `waiting` | S2, button released | `waiting...` | STOP |
| `heard` | S3_ACK | `I heard you.` | STOP |
| `planning` | S4_PLAN | `planning...` | STOP |
| `tracking` | S5_TRACK | `tracking...` | STOP |
| `notthat` | S6_FINETUNE | `not that!?` | STOP |
| `noticed` | S7a / S7b | `<n> noticed` — session cumulative | **OK** (green) · **STOP** (red) |
| `error` | S8_ERROR | `error` | STOP |

`recording` deliberately has no words and no buttons: the participant is
mid-sentence with a finger on the button, and text would be asking them to read
while they talk. The bar says only that the microphone is live.

---

## Re-aiming in S6 (the "pointing" substitute)

Finger/pointing recognition is out of scope. Instead the **developer web UI gets
one button per pan station**, and the researcher steers the head.

This is a Wizard-of-Oz stand-in and the paper has to say so. What it buys: the
*interaction* being studied is "tap to reject, robot re-aims and keeps watching",
and that is intact regardless of whether the direction came from a gesture
recogniser or from a person clicking. The recogniser is not the contribution.

Mechanism: S5_TRACK is a **static hold**, so it can be re-aimed by offsetting the
held pan at playback time — no new clip and no re-export. (S4 could not be
retargeted this way; its return is an eased curve authored for one distance. See
`TEST_PLAN_integration.md`, Stage 7.)

---

## Decisions (settled 2026-07-26)

**1. Transcript: real STT (Whisper) on the laptop.** It already worked in the
earlier rig, just unreliably.

**2. The laptop owns the noticed counter** and pushes `EVT NOTICED <n>`. It
already keeps the feed, so this is one source of truth, and it survives a CoreS3
reboot mid-session.

**3. STOP is a CANCEL, not a pause.** The watch-spec is discarded; the robot
returns to S1 and waits for a new request. PTT after STOP therefore starts fresh.

### Consequence A — the microphone is on the laptop, not the CoreS3

PTT only *brackets* the recording: `IN PTT_DOWN` starts it, `IN PTT_UP` stops it,
and the laptop records from its own mic throughout.

Streaming the CoreS3's mic over the existing link is not possible, and the
arithmetic is worth writing down so nobody tries: 16 kHz 16-bit mono is
32 kB/s, and the serial link is 115200 baud ≈ 14 kB/s. Even 8 kHz 8-bit (8 kB/s)
would occupy well over half the link that also has to carry the LED level stream.
A second connection would be needed, and a laptop mic is better positioned for
speech anyway.

### Consequence B — S2 waits; S3 does not start until the words exist

Whisper takes longer than S3's 1.13 s of motion, so something has to wait. It is
**S2**, not S3:

  - PTT released → S2's clip has already finished, so the robot simply **holds,
    facing the person**, and the screen changes to `waiting...`;
  - a usable transcript arrives → **S3 fires, and the nod lands together with
    `I heard you.`**;
  - unusable → **S8**.

The nod and the words are *one* act of acknowledgement. Nodding first and
producing the words afterwards would be claiming to have understood something not
yet read — the gesture would be running ahead of the fact. Holding still while
waiting is also honest in a way a nod is not: it says "I'm working on it".

This is why the screen vocabulary has **one more entry than there are states**.
`waiting` is a screen, not a state: nothing about the robot's behaviour changes,
only what it is telling you. Screens therefore live in their own `SCREENS` dict
rather than as a field of the state.

### Unusable vs merely wrong — two different failures

**A misread must NOT go to S8.** The researcher fixes it by typing the correction
in the web UI, and the run continues. Sending a recoverable problem to an error
state would waste a session over a word.

**Absence and nonsense must go to S8**, and visibly: silence, mumbling, a wall of
garbage. A robot that confidently plans against nonsense is worse than one that
admits it did not get a request — and S8's amber + `lost` sound is exactly the
"I have nothing to work with" signal. Intuitive is the point.

So the bar is *"is there a request here at all"*, not *"is it a good one"*.
`states.py: STT_REJECT` holds the thresholds:

| check | value | what it catches |
|---|---|---|
| `min_chars` | 4 | "uh", a click, a single syllable |
| `min_words` | 2 | in-scope requests are at least verb + object |
| `max_no_speech_prob` | 0.6 | faster-whisper's own "there was no speech here" |
| `min_avg_logprob` | −1.0 | very low = decoding was guessing → hallucinated filler |
| `require_letters` | True | all-punctuation output is a known Whisper artefact |

Erring strict is the safer direction: a rejected real request is rescued by the
researcher in seconds, while a false accept produces a confident plan against
nothing and cannot be rescued at all.

### Whisper reliability, since "unstable" was the reported problem

Short commands are the hardest case for Whisper, and four things help
disproportionately:
  - **pin the language** — auto-detect on 2 seconds of speech is a coin toss;
  - **use `small` or better**, not `tiny`/`base`, via `faster-whisper` for speed;
  - **pad the clip** to ≥ 2 s of audio; very short input produces hallucinated
    filler;
  - **set `initial_prompt`** to the vocabulary you expect (the object names that
    can be in the room). This is the single biggest win for short imperatives,
    because it biases decoding toward the words that matter here.

---

## Build order

1. **Camera** — Stage 2/3 of the test plan. Independent of all of the above.
2. **CoreS3 screens + buttons** — firmware: per-state layouts, `IN OK`,
   `IN STOP`, `IN PTT_DOWN/UP`, recording bar, `EVT NOTICED <n>`.
3. **Trigger wiring** in `noticebot_loop.py` — the table above, plus the 30 s
   timeout.
4. **Developer web UI**: pan station buttons, the feed, the context box.
5. **CV/VLM** — the stages already planned (S4 station scoring → retargetable
   S4 → S5 WatchExecutor → judge).

Steps 2–4 are all Wizard-of-Oz-able and can be tested with no perception at all,
which is the point: the interaction can be piloted before the autonomy exists.
