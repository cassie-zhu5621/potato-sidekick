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
                      IN PONG cores3_sidekick v2

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


def find_cores3(exclude=(), timeout=1.2, baud=115200, verbose=True):
    """-> port of the CoreS3, or None.

    Same problem as the servo adapter: macOS names both of them usbmodem-<location
    id>, so the paths move between sessions and cannot be told apart by name.

    Detection is by ASKING: send `EVT PING`, expect `IN PONG cores3_sidekick`.
    An earlier version listened for the boot greeting instead, on the assumption
    that opening the port would reset the board. That is true of a CH340/CP2102
    with an auto-reset circuit, and false here -- the CoreS3 is an ESP32-S3 with
    native USB CDC, which does not reboot when DTR is asserted, so the HELLO from
    setup() never comes again. (Which is what you want mid-session; it just makes
    for a useless detector.)

    Pass the servo port in `exclude` so it is never poked.
    """
    import glob
    if serial is None:
        return None
    cands = [p for p in sorted(glob.glob("/dev/cu.usbmodem*")) if p not in exclude]

    for port in cands:
        try:
            with serial.Serial(port, baud, timeout=0.2) as s:
                time.sleep(0.15)
                s.reset_input_buffer()
                s.write(b"EVT PING\n")
                t0, buf = time.time(), b""
                while time.time() - t0 < timeout:
                    buf += s.read(128)
                    if b"cores3" in buf.lower():
                        if verbose:
                            print(f"[cores3] {port} answered PING")
                        return port
        except Exception:
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
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/tty.usbmodem101"
    link = CoreS3Link(port, on_input=lambda s: print("[cores3]", s))
    print(f"connected to {port}; running demo…  (Ctrl-C to stop)")
    for cmd, arg in [("ARMED", ""), ("WATCH", "blue bottle on the shelf"),
                     ("FOUND", ""), ("KEEP", ""), ("STEP", "STOP")]:
        link.event(cmd, arg)
        time.sleep(1.4)
    try:
        while True:           # keep reading button presses
            time.sleep(0.2)
    except KeyboardInterrupt:
        link.close()
