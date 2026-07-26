# Integration test plan — motion library → full session

Staged bring-up for the NoticeBot session rig. **Each stage adds exactly one
device or one layer**, so a failure is attributable to what was just added. Do
not skip ahead: the failure modes in this system are almost all silent, and a
combined run tells you nothing about which of four things is wrong.

Every stage has a numeric pass criterion. "Looks about right" is not decidable
in a system with three devices, a timing budget and a resolution floor — and it
is the reason several of the earlier problems here cost an afternoon each.

Written 2026-07-26. Target: HRI 2026 Design track, deadline 2026-09-18.

---

## Before every session

```bash
cd <repo root>          # all commands below are run from there
python3 robot/tools/preflight.py --cam 0
```

Must print **GO**. It checks: calibration complete, clips clean, state table
matches the clips on disk, three servos answering, bus voltage, torque enabled,
camera index yields a frame of the right size.

**No port to set.** Serial paths are probed, not configured: macOS names a
usbmodem device after its USB *location id* when the device has no serial number,
so the FE-URT2's path changes whenever it is replugged or comes up through a hub
— and the CoreS3 is also a usbmodem, so they cannot be told apart by name. The
servo bus is whichever port a servo answers a ping on; the CoreS3 is whichever
port greets with `cores3_sidekick`. `NOTICEBOT_PORT` is honoured if it still
exists, but it is only a hint. A hardcoded path is guaranteed to go stale, and it
fails as "the robot is dead" rather than "wrong port".

Camera indices move for the same reason — re-run `--list-cams` each session
rather than trusting a remembered number.

`NO-GO` on bus voltage below 5.0 V means external power is not reaching the bus
and the servos are limping on USB back-feed — nothing downstream is meaningful
until that is fixed.

---

## Stage 1a — motion alone

No camera, no CoreS3. Establishes that the timing contribution actually survives
on hardware.

```bash
python3 robot/tools/play_on_hardware.py --center
python3 robot/clip_player.py                       # walks the designed CYCLE
```

**Pass:**

| metric | threshold | why |
|---|---|---|
| dropped frames | **0** | a drop means the bus cannot sustain 30 fps; fix by lowering export fps in Blender, never in the player |
| max lag | **< 20 ms** | above this the ease curves are being re-timed, which is the thing the clips exist to preserve |
| `did not arrive` warnings | none | a joint that cannot reach its target is jammed, unpowered, or over-loaded |
| audible strain at any pose | none | check against `calibration.py` margins; S7b nod and S7a pan are the tight ones |

Then test the override: while `S5_TRACK` or `S8_ERROR` is looping, request
another state. It must take effect **within one frame (~33 ms)**, not at the end
of the loop. A player that finishes the clip first is unusable in a session.

`ST.CYCLE` deliberately excludes `S8_ERROR` — it is not part of the designed
cycle. Test it on its own: `python3 robot/clip_player.py S8_ERROR`.

---

## Stage 1b — the LED envelope

**The LED belongs to this stage, not to the CoreS3 stage.** Part of it is
authored *against the motion*: S4's shutter flash fires once the head has settled
at a station, S7b's peak lands on the top of the toss. Firmware cannot know when
those moments are, so the level travels in the clip's `led` column and the player
streams it. Verifying that needs the servos and the CoreS3 — but no camera.

Isolate the firmware first, because "no LED response" looks identical whether the
clips, the transport or the firmware is at fault, and the old firmware ignores an
unknown `EVT` silently:

```bash
python3 robot/clip_player.py --led-test --cores3      # touches no servos
```

**Pass:** the LED steps between dark and full five times. If it only keeps
breathing, `EVT LED` is not in the flashed firmware yet.

Then the real thing:

```bash
python3 robot/clip_player.py --cores3
```

**Pass:**

| metric | threshold |
|---|---|
| `N/9 clips carry an led envelope` | **9/9** (a *constant* column counts as none — see below) |
| `led updates sent` | hundreds, not 0 |
| lag / dropped | unchanged from Stage 1a |

Then watch the three places the sync actually matters:

- **S4** — five flashes, each *after* the head stops, not during the move
- **S7a** — a beat on each of turn / crane / head-up
- **S7b** — the peak on the *upward* stroke; dark on the way down

A constant `led` column is treated as "nobody authored an envelope for this clip"
and is not streamed. That is deliberate: the firmware only falls back to its own
breathing after 500 ms of silence, so streaming a constant would pin the LED solid
forever — and S1/S8 are exactly the states where looking alive matters most. Both
now have generators, so all nine should be authored.

---

## Stage 2 — camera alone

```bash
python3 ../../notice-sidekick-runkit/noticebot_loop.py --list-cams
```

**Pass:** the head camera appears at a known index at **1280×720 or better**. If
you see 640×480, that is probably the laptop's built-in — the index moves when
anything else USB is plugged or unplugged, so confirm it every session rather
than trusting a remembered number.

Sanity-check the stream on its own:

```bash
python3 noticebot_loop.py --list-cams
```

**Pass:** ≥ 15 fps sustained. The OV4688 does 1080p60 over UVC, so anything in
single digits means the format negotiated down — the loop requests MJPG for
exactly this reason.

---

## Stage 3 — camera + motion: the settled-frame gate

This is the stage that validates the head-mounted camera concept. Nothing else
tests it.

```bash
python3 noticebot_loop.py --cam 0
```

Press `4` for `S4_PLAN` and watch the overlay.

**Pass:**

- The badge reads **STILL** exactly **5 times** during the sweep — one per
  station — and **MOVING** in between.
- Frames captured while STILL are sharp; frames while MOVING are visibly
  blurred. If they are not, the sweep is too slow to be worth gating, or too
  fast for the exposure — either way that is a finding, not a nuisance.
- Each STILL window lasts **> 400 ms** (measured: 0.44–0.62 s).

Failure here is architectural, not a bug: it means either the camera needs a
shorter exposure, or `SETTLE_MS` in `noticebot_loop.py` needs to move, or the
station dwell in `generate_s4_sweep.py` needs lengthening.

### Use the existing web UI instead of the cv2 window

`webui/server.py` works unchanged — same `STATE`/`LOCK` contract:

```bash
python3 noticebot_loop.py --cam 0 --serve            # window + web
python3 noticebot_loop.py --cam 0 --serve --no-view   # web only
# open http://localhost:8000  (or http://<laptop>.local:8000 from a phone)
```

Until perception is wired there are no WatchExecutor entries, so the PLAN panel
shows the **state machine** in the same slots: the nine states with the current
one marked satisfied, and live lag / dropped / settled / pose underneath.

**Prefer `--serve --no-view` for anything with a participant in the room.** Not
for convenience: a laptop beside the robot showing bounding boxes and state names
tells the participant what to believe the robot is, which contaminates exactly
what the study is measuring. The web UI puts that screen somewhere else — a
second machine or a phone — and leaves the robot to speak for itself.

The browser's context box is also a free wizard channel: typing a sentence posts
to `/context`, which the loop reads and uses to enter the designed cycle at
`S2_LISTEN`. During a session, type what the participant actually asked.

---

## Stage 4 — CoreS3 input

The LED output was already covered in 1b; what is left is the *input* half —
touch and buttons — which nothing else exercises.

```bash
cd ../../notice-sidekick-runkit
python3 cores3_link.py /dev/cu.usbmodemXXXX
```

**Pass:** screen and chirps fire on the scripted `EVT` sequence; pressing the
buttons and tapping the body prints `IN TAP …` / `IN BODYTAP`.

---

## Stage 5 — all three

```bash
cd ~/Documents/Claude/Projects/potatobot
python3 notice-sidekick-runkit/noticebot_loop.py --cam 0 --cores3 --serve
```

Both `--cores3` and the servo port auto-detect; `--cam` still needs its index.

**Pass:**

- `c` enters the designed cycle and it runs `S2 → S3 → S4 → S5` unattended.
- Body tap during `S5` switches to `S6_FINETUNE` and returns to `S5`.
- Keys `1`–`8` still override instantly with all three devices live.
- LED colour follows `states.py` (`warm` in S1/S7, `cool` in S2–S6, `alarm` in
  S8) while the *level* still follows the clip.

**Watch for the one thing that only appears here:** serial contention. The servo
bus and the CoreS3 are separate ports, but both are driven from the same process,
and the CoreS3 redraws its screen inside its own `loop()`. Compare lag against
the Stage 1b figure — if it rose, the LED/screen traffic is stealing from the
player, and the fix is to throttle the CoreS3's screen redraw, not the motion.

---

## Stage 6 — perception: S4 station scoring (no VLM)

First autonomous layer. Detector only, no API key, so a failure is CV or wiring
and cannot be the model.

In `noticebot_loop.perceive()`: score each settled S4 frame with
`perceive.make_detector("yolo", vocab)` and keep the best index.

**Pass:** 5 scores per sweep, one per station, each attributable to the correct
pan unit. The sweep runs right-to-left (Blender +60° → −60°), and INVERT mirrors
that, so `ctx["stations"]` should come out in this order:

| station | Blender pan | servo unit |
|---|---|---|
| 1 | +60° | 303 |
| 2 | +30° | 405 |
| 3 | 0° | 507 |
| 4 | −30° | 610 |
| 5 | −60° | 712 |

If the order is reversed, the S4 CSV predates the direction change and needs
re-exporting.

---

## Stage 7 — retargetable S4

The richest station is chosen at runtime, but `RICHEST_DEG` is baked in at export
time. **S5 can simply be offset** (a static hold has no authored timing to
lose). **S4 cannot** — its return is a decelerating curve authored for one
distance, and stretching it rewrites the timing.

So: export **five S4 variants**, `RICHEST_DEG` set to each station, each with its
own correctly-derived `RETURN_F`. Then the player picks the variant.

**Pass:** all five variants pass `check_clips.py` with no clamping and no
handover problem into `S5_TRACK`.

---

## Stage 8 — S5 WatchExecutor

`watch_exec.py`'s `persist` counts **consecutive frames**, which assumes an
unchanging view. That assumption holds in `S5_TRACK` and nowhere else: S4's
viewpoint changes every station, so a streak accumulated across stations is
meaningless and can fire spuriously.

Run the relation gate **only while the state is `S5_TRACK`**, and reset its
streaks whenever the state changes.

**Pass:** a held relation fires once, then does not re-fire within `cooldown`;
breaking and re-forming it fires again.

---

## Stage 9 — judge / VLM

`judge.judge(jpeg, graph, taste)` on the S5 frame that fired, before `S7a`.
Needs `ANTHROPIC_API_KEY`. Test `--offline` first so the plumbing is proven
without the model in the loop.

**Pass:** a confirmed report reaches `S7a` within a latency you are willing to
have a participant sit through. Measure it — if it is seconds, the state machine
needs something to say while it waits, and that is a design question, not an
engineering one.

---

## Stage 10 — pilot, then study

1. **Dry run, no participant.** Full cycle end to end, three times. Nothing
   should require a keypress.
2. **Pilot with one colleague.** The thing to watch is not whether it works but
   whether the states are *read* the way they were designed — particularly
   `S6` as negation and `S7b` as a summons rather than a nod, since those two
   are the grammar claims.
3. **Session protocol.** Fix `SETTLE_MS`, cooldowns and the S4 station count
   before the first real participant; changing them mid-study makes the sessions
   non-comparable.

---

## Known constraints to design within

| constraint | value | source |
|---|---|---|
| pan travel | −70° … +76° | cable loom through the pan axis, not the servo |
| tilt travel | ±29° (guard rail; no mechanical stop exists) | chosen from clip demand + margin |
| nod travel | +26° up … −47° down (Blender signs) | measured; **up is the tight side**, and S4/S5/S7 all use it |
| servo resolution | 1 unit = 0.293° | SCS0009. Below ~8 units nothing visibly moves |
| slow + small + smooth | **not achievable** | step count is set by amplitude; stretching the duration only spreads the same steps |
| speed ceiling | 200°/s authored, 120°/s for unauthored moves | 200 is a no-load figure at 6 V; the rig is loaded and on batteries |
| camera | OV4688 UVC, H-FOV 58° | station spacing must stay under 58° or the sweep leaves gaps |
| forward coverage | 178° = 2×60° sweep + 58° FOV | matches the study requirement; widening the sweep buys nothing |
