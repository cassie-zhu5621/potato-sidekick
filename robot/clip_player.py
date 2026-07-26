#!/usr/bin/env python3
"""
Non-blocking clip playback for the 3-DOF prototype.

One background thread owns the bus and plays clips on their own clock; the main
loop asks for state changes and reads status. Playback must not block, because
the conductor also has a camera to drain and a CoreS3 to poll -- and because a
blocking player makes the researcher's override key wait for the clip to end,
which is exactly when you most want it.

Two things this exists to protect:

* AUTHORED TIMING. Inside a clip the CSV's t_ms is honoured to the millisecond
  and late frames are dropped rather than sent late. The ease curves are the
  design contribution; re-timing them throws it away.
* SETTLED-ONLY VISION. The camera rides on the head, so a frame taken mid-move
  is motion-blurred and points somewhere that no longer matches the pose it will
  be attributed to. `settled_ms` reports how long the commanded pose has been
  unchanged, so the conductor can sample only while still. This falls out for
  free during S4's per-station dwells.

Standalone:
  export NOTICEBOT_PORT=/dev/cu.usbmodemXXXX
  python3 clip_player.py                 # walk the designed cycle
  python3 clip_player.py S7a             # one state, then hold
"""
import csv, os, sys, threading, time

# Run me directly: put the repo ROOT on the path so the packages import.
# (Kept as a two-line preamble rather than a helper module, because a
# bootstrap that itself needs importing defeats the purpose.)
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
from robot.scs import Bus, open_bus
from robot.pose import (JOINTS, IDS, CENTER, UNITS_PER_DEG, resolve, centre_units,
                  unit_to_deg,
                  move_ms)
from robot import states as ST

DEFAULT_CLIPS = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "motion", "clips")
STREAM_HZ = 60          # command scheduler tick; clips are 30 fps
ARRIVE_TOL = 8          # units. Bigger than settling error, smaller than a jam.
# Flash detection is a Schmitt trigger, not a rising-edge difference. A flash
# takes several frames to climb (S4 goes 32 -> 96 -> 191 -> 223), so a plain
# "rose by more than X" test fires on each of those steps -- the shutter clicked
# twice per station. Crossing HIGH fires once; it will not fire again until the
# level has fallen back under LOW.
LED_FLASH_HIGH = 150   # of 255. S4's flash peaks at 223.
LED_FLASH_LOW = 90     # must fall below this to re-arm; S4 rests at 32.
LED_HEARTBEAT_S = 0.30  # must stay well under the firmware's 500 ms fallback,
                        # or a slow-changing stretch of an authored envelope
                        # silently hands the LED back to the local one


def load_clip(path):
    """-> list of {t, pan, tilt, nod} in COMMANDED units. Clamping is a bug in
    the clip, so it is reported once here rather than swallowed per frame."""
    with open(path) as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"{path}: empty")
    has_nod = "nod_unit" in rows[0]
    has_led = "led" in rows[0]
    if has_led:
        # A CONSTANT led column means nobody authored an envelope for this clip
        # (S1_IDLE and S8_ERROR have no generator, so they export the material's
        # static Strength). Streaming a constant would be worse than streaming
        # nothing: the firmware only falls back to its own breathing after 500 ms
        # of silence, so a steady value pins the LED solid forever -- exactly the
        # states where "alive" matters most would be the ones that look dead.
        vals = {int(float(r["led"])) for r in rows}
        if len(vals) <= 1:
            has_led = False
    frames, clamped = [], set()
    for r in rows:
        f = {"t": float(r["t_ms"]) / 1000.0,
             "led": int(float(r["led"])) if has_led else None}
        for j in JOINTS:
            raw = r[f"{j}_unit"] if (j != "nod" or has_nod) else CENTER
            u, was = resolve(j, raw)
            f[j] = u
            if was:
                clamped.add(j)
        frames.append(f)
    return frames, sorted(clamped), has_nod, has_led


class ClipPlayer:
    def __init__(self, bus, clips_dir=DEFAULT_CLIPS, verbose=True, on_led=None,
                 on_sfx=None):
        self.bus = bus
        self.verbose = verbose
        # Called with 0-255 when the clip's authored LED level changes. Kept as a
        # callback so the player stays a servo-only component: the LED lives on a
        # different device, on a different serial port, and a dropped LED update
        # must never be able to disturb the motion timing.
        self.on_led = on_led
        # Fired with a sound name. Entry sounds come from states.py; the S4
        # shutter is derived from the clip's own LED rising edge, because the
        # click and the flash are one event and anything that computes them
        # separately can drift.
        self.on_sfx = on_sfx
        self._last_led = None
        self._last_led_at = 0.0
        self._in_flash = False
        self._flash_sfx = None
        self._pending_sfx = None
        self._pending_at = 0.0
        self._pan_override = None
        self._pan_pending = None      # armed now, applied when S5_TRACK begins
        self.clips = {}
        for fn in sorted(os.listdir(clips_dir)):
            if fn.endswith(".csv"):
                name = fn[:-4]
                fr, clamped, has_nod, has_led = load_clip(
                    os.path.join(clips_dir, fn))
                self.clips[name] = fr
                if clamped and verbose:
                    print(f"[player] !! {name}: {clamped} would be clamped -- "
                          f"re-author, do not ship this")
                if not has_nod and verbose:
                    print(f"[player] {name}: no nod channel, held at centre")
                if not has_led and verbose:
                    print(f"[player] {name}: no authored LED envelope -- the "
                          f"CoreS3 runs its own for this state")

        problems = ST.validate(set(self.clips))
        if problems:
            raise RuntimeError("state table does not match the clips:\n  "
                               + "\n  ".join(problems))

        self.state = None
        self._want = None            # state requested by the conductor
        self._stop = False
        self._lock = threading.Lock()
        self.cur = dict(centre_units())      # last COMMANDED pose
        self._pose_changed_at = time.perf_counter()
        self.loops_done = 0
        self.lag_ms = 0.0
        self.dropped = 0
        self.error = None
        self._thread = None

    # ---------------- public API, called from the main loop ----------------
    def set_pan_deg(self, deg):
        """Re-aim the watching pose, in Blender degrees.

        Only meaningful for a STATIC clip, which is exactly why S5_TRACK is one:
        a hold has no authored timing to damage, so the pan can simply be
        replaced. S4 could not be retargeted this way -- its return is an eased
        curve authored for one specific distance, and stretching it would rewrite
        the very thing the clip exists to carry.

        The tap that led here said "wrong direction", not "wrong task", so the
        watch-spec is untouched.
        """
        unit = resolve("pan", max(0, min(1023, round((deg + 150.0) / 300.0 * 1023))))[0]
        with self._lock:
            self._pan_override = unit
        if self.verbose:
            print(f"[player] re-aim: pan {deg:+.0f} deg -> unit {unit}")

    def arm_pan_deg(self, deg):
        """Where S5 should hold, decided BEFORE S5 starts.

        set_pan_deg() cannot be used for this: the player applies an override the
        moment it is set, so calling it during S4 would hijack the sweep mid-turn.
        And calling it after S4 ends is too late -- the transition into S5 has
        already moved the head to the clip's authored HOLD_PAN, so the head visibly
        swings to 25 deg and then swings back. Arming stores the angle; the
        transition consumes it, and the head simply stays where the sweep left it.
        """
        self._pan_pending = resolve(
            "pan", max(0, min(1023, round((deg + 150.0) / 300.0 * 1023))))[0]

    def request(self, state):
        """Ask for a state. Takes effect within a frame; interrupts a clip."""
        if state not in ST.STATES:
            raise KeyError(state)
        with self._lock:
            self._want = state

    @property
    def settled_ms(self):
        """How long the commanded pose has been unchanged. The camera should
        only be trusted above ~150 ms of this."""
        return (time.perf_counter() - self._pose_changed_at) * 1000.0

    def snapshot(self):
        with self._lock:
            return dict(state=self.state, want=self._want, pose=dict(self.cur),
                        pose_deg={n: unit_to_deg(n, u) for n, u in self.cur.items()},
                        settled_ms=self.settled_ms, loops=self.loops_done,
                        lag_ms=self.lag_ms, dropped=self.dropped,
                        alive=self.alive, error=self.error)

    def start(self, home=True):
        if home:
            self._goto(centre_units(), label="home")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self, relax=True):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=3)
        if relax:
            for sid in IDS.values():
                try:
                    self.bus.torque(sid, False)
                except Exception:
                    pass

    # ---------------- internals ----------------
    def _send(self, units, timed_ms=0):
        changed = any(units[j] != self.cur[j] for j in JOINTS)
        for j in JOINTS:
            if timed_ms:
                self.bus.write_pos(IDS[j], units[j], time_ms=timed_ms, speed=0)
            else:
                self.bus.write_pos_fast(IDS[j], units[j], time_ms=0, speed=0)
        self.cur.update(units)
        if changed:
            self._pose_changed_at = time.perf_counter()

    def _goto(self, units, label=""):
        """A move nobody authored: transition, pre-roll, homing. Duration scales
        with distance so a long hop is not a lurch."""
        self.bus.flush_input()      # arrive with a clean buffer, whatever ran before
        for sid in IDS.values():
            self.bus.torque(sid, True)
        ms, far = move_ms(self.cur, units)
        if self.verbose and far > 20:
            print(f"[player] {label or 'move'}: {far} units "
                  f"({far / UNITS_PER_DEG:.0f} deg) over {ms} ms")
        self._send(units, timed_ms=ms)
        time.sleep(ms / 1000.0 + 0.15)
        self.bus.flush_input()
        bad = []
        for j in JOINTS:
            p = self.bus.read_pos(IDS[j])
            if p is None or abs(p - units[j]) > ARRIVE_TOL:
                bad.append(f"{j}={p}/{units[j]}")
        if bad and self.verbose:
            print(f"[player] !! did not arrive: {', '.join(bad)} -- torque off, "
                  f"jammed, or over-loaded at this voltage")
        return not bad

    def _play_once(self, frames):
        """Stream one pass of a clip on its own clock. Returns False if a state
        change was requested mid-clip."""
        t0 = time.perf_counter()
        late, dropped, i = 0.0, 0, 0
        for f in frames:
            with self._lock:
                if self._want or self._stop:
                    self.lag_ms, self.dropped = late * 1000, dropped
                    self.bus.flush_input()      # see DRAIN below -- the early
                    return False                # exit needs it just as much
            target = t0 + f["t"]
            now = time.perf_counter()
            if now > target + 0.020:
                dropped += 1              # skip, never send late: sending late
                continue                  # accumulates and stretches the clip
            while now < target:
                rem = target - now
                if rem > 0.002:
                    time.sleep(rem - 0.001)
                now = time.perf_counter()
            late = max(late, now - target)
            units = {j: f[j] for j in JOINTS}
            if self.state == "S5_TRACK" and self._pan_override is not None:
                units["pan"] = self._pan_override
            self._send(units)

            if (self._pending_sfx and self.on_sfx
                    and f["t"] >= self._pending_at):
                try:
                    self.on_sfx(self._pending_sfx)
                except Exception:
                    pass
                self._pending_sfx = None
            # LED after the servos, and only on a real change: at 30 fps a
            # per-frame send would be ~30 msgs/s of mostly identical values, and
            # the quantisation keeps a slow breath from emitting a message per
            # frame. Servos first so a busy LED link cannot delay the motion.
            if self.on_led is not None and f["led"] is not None:
                lv = f["led"]
                # Send on a real change, OR on a heartbeat. The heartbeat is
                # unconditional -- an earlier version only resent when the value
                # differed, which is exactly wrong: the flat top and bottom of a
                # slow breath are where the value stops changing, so those are
                # where the last send would age past the firmware's 500 ms
                # timeout and hand the LED back to its own envelope mid-state.
                # It looked like the light drifting out of sync with the motion.
                if (self._last_led is None
                        or abs(lv - self._last_led) >= 4
                        or now - self._last_led_at > LED_HEARTBEAT_S):
                    self._last_led, self._last_led_at = lv, now
                    try:
                        self.on_led(lv)
                    except Exception:
                        pass          # the LED is never worth a motion glitch

            # A capture is one event with two channels. Detecting the flash and
            # firing the click off the SAME rising edge is what guarantees they
            # land on the same frame; a separately-authored cue track would be
            # one more thing to keep in sync by hand.
            if self._flash_sfx and self.on_sfx and f["led"] is not None:
                lv = f["led"]
                if lv >= LED_FLASH_HIGH and not self._in_flash:
                    self._in_flash = True
                    try:
                        self.on_sfx(self._flash_sfx)
                    except Exception:
                        pass
                elif lv <= LED_FLASH_LOW:
                    self._in_flash = False
            i += 1
            # DRAIN. write_pos_fast is fire-and-forget, but the servos still send
            # a status packet for every write -- 90 a second, three joints at
            # 30 fps. Nobody reads them, so they accumulate in the OS buffer and
            # eventually a later read starts mid-packet. Draining as we go keeps
            # the buffer from ever growing, which is cheaper and more reliable
            # than clearing it afterwards and hoping nothing arrived in between.
            if i % 30 == 0:
                self.bus.flush_input()
        self.bus.flush_input()
        self.lag_ms, self.dropped = late * 1000, dropped
        return True

    @property
    def alive(self):
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        try:
            self._run_inner()
        except Exception as e:
            # Record it. A player thread that dies silently leaves the main loop
            # happily reporting the last known lag forever, which is how the
            # IndexError in the vendor SDK stayed invisible for a whole run.
            import traceback
            self.error = f"{type(e).__name__}: {e}"
            traceback.print_exc()

    def _run_inner(self):
        while not self._stop:
            with self._lock:
                nxt, self._want = self._want, None
            if nxt is None:
                time.sleep(0.02)
                continue

            spec = ST.STATES[nxt]
            frames = self.clips[spec["clip"]]
            first = {j: frames[0][j] for j in JOINTS}
            # A re-aim only applies to the clip it was aimed at; entering anything
            # else clears it, so an old override cannot silently steer a later state.
            if nxt != "S5_TRACK":
                self._pan_override = None
            else:
                if self._pan_pending is not None:
                    self._pan_override, self._pan_pending = self._pan_pending, None
                if self._pan_override is not None:
                    first["pan"] = self._pan_override
            self._goto(first, label=f"-> {nxt}")
            self.state, self.loops_done = nxt, 0
            self._flash_sfx = spec.get("sfx_flash")
            self._in_flash = False
            # Scheduled inside the clip rather than fired here, unless sfx_at is
            # 0. A sound that arrives before the accent it belongs to reads as a
            # separate event instead of emphasis -- which is what made S2 and S3
            # sound like two unrelated noises rather than one exchange.
            self._pending_sfx = spec.get("sfx")
            self._pending_at = spec.get("sfx_at", 0.0) * frames[-1]["t"]
            if self.on_sfx and self._pending_sfx and self._pending_at <= 0.0:
                try:
                    self.on_sfx(self._pending_sfx)
                except Exception:
                    pass
                self._pending_sfx = None
            if self.verbose:
                print(f"[player] {nxt}: {spec['note']}")

            while not self._stop:
                completed = self._play_once(frames)
                if not completed:
                    break                       # interrupted by a request
                self.loops_done += 1
                if spec["loop"]:
                    if spec.get("sfx") and spec.get("sfx_loop"):
                        if self._pending_at <= 0.0:
                            try:
                                self.on_sfx and self.on_sfx(spec["sfx"])
                            except Exception:
                                pass
                        else:
                            self._pending_sfx = spec["sfx"]   # re-arm for next pass
                    continue
                if spec["then"]:
                    with self._lock:
                        if not self._want:
                            self._want = spec["then"]
                break
            # A one-shot with no `then` simply holds its last pose: fall back to
            # the outer loop and wait for the next request.


def _main():
    import argparse
    ap = argparse.ArgumentParser(description="Stage 1: motion, and the LED "
                                             "envelope that is authored with it")
    ap.add_argument("state", nargs="?", help="one state, else the whole CYCLE")
    ap.add_argument("--all", action="store_true",
                    help="walk every state, not just the designed cycle. S8_ERROR "
                         "is deliberately outside CYCLE -- it is not a step in the "
                         "communication cycle, it is what happens when the cycle "
                         "cannot continue -- but it still needs testing.")
    ap.add_argument("--clips", default=DEFAULT_CLIPS)
    ap.add_argument("--cores3", nargs="?", const="auto",
                    help="attach the CoreS3 so the clip's LED envelope is "
                         "audible/visible. Bare --cores3 auto-detects it; the "
                         "LED level is part of the clip, so it belongs in this "
                         "stage and needs no camera.")
    ap.add_argument("--led-test", action="store_true",
                    help="with --cores3: step the LED 0/255 a few times and exit. "
                         "Isolates 'firmware not reflashed' from everything else, "
                         "and touches no servos.")
    a = ap.parse_args()

    # LED-only test first: it needs no bus, so it cannot be confused by anything
    # on the servo side.
    if a.led_test:
        from session.cores3_link import CoreS3Link, find_cores3
        c3 = a.cores3 if (a.cores3 and a.cores3 != "auto") else find_cores3()
        if not c3:
            sys.exit("no CoreS3 found -- pass --cores3 /dev/cu.usbmodemXXXX")
        link = CoreS3Link(c3, on_input=lambda s: print(f"[cores3] {s}"))
        print("EVT HUE COOL, then LED 0/255 five times, 0.6s apart.")
        print("  steps between dark and full  -> firmware has EVT LED")
        print("  only the old breathing       -> NOT reflashed yet")
        link.hue("COOL")
        for v in (0, 255, 0, 255, 0):
            print(f"  EVT LED {v}")
            link.led(v)
            time.sleep(0.6)
        link.close()
        return

    try:
        bus, port = open_bus()          # probes; NOTICEBOT_PORT is only a hint
    except IOError as e:
        sys.exit(str(e))
    missing = [n for n, sid in IDS.items() if not bus.ping(sid)[1]]
    if missing:
        sys.exit(f"no reply from {missing} on {port}")

    link, on_led = None, None
    if a.cores3:
        from session.cores3_link import CoreS3Link, find_cores3
        c3 = a.cores3
        if c3 == "auto":
            c3 = find_cores3(exclude=(port,))
            if not c3:
                sys.exit("no CoreS3 found (it greets with 'cores3_sidekick' on "
                         "boot; is it flashed and plugged in?)")
        link = CoreS3Link(c3, on_input=lambda s: print(f"[cores3] {s}"))
        n_led = [0]

        def on_led(v):
            n_led[0] += 1
            link.led(v)

        n_sfx = [0]

        def on_sfx(name):
            n_sfx[0] += 1
            link.sfx(name)
        _led_count, _sfx_count = n_led, n_sfx

    p = ClipPlayer(bus, a.clips, on_led=on_led,
                   on_sfx=on_sfx if a.cores3 else None)
    have_led = sum(1 for fr in p.clips.values() if fr[0]["led"] is not None)
    print(f"[stage1] {have_led}/{len(p.clips)} clips carry an led envelope")
    if a.cores3 and have_led == 0:
        print("[stage1] !! nothing to stream -- re-export from Blender first, "
              "the CoreS3 will just run its own envelope")
    if not a.cores3:
        print("[stage1] no --cores3: the LED is NOT connected in this run, so it "
              "cannot respond\n"
              "         even with the envelopes present. Add "
              "--cores3 /dev/cu.usbmodemXXXX to see it.")
    if a.state:
        seq = [a.state]
    elif a.all:
        seq = ST.CYCLE + [s for s in ST.STATES if s not in ST.CYCLE]
    else:
        seq = ST.CYCLE
    worst_lag, total_dropped = 0.0, 0
    try:
        p.start()
        for s in seq:
            p.request(s)
            spec = ST.STATES[s]
            if link:
                link.hue(spec["hue"])       # colour = state, level = clip
                link.step({"S1_IDLE": "REST", "S8_ERROR": "CONFUSED"}
                          .get(s, "WATCH"))
            # Wait for it to actually BE the state, then for the exit condition:
            # a loop state has no natural end, so give it two full passes; a
            # one-shot is done when the player has moved on or is holding.
            arrived, deadline = False, time.time() + 40
            while time.time() < deadline:
                snap = p.snapshot()
                if snap["error"]:
                    raise SystemExit(f"player thread died: {snap['error']}")
                if not snap["alive"]:
                    raise SystemExit("player thread is gone")
                if snap["state"] == s:
                    arrived = True
                    if spec["loop"] and snap["loops"] >= 2:
                        break
                elif arrived:
                    break                       # chained onward, or overridden
                time.sleep(0.05)
            snap = p.snapshot()
            worst_lag = max(worst_lag, snap["lag_ms"])
            total_dropped += snap["dropped"]
            print(f"    {s:<12} lag {snap['lag_ms']:5.1f} ms  "
                  f"dropped {snap['dropped']}"
                  + ("" if arrived else "   <-- never became active"))
        print(f"\nStage 1 result: worst lag {worst_lag:.1f} ms, "
              f"{total_dropped} dropped in total")
        print("  pass = 0 dropped and worst lag under 20 ms")
        if link:
            print(f"  sfx fired: {_sfx_count[0]}")
            print(f"  led updates sent: {_led_count[0]}"
                  + ("  <-- zero means the clips have no led column"
                     if _led_count[0] == 0 else ""))
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        p.stop()
        if link:
            # A run that ends on S8 would otherwise leave the amber alarm
            # flashing forever: the hue lock and the streamed level both outlive
            # the laptop going quiet. EVT REST hands colour back to the local
            # warm breathe and stops any sound mid-sequence.
            link.rest()
            time.sleep(0.3)
            link.close()
        bus.close()


if __name__ == "__main__":
    _main()
