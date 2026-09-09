# DEMO RUNBOOK — the exhibition stand

Branch `demo-expo-2026`. The user-study build is `cassie/tomato`, tagged
`s2-study-build`; nothing here is meant to go back to it.

**The robot is unchanged** — same firmware, same English screen, same faces.
Everything a visitor reads is on the iPad, so translating the stand means
translating one file (`webui/booth.py`, the `CHOICES` list at the top).

---

## 0. Once — the touch sensor

A TTP223 capacitive module on the head. The only hardware the demo adds, and the
firmware already supports it (`USE_TAP 1`, `TAP_SRC 1`).

```
SIG -> GPIO 17   (Grove Port C)
VCC -> 3V3       NOT 5V. ESP32 GPIO is not 5 V tolerant.
GND -> GND
```

Check it before anything else:

```bash
python3 robot/tools/touch_test.py --cores3 /dev/cu.usbmodem11201
```

One `IN BODYTAP` per touch. Random 0/1 with nothing touching means SIG is not
reaching the pin. **No sensor to hand?** Set `TAP_SRC 2` in the `.ino` and
reflash — the onboard IMU reports a knock instead, no wiring at all.

---

## 1. Each morning — is the VLM fast today?

```bash
python3 -m robot.tools.net_check
```

Read **FLOOR** (see §2b of the main RUNBOOK):

| FLOOR | what it means |
|---|---|
| 1–2 s | planning will be 5–8 s. The sweep screen carries it easily. |
| 7–10 s | planning will be ~20 s. Still works; stand next to the visitor and talk over it. |

A slow FLOOR is Google queueing and **cannot be fixed from here** — not by the
network, not by the tier, not by sending fewer frames. Measured across two days:
RTT 0.03 s while FLOOR swung 1.3 → 20 s, and a hotspot made RTT *worse* without
touching FLOOR.

**Do not change `NOTICEBOT_GEMINI_MODEL`.**

---

## 2. Start it — ONE terminal, all day

```bash
cd ~/Documents/Claude/Projects/potatobot/notice-sidekick-runkit
git checkout demo-expo-2026

python3 noticebot_loop.py \
    --cam 0 \
    --cores3 /dev/cu.usbmodem11201 \
    --detector yoloworld \
    --cv-hz 4 \
    --feedback robot \
    --serve --conf 0.2
```

Identical to the study command. It prints two addresses:

```
open  http://localhost:8000                 <- the researcher's page
      http://192.168.x.x:8000/booth         <- type THIS on the iPad
```

**Do not restart between visitors.** Every story lands in one
`attention_log.jsonl`, and the day's wall on the tablet is that file. A restart
opens a new session folder and the wall goes empty.

There is no second terminal. The study's file server (main RUNBOOK §4b) serves a
static page that must outlive a loop restart; the tablet has to *control* the
loop, so it lives in the same server.

---

## 3. The iPad

Open the printed address in Safari, then once:

1. **共有 → ホーム画面に追加.** Opening from the Home Screen is full-screen with
   no address bar, so a visitor cannot navigate away.
2. **設定 → アクセシビリティ → アクセスガイド** on. Triple-click to lock it to
   the page.
3. **設定 → 画面表示と明るさ → 自動ロック → なし.**

### Bring your own network

Venue Wi-Fi very often has **client isolation**: two devices on the same SSID
cannot reach each other, and there is no fixing it on the day. Bring a travel
router or use a phone hotspot, and put the laptop and the iPad both on it with
the laptop reaching the internet through the same box.

Test before doors open: if `localhost:8000/booth` works on the laptop and the
iPad hangs, it is isolation.

---

## 4. What a visitor does

```
   ねている        Idle. Sleeping face on the robot AND on the iPad --
                   (-_-) z z z and one line: あたまに そっとさわって
                   ください. No menu: a stand with a list of options on
                   it is a kiosk.
1  頭にさわる      Tap -> it lifts out of the bow and looks up, and the
                   THREE CHOICES appear on the iPad.                    (S2)
                   The tap has a consequence on both screens at once,
                   which is what teaches the gesture.
2  iPadで選ぶ      Three Japanese sentences. Tapping posts the ENGLISH
                   one through the same door speech uses, so the planner
                   really compiles it.
3  うなずく        Acknowledge. 1.7 s, no page of its own -- the strip
                   shows ^o^ and the page stays put.                    (S3)
4  部屋を見る      Scan. Five stations appear on the iPad as they are
                   captured; when the plan lands the boxes are drawn and
                   the chosen one goes red.                             (S4)
5  見張る          Watch. SAME SCREEN. The red cell is now the LIVE
                   camera; the other four stay as sweep stills.         (S5)
   頭にさわる      -> Correct. It looks elsewhere and the red frame moves
                   with it.                                             (S6)
6  よぶ            Call. Chirp + lean. The same prompt appears on the
                   iPad and on the robot's screen.                      (S7)
7  OK             Either screen. The robot nods and goes straight back
                   to watching; the TABLET stays behind and waits for
                   the report -- 6-45 s, with a face and a moving bar.
8  Then the story: the strip at full height, swiped sideways, sentence
   underneath.  とじる returns to the choose screen.
```

**The tap means two things and the robot decides which** — asleep it means "look
at me", watching it means "not that one". Both are the same sentence: put your
attention where I am pointing it.

### The two extra touches on the room screen

- **Tap the red cell** → the live view fills the screen, everything else dims to
  10–14%, and the rule stays lit in the sixth cell. Tap again to go back.
- **The sixth cell is the rule** — relation chips that light as each becomes
  true, the AND/OR/THEN between them, and the object they are bound to. This is
  what tells a visitor *how to trigger it*, and it is the panel to point at when
  somebody asks how it works.

### The top strip

```
[-_-] [._.] [^o^] [・・・] [o_o] [>_<] [\^o^/]              [☰ 3]
ねてる きづいた わかった みてる  みはり ちがう よんでる        きろく
```

The faces are copied from the firmware's `uiFace()` — the same characters the
robot's own screen shows, so a visitor looking from one to the other sees the
same thing twice. `☰` opens the day's stories; it hides itself while open.

---

## 4b. The keys, from the terminal

They work in the terminal the loop was launched from — no need to click the
preview window first.

| key | does |
|---|---|
| `1`…`8`, `0` | force a state (1 Idle · 2 Attend · 3 Acknowledge · 4 Scan · 5 Watch · 0 Settle · 6 Correct · 7 Call · 8 Error) |
| `g` | S2_LISTEN — the same lift the head tap produces, for testing without the sensor |
| `f` | force a finding — the keyboard twin of **notice this NOW** |
| `r` | torque off (press a state key to re-engage) |
| `q` | quit |

Useful before the sensor is wired: `g` → tap a choice on the iPad → `f` → OK.
That walks the whole demo without touching the robot.

---

## 4c. Your break — the stand runs itself

```bash
# stop the loop first: Ctrl-C. Both open the same two serial ports.
python3 -m robot.tools.attract
```

Walks the whole eight-state cycle — motion, screen, light and sound together —
and loops until Ctrl-C. About 55 s a pass. `--gap 1.5` slows it, `--once` does a
single pass.

Each beat waits for its clip to finish and *then* holds, so a gesture is never
cut off mid-turn. Clip lengths are read from the CSVs at startup rather than
kept in a table, so re-exporting from Blender cannot make the timing stale.

A motionless robot on a table reads as broken, and one that only moves when
somebody works the tablet is invisible from across the hall.

**It is NOT the system.** No camera, no VLM, no watch-spec, nothing recorded —
authored clips on a timer. The board shows `DEMO LOOP` and the terminal says so
too, because at a research stand that reading has to be corrected before it is
made, not after somebody has told a colleague about it. If anyone asks whether
it is really watching right now: no, and start the real loop to show them.

Ctrl-C releases the joints, so the head does not fight the next person who picks
the robot up to look underneath.

---

## 5. When it does not fire

Both controls are on the **laptop's** page (`http://localhost:8000`, LIVE tab,
**IF IT MISSES THE SCENE**):

- **notice this NOW** — skips the trigger and the confirmation judge. The robot
  performs the notice and collects the story exactly as if it had fired. The
  visitor cannot tell.
- **look around again** — a fresh sweep on the same request.

Give it ten seconds first. A real trigger is better than a forced one, and the
visitor is usually still reading the tablet.

---

## 6. What is different from the study build

Worth knowing, because someone will ask:

| | study | stand |
|---|---|---|
| brief | spoken, Whisper | two Japanese buttons → the same English sentence → the same planner |
| `REPLAN_PERIOD_S` | 540 s — one self-directed sweep per session | **0** — at a stand it lands mid-visit and reads as a fault |
| tablet | review page only, second process | the whole interface, in the loop's server |
| head tap | Correct only | Correct **and** wake |

The VLM path is **byte-identical** to `s2-study-build`: `perception/`,
`planning/planner.py`, `planning/judge.py`, `planning/provider.py`,
`planning/gemini_provider.py`, `robot/`, `motion/` — no diff. The only change
under `planning/` is `sweep_plan.py` recording its own output directory, which
no prompt ever sees.

---

## 7. Changing the wording

`webui/booth.py`, `CHOICES` at the top. Japanese for the visitor, English for
the planner; **the planner never sees Japanese**. Restart the loop.

The three shipped are:

| | asks for | why |
|---|---|---|
| 荷物に触ったら教えて | hands-on the visitor's own bag | "that is mine" is what makes delegating mean anything |
| 人が集まったら教えて | gathering | ambient; a crowded hall supplies it for free |
| ポスターの前で発表がはじまったら教えて | people gathering **in front of a poster** | **the room the stand is actually in** — presentations start at the posters all day. Costs the visitor nothing to arrange, happens whether or not anyone waits for it, and makes the point better than a staged event could. Bound to the poster, which is what keeps it from compiling to the same entry as `gather` |

Keep it to three or fewer. A visitor deciding between two or three things reads
them; a visitor deciding between five picks the first one.

---

## 8. End of the day

Ctrl-C once. The teardown flushes any story still collecting, so the last
finding of the day is on disk rather than lost with the process.

`session_feed/<run>/` is the whole day: every report, every sweep, every LLM
call, and `review.html`.

---

## Troubleshooting

| symptom | first suspect |
|---|---|
| iPad hangs, laptop is fine | venue Wi-Fi client isolation (§3) |
| tablet stuck on the choose screen | the loop is not publishing — check the terminal for a traceback |
| tapping the head does nothing | `touch_test.py` first; then whether the robot is in the state that accepts it |
| nod, then sleep, no sweep | an `arm_next` ordering regression — every caller must `request()` first and `arm_next()` second |
| the wall is empty | the loop was restarted; the wall is one `attention_log.jsonl` |
| planning takes 20 s | `net_check`. If FLOOR is high it is Google, and nothing here helps |
| the live cell flickers black | the stream got moved inside the markup `render()` rewrites — it must stay outside `#app` |
| in `attract`, the screen changes but nothing moves | `player.start()` is missing — `request()` only records what the player should do; a worker thread feeds the bus. It looks exactly like a servo-power fault and is not |
| the robot fights your hand | you are in `attract` and it has not been Ctrl-C'd, or a state key re-engaged torque. `r` releases |
| keyboard keys do nothing | fixed 2026-09-09 — the terminal used to be read only under `--no-view`, so with a preview window open `f`, `g` and the state numbers only worked when THAT window had focus. If it regresses, look for an `a.no_view` gate on `stdin_key()` |
