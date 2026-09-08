# DEMO RUNBOOK — the exhibition stand

Branch `demo-expo-2026`. The study build is `cassie/tomato` / tag
`s2-study-build`; nothing here is meant to go back to it.

The robot is **unchanged** — same firmware, same English screen, same faces.
Everything the visitor reads is on the iPad.

---

## 0. Once — wire the touch sensor

A TTP223 capacitive module on the head. This is the only hardware the demo adds,
and the firmware already supports it (`USE_TAP 1`, `TAP_SRC 1`).

```
SIG -> GPIO 17   (Grove Port C)
VCC -> 3V3       NOT 5V. ESP32 GPIO is not 5 V tolerant.
GND -> GND
```

Check it before anything else:

```bash
python3 robot/tools/touch_test.py --cores3 /dev/cu.usbmodem11201
```

Touch the head → `IN BODYTAP` per touch. Random 0/1 with nothing touching means
SIG is not reaching the pin. No sensor to hand? Set `TAP_SRC 2` in the `.ino` and
reflash — the onboard IMU then reports a knock instead, no wiring at all.

---

## 1. Every morning — is the VLM fast today?

```bash
python3 -m robot.tools.net_check
```

See §2b of the main RUNBOOK. FLOOR under ~2 s: planning will be 5–8 s and the
sweep screen carries it. FLOOR at 8–10 s: planning is 20 s, and the visitor is
watching five frames appear for that whole time — still works, but stand next to
them and talk.

---

## 2. Start it — ONE terminal

```bash
cd ~/Documents/Claude/Projects/potatobot/notice-sidekick-runkit
python3 noticebot_loop.py \
    --cam 0 \
    --cores3 /dev/cu.usbmodem11201 \
    --detector yoloworld \
    --cv-hz 4 \
    --feedback robot \
    --serve --conf 0.2
```

**One process, all day.** Do not restart between visitors: every story lands in
one `attention_log.jsonl`, and the wall on the tablet is that file. Restarting
starts a new session folder and the wall goes empty.

There is no second terminal here. The study's file server (main RUNBOOK §4b)
serves a static page that must outlive a loop restart; the tablet has to
*control* the loop, so it lives in the same server.

---

## 3. The iPad

```bash
ipconfig getifaddr en0          # the laptop's address on the LAN
```

On the iPad, Safari → `http://<that address>:8000/booth`

Then, once:

1. **Share → ホーム画面に追加.** Opening from the Home Screen is full-screen with
   no address bar, so a visitor cannot navigate away.
2. **設定 → アクセシビリティ → アクセスガイド** on. Triple-click to lock the iPad
   into the page.
3. **設定 → 画面表示と明るさ → 自動ロック → なし.**

### Bring your own network

Venue Wi-Fi very often has **client isolation**: two devices on the same SSID
cannot reach each other, and there is no way to fix that on the day. Bring a
travel router (or use a phone hotspot) and put the laptop and the iPad both on
it, with the laptop reaching the internet through the same box.

Test it before the doors open: open `/booth` on the iPad. If it hangs while
`http://localhost:8000/booth` works on the laptop, it is isolation.

---

## 4. What a visitor does

```
   ねている        Idle. Sleeping face, breathing light.
1  頭にさわる      Tap -> it lifts out of the bow and looks up.        (S2)
2  iPadで選ぶ      Two Japanese sentences. Tapping posts the ENGLISH
                   one through the same door speech uses.
3  うなずく        Acknowledge.                                        (S3)
4  部屋を見る      Scan. The five stations appear on the iPad as they
                   are captured; when the plan lands, the boxes are
                   drawn and the chosen one goes red.                  (S4)
5  見張る          Watch. Quiet.                                       (S5)
   頭にさわる      -> Correct. It looks somewhere else, and the red
                   frame on the iPad moves with it.                    (S6)
6  よぶ            Call. Chirp + lean. The same prompt appears on the
                   iPad and on the robot's own screen.                 (S7)
7  OK             Either screen. It nods, and the sentence appears at
                   once; the strip fills in behind it a few seconds
                   later.
```

**The tap means two things and the robot decides which** — asleep it means "look
at me", watching it means "not that one". Both are the same sentence: put your
attention where I am pointing it.

---

## 5. When it does not fire

Both controls are on the laptop's own page (`http://localhost:8000`, LIVE tab,
**IF IT MISSES THE SCENE**):

- **notice this NOW** — skips the trigger and the judge. The robot performs the
  notice and collects the story exactly as if it had fired. Use it when the
  actor plays the scene and nothing happens; the visitor cannot tell.
- **look around again** — a fresh sweep on the same request.

Do not reach for these too early. Give it ten seconds first — a real trigger is
better than a forced one, and the visitor is usually still reading the tablet.

---

## 6. Changing the wording

`webui/booth.py`, the `CHOICES` list at the top. Japanese for the visitor,
English for the planner, and the planner never sees Japanese. Restart the loop.

Adding a third choice works, but two is deliberate: a visitor deciding between
two things reads them, and a visitor deciding between four picks the first one.

---

## 7. End of the day

Ctrl-C once. The teardown flushes any story still collecting, so the last
finding of the day is on disk rather than lost with the process.

`session_feed/<run>/` is the whole day: every report, every sweep, every LLM
call, and `review.html` for the wall.
