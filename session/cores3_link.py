"""
cores3_link.py — laptop-side link to the CoreS3 I/O board (v1).

The CoreS3 is a SEPARATE USB serial device from the R4 rig (the head).
  - Head / servos  -> rig.py talks to the Arduino R4 (unchanged).
  - Screen/sound/touch -> THIS module talks to the CoreS3.
Both run at the same time; the laptop loop is the conductor.

Protocol (line-based, 115200, '
'):
  laptop -> CoreS3 :  EVT UI <screen> | EVT NOTICED <n> | EVT LEVEL <0-100> |
                      EVT LED <0-255> | EVT HUE <WARM|COOL|RED|GREEN|ALARM> |
                      EVT SFX <name> | EVT VOL <0-255> | EVT REST | EVT PING
  CoreS3 -> laptop :  IN PTT_DOWN | IN PTT_UP | IN OK | IN STOP | IN BODYTAP |
                      IN PONG cores3_sidekick v5

The board cannot be found by name -- macOS calls it usbmodem-<location id> just
like the servo adapter -- so find_cores3() asks it instead. See below.

Deps: pyserial  (pip install pyserial)
"""
from __future__ import annotations
import threading, time

try:
    import serial  # pyserial
except ImportError:
    serial = None


# The version robot/firmware/cores3_sidekick/cores3_sidekick.ino currently
# announces. Bump it for ANY change the laptop cannot observe -- a new command,
# a changed colour, a moved button. Not just the protocol.
#
# v3 -> v4 is exactly that lesson: v3 added the SUMMON and SPENT hue names, then
# SPENT's RGB changed from blue to amber WITHIN v3. The board still answered
# "v3", this check still said "match", and the antenna was still blue. A version
# that tracks only the protocol cannot answer the one question it is asked --
# "is the thing in front of me built from the code in front of me".
FIRMWARE_V = "v5"


def find_cores3(exclude=(), timeout=4.0, baud=115200, verbose=True):
    """-> port of the CoreS3, or None.

    Same problem as the servo adapter: macOS names both of them usbmodem-<location
    id>, so the paths move between sessions and cannot be told apart by name.

    Detection is by ASKING: send `EVT PING`, expect `IN PONG cores3_sidekick`.

    OPENING THE PORT DOES RESET THIS BOARD, and the previous version of this
    function asserted the opposite. Measured on the bench: a bare open followed
    by a read returns `IN HELLO cores3_sidekick v5` -- the greeting from setup()
    -- which only happens if the board rebooted. So the old sequence lost every
    time it mattered:

        open (board reboots) -> sleep 150 ms -> reset_input_buffer() ->
        write PING -> read for 1.2 s

    At 150 ms the firmware is still inside M5.begin() and the display init; its
    loop() is not reading yet, so the PING went into the void. reset_input_buffer
    then threw away any greeting that HAD arrived. And 1.2 s is shorter than a
    CoreS3 cold boot, so even the HELLO usually missed the window. Worse, this
    runs right after open_bus() has probed the same port looking for the servo
    adapter -- so by the time we get here the board has already been reset once
    and is mid-boot.

    Now: no input flush, PING repeatedly, and wait long enough for a boot. Either
    `HELLO` or `PONG` identifies the board, which is why the test is just the
    substring `cores3` -- an unsolicited greeting is as good an answer as a
    solicited one.

    Pass the servo port in `exclude` so it is never poked.
    """
    import glob
    if serial is None:
        return None
    cands = [p for p in sorted(glob.glob("/dev/cu.usbmodem*")) if p not in exclude]

    for port in cands:
        try:
            with serial.Serial(port, baud, timeout=0.2) as s:
                # NO reset_input_buffer(): the greeting may already be in flight.
                t0, buf, last_ping = time.time(), b"", 0.0
                while time.time() - t0 < timeout:
                    # Re-ask periodically. The first PING lands while the board is
                    # still booting and is simply lost; a later one is answered.
                    if time.time() - last_ping > 0.4:
                        try:
                            s.write(b"EVT PING\n")
                        except Exception:
                            pass
                        last_ping = time.time()
                    buf += s.read(128)
                    if b"cores3" in buf.lower():
                        if verbose:
                            what = "greeted" if b"hello" in buf.lower() else "answered PING"
                            txt = buf.decode("utf-8", "ignore")
                            ver = ""
                            for tok in txt.replace("\r", " ").split():
                                if tok.startswith("v") and tok[1:].isdigit():
                                    ver = tok
                            print(f"[cores3] {port} {what} ({ver or 'no version'})")
                            # CHECK THE FIRMWARE VERSION HERE, not when a state
                            # finally looks wrong. The board answers PING happily
                            # while running any build, so "connected" says nothing
                            # about whether it understands what it is about to be
                            # sent. A pre-v3 board does not know HUE SUMMON or
                            # SPENT: it falls through the chain, sets no colour,
                            # and keeps the previous one -- so S8 comes up in S1's
                            # colour and the run looks fine until someone notices
                            # that error and idle are the same.
                            if ver and ver != FIRMWARE_V:
                                print(f"[cores3] !! board is {ver}, this checkout "
                                      f"expects {FIRMWARE_V}. Colours and screens "
                                      f"added since {ver} will be IGNORED, "
                                      f"silently. Reflash "
                                      f"robot/firmware/cores3_sidekick/.")
                        return port
        except Exception as e:
            # Say WHY. Swallowing this made three different failures -- port
            # busy, permissions, wrong device -- all present as the single
            # unhelpful "no CoreS3 found".
            if verbose:
                print(f"[cores3] {port} skipped: {type(e).__name__}: {e}")
            continue

    # Fallback for firmware without EVT PING: if exactly one candidate is left
    # once the servo bus is excluded, it can only be the CoreS3. Said out loud,
    # because a guess that works is still a guess.
    if len(cands) == 1:
        if verbose:
            print(f"[cores3] no PING reply; {cands[0]} is the only non-servo "
                  f"usbmodem, using it. Reflash to get EVT PING.")
        return cands[0]
    return None


class CoreS3Link:
    def __init__(self, port: str, baud: int = 115200, on_input=None):
        if serial is None:
            raise RuntimeError("pyserial not installed: pip install pyserial")
        self.ser = serial.Serial(port, baud, timeout=0.1, write_timeout=0.5)
        self.on_input = on_input            # callback(str) for each "IN ..." line
        self._wlock = threading.Lock()      # serialize writes (main thread + reader thread)
        self._stop = False
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()
        time.sleep(0.3)                     # let the board boot/settle

    # ---- receive ----
    def _reader(self):
        buf = b""
        while not self._stop:
            try:
                data = self.ser.read(64)
                if not data:
                    continue
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    s = line.decode("utf-8", "ignore").strip()
                    if s and self.on_input:
                        self.on_input(s)
            except Exception:
                time.sleep(0.2)

    # ---- send ----
    def event(self, cmd: str, arg=""):
        # `arg is not None and arg != ""`, NOT `if arg`. Integer 0 is falsy, so
        # the obvious version silently dropped the argument from every zero:
        # EVT LED 0, EVT LEVEL 0 and EVT VOL 0 (mute) all went out as bare
        # commands. The firmware parses a missing arg as "".toInt() == 0, so
        # they happened to do the right thing -- which is why this survived. Any
        # future command where 0 differs from "absent" would not be so lucky.
        msg = f"EVT {cmd}".strip()
        if arg is not None and arg != "":
            msg += " " + str(arg)
        try:
            with self._wlock:
                self.ser.write((msg + "\n").encode("utf-8"))
        except Exception:
            pass

    # convenience wrappers. ONLY commands the v2 firmware actually implements.
    #
    # The v1 wrappers (armed / watch / prompt / transcript / say / found / look /
    # beep / beep_low / keep / not_this / mode / step / clear) are gone with the
    # firmware code they drove. Leaving them would have been worse than removing
    # them: a method that quietly does nothing is indistinguishable from a method
    # that works, so a call site keeps compiling and the failure only shows up as
    # "the screen didn't change" in the middle of a session.
    def ui(self, screen):         self.event("UI", screen)          # nine screens
    def noticed(self, n):         self.event("NOTICED", int(n))     # laptop owns it
    def level(self, v):           self.event("LEVEL", int(v))       # mic bar, 0-100
    def led(self, level):         self.event("LED", int(level))     # 0-255 from the clip
    def hue(self, kind):          self.event("HUE", str(kind).upper())
    def sfx(self, name):          self.event("SFX", str(name).upper())
    def volume(self, v):          self.event("VOL", int(v))
    def mute(self):               self.volume(0)
    def rest(self):               self.event("REST")   # quiet + back to idle
    def ping(self):               self.event("PING")   # -> IN PONG ...

    def close(self):
        self._stop = True
        try:
            self.ser.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Standalone smoke test:
#   python cores3_link.py /dev/tty.usbmodemXXXX
# Click the CoreS3 buttons -> you'll see IN ... printed; the screen/chirps fire.
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    port = args[0] if args else (find_cores3() or "/dev/tty.usbmodem101")

    # --hues walks the palette at ONE FIXED LEVEL. Every complaint about these
    # colours so far has been confounded by the envelope: each state also has
    # its own brightness, so "these two look the same" could mean the hues are
    # too close, or that one is simply dim. Holding the level constant asks only
    # the colour question. It also proves the board understands the names --
    # a pre-v3 board ignores SUMMON and SPENT and just keeps the previous
    # colour, so the walk visibly stalls on RED instead of continuing.
    if "--hues" in sys.argv:
        seen = []
        link = CoreS3Link(port, on_input=lambda s: (print("[cores3]", s),
                                                    seen.append(s)))
        link.event("PING")
        time.sleep(0.8)
        ver = ""
        for s in seen:
            for tok in s.split():
                if tok.startswith("v") and tok[1:].isdigit():
                    ver = tok
        print(f"\n  firmware: {ver or 'UNKNOWN'}   expected: {FIRMWARE_V}")
        if ver and ver != FIRMWARE_V:
            print(f"  !! STOP HERE AND REFLASH. On {ver}, SUMMON and SPENT are "
                  f"not understood;\n     the antenna keeps whatever colour was "
                  f"set last, so S8 shows S1's colour.")
        print()
        link.event("UI", "idle")
        link.event("LED", 200)                 # one level for all five
        for name, want in [("WARM", "warm white -- idle"),
                           ("COOL", "cyan -- attending"),
                           ("RED", "red -- not that one"),
                           ("SUMMON", "amber/yellow -- a finding"),
                           ("SPENT", "blue -- stuck")]:
            print(f"  HUE {name:7} should look: {want}")
            link.event("HUE", name)
            for _ in range(14):                # LED level must be re-sent: the
                link.event("LED", 200)         # board falls back to its breath
                time.sleep(0.1)                # after 500 ms of silence
        link.event("REST")
        link.close()
        raise SystemExit(0)

    link = CoreS3Link(port, on_input=lambda s: print("[cores3]", s))
    print(f"connected to {port}; running demo…  (Ctrl-C to stop)")
    for cmd, arg in [("PING", ""), ("UI", "idle"), ("HUE", "COOL"),
                     ("UI", "tracking"), ("NOTICED", "1"),
                     ("UI", "noticed"), ("SFX", "EXCITED")]:
        link.event(cmd, arg)
        time.sleep(1.4)
    try:
        while True:           # keep reading button presses
            time.sleep(0.2)
    except KeyboardInterrupt:
        link.close()
