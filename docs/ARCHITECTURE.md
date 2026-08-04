# Architecture

Four layers, one conductor. `noticebot_loop.py` is the only long-running process;
everything else is a library it drives.

```
                       ┌──────────────────────────────┐
   participant ──PTT──▶│  session/   state machine    │◀── researcher (webui/)
   participant ──tap──▶│  S1…S8, screens, sound, STT  │
                       └───────┬──────────────┬───────┘
                    state name │              │ request text
                               ▼              ▼
                       ┌───────────────┐  ┌──────────────────────┐
                       │   robot/      │  │     planning/        │
                       │ clip player   │  │ sweep → ONE Gemini call│
                       │ → SCS0009 ×3  │  │ → watch-spec + tiers │
                       └───────┬───────┘  └──────────┬───────────┘
                     pose, deg │                     │ detect / focus vocab
                               ▼                     ▼
                       ┌──────────────────────────────────────────┐
                       │            perception/                   │
                       │ detector → scene graph → 11-row truth    │
                       │ → WatchExecutor → fired entries          │
                       └──────────────────┬───────────────────────┘
                                          │ a finding
                                          ▼
                              session/storyboard → session/feed
```

## The order of a round, and why it is that order

**S1 idle → S2 listen.** PTT held on the CoreS3. Audio is captured on the
*laptop* mic, not the board: 16 kHz 16-bit mono is 32 kB/s and the serial link
carries about 14 kB/s.

**S2 → S3 acknowledge.** Whisper returns text. The nod and "I heard you." are one
act and neither happens until the words exist — nodding earlier would claim
understanding of something not yet read. Unusable text (silence, one syllable,
punctuation) goes to S8 instead, visibly.

**S3 → S4 plan.** The head sweeps its stations and grabs **one pure frame per
station** — no detector, nothing drawn. Annotating a frame before the model reads
it feeds our own guesses back as its judgement.

**End of S4: the single Gemini call.** The five raw frames are sent as separately
labelled spatial images in one request. A grid is written only for local researcher
visualization and is not model input. Back comes:

- `seen` — everything in the room
- `detect` — context tier: enriches, cannot trigger
- `focus` — may trigger
- `boxes` — per object, mapped back to the angle it came from
- `watch` — the compiled spec, over rows 1–11

**S4 → S5 track.** The clip ends before the VLM does, so the head **holds the last
angle it swept** and the screen keeps saying `planning...`. Only when the answer
lands does it turn to the richest-scoring angle and say `tracking...`. Claiming to
track while the answer is in flight is a statement about the robot's state that
is simply untrue.

**S5 watching.** `focus`/`detect` become the detector's vocabulary — `set_vocab`
for an open-vocab model, a synonym-expanded whitelist for closed COCO YOLO. Each
settled frame produces a truth vector; `WatchExecutor` decides which entries are
satisfied; `_focus_ok` refuses any gaze or point that did not land on a focus
object. Without that last gate, "someone looked at something" fires on every
chair in the room.

**A fired entry → candidate → Gemini confirm.** A raw-frame ring buffer selects
five ordered frames at t−1, t−0.5, onset, t+0.5 and t+1 seconds. Only a confirmed
candidate is reported. The safe default is one console feedback line; S7 motion
requires explicit `--feedback robot`.

**S6, "not that one."** A body tap during S5. The watch-spec survives; only the
direction changes. If nobody answers within 15 s the robot moves to the *next-best*
angle from the sweep rather than back to the rejected one.

## Rules that cross layer boundaries

**Frames are only used when the head is still.** `settled_ms > SETTLE_MS`. A
frame grabbed mid-move is blurred *and* attributed to a pose the head has already
left.

**Perception may fail forever without anything else noticing.** The state
machine, the screens and the researcher's keys do not depend on it. A run that
dies mid-study costs a participant.

**Every finding goes through one door.** A watch entry firing, the researcher's
`f` key and a forced state all reach `session/feed.py` the same way. When the
storyboard hung off the detector while the screen counter hung off the state
machine, the board read 3 and the feed read 0 — they were counting different
events.

**One owner per channel.** Screen ← state machine. Antenna colour ← state table.
Antenna brightness ← the playing clip. Sound ← the clip's `sfx`. The v1 firmware
had legacy commands that set colour and text as *side effects*, which is how S6's
red negation got painted blue.

## Testing without hardware

`session/session_flow.py` contains the transition rules and nothing else — no
serial port, no frame grab, no sleep. `tests/test_session_flow.py` drives it with
a fake clock, 86 assertions. The awkward paths (STOP mid-scan, a transcript that
arrives after its timeout, a tap in the wrong state) are exactly the ones that are
tedious to trigger by hand and the ones a participant finds in five minutes.
