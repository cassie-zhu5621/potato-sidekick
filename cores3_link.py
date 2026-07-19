"""
cores3_link.py — laptop-side link to the CoreS3 I/O board (v1).

The CoreS3 is a SEPARATE USB serial device from the R4 rig (the head).
  - Head / servos  -> rig.py talks to the Arduino R4 (unchanged).
  - Screen/sound/touch -> THIS module talks to the CoreS3.
Both run at the same time; the laptop loop is the conductor.

Protocol (line-based, 115200, '\n'):
  laptop -> CoreS3 :  EVT ARMED | EVT WATCH <text> | EVT PROMPT <text> | EVT TRANSCRIPT <text> |
                      EVT SAY <text> | EVT FOUND | EVT LOOK | EVT BEEP | EVT BEEPLO |
                      EVT KEEP | EVT MODE <EAGER|CALM> | EVT STEP <REST|WATCH|STOP|CONFUSED> | EVT CLEAR
  CoreS3 -> laptop :  IN PTT_DOWN | IN PTT_UP | IN BODYTAP (head-tap = "that's wrong") |
                      IN TAP STOP | IN TAP EAGER | IN TAP CALM | IN HELLO ...

Find the port (mac): `ls /dev/tty.usbmodem*` — there will be two now (R4 + CoreS3);
the CoreS3 prints `IN HELLO cores3_sidekick v1` on boot, so you can tell them apart.

Deps: pyserial  (pip install pyserial)
"""
from __future__ import annotations
import threading, time

try:
    import serial  # pyserial
except ImportError:
    serial = None


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
    def event(self, cmd: str, arg: str = ""):
        msg = f"EVT {cmd}".strip()
        if arg:
            msg += " " + str(arg)
        try:
            with self._wlock:
                self.ser.write((msg + "\n").encode("utf-8"))
        except Exception:
            pass

    # convenience wrappers (call these from the main loop)
    def armed(self):              self.event("ARMED")
    def watch(self, text):        self.event("WATCH", text)        # "Looking for: <text>"
    def prompt(self, text):       self.event("PROMPT", text)       # the brief, shown as-is (short)
    def transcript(self, text):   self.event("TRANSCRIPT", text)
    def say(self, text):          self.event("SAY", text)
    def found(self):              self.event("FOUND")      # "I noticed it" (calm)
    def look(self):               self.event("LOOK")       # "come look!" screen+antenna (beats via beep)
    def beep(self):               self.event("BEEP")       # one rhythm beat (eager tempo)
    def beep_low(self):           self.event("BEEPLO")     # one rhythm beat (calm tempo, lower/softer)
    def mode(self, eager):        self.event("MODE", "EAGER" if eager else "CALM")  # tempo-dance label
    def volume(self, v):          self.event("VOL", int(v))    # 0..255 speaker volume
    def mute(self):               self.volume(0)               # silent run
    def keep(self):               self.event("KEEP")
    def not_this(self):           self.event("NOT")        # legacy webui down-weight (head-tap is the live "wrong")
    def step(self, name):         self.event("STEP", name)         # REST|WATCH|STOP|CONFUSED
    def clear(self):              self.event("CLEAR")

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
