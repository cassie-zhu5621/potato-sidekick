#!/usr/bin/env python3
"""
STEP 2: jog each joint by hand, find its real limits and true centre.

Never jumps. Every key press is a small, timed move from where the joint
already is, so you can stop the moment something looks wrong. Use this instead
of --center on a fresh build: the servo's 512 is not necessarily your model's
neutral pose, and driving into a printed hard stop strips gears in seconds.

  export NOTICEBOT_PORT=/dev/cu.usbmodemXXXX
  python3 jog.py

Keys
  1 2 3    select joint (pan / tilt / nod)
  a / d    -10 / +10 units   (~3 deg)
  A / D    -40 / +40 units
  w / s    +2 / -2 units     (fine)
  [ / ]    record this position as the joint's MIN / MAX limit
  c        record this position as the joint's CENTRE
  r / t    torque OFF / ON for the SELECTED joint
           (t adopts the joint's actual position as its goal first, so it
            holds where you put it instead of snapping back)
  R / T    torque OFF / ON for all three at once -- the head will flop on R
  p        print the LIMITS / OFFSET block for play_on_hardware.py
  q        quit (torque off)

Work joint by joint: r, hand-move it to neutral, t, c. Then jog to each end
stop, backing off a few units before you press [ or ] so the limit you save is
inside the mechanical stop, not on it.
"""
import os, sys, termios, tty, time

# Run me directly: the repo root is two levels up from robot/tools/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from robot.scs import Bus, open_bus

IDS = {"pan": 1, "tilt": 2, "nod": 3}
ORDER = ["pan", "tilt", "nod"]
UNITS_PER_DEG = 1023 / 300.0
STEP_TIME_MS = 220          # every jog is a timed move -- never full speed
END_MARGIN = 60             # treat this close to 0 or 1023 as "against the stop"


def deg(u):
    return (u - 512) / UNITS_PER_DEG


class Jog:
    def __init__(self, bus):
        self.bus = bus
        self.sel = "pan"
        self.target = {}
        self.seen = {}
        self.limits = {}
        self.centre = {}
        # Anything already on disk. Kept separate from what gets measured this
        # session so that saving after calibrating one joint cannot wipe the
        # other two.
        self.prev_centre, self.prev_limits = {}, {}
        self.prev_uncal = set()
        try:
            from robot import calibration as _c
            self.prev_centre = dict(getattr(_c, "CENTRE", {}))
            self.prev_limits = {k: tuple(v) for k, v in getattr(_c, "LIMITS", {}).items()}
            # Carried over, not recomputed: a joint listed here has placeholder
            # numbers in the file. Without this the placeholders look exactly
            # like real measurements on the next save, and the guard silently
            # unlocks itself.
            self.prev_uncal = set(getattr(_c, "UNCALIBRATED", set()))
            done = set(ORDER) - self.prev_uncal
            if done:
                print(f"loaded existing calibration for: {', '.join(sorted(done))}")
        except Exception:
            pass
        for n, sid in IDS.items():
            p = bus.read_pos(sid)
            if p is None:
                sys.exit(f"{n} (id {sid}) not responding -- run check_bus.py")
            self.target[n] = p
            self.seen[n] = [p, p]

    def move(self, delta):
        n = self.sel
        u = max(0, min(1023, self.target[n] + delta))
        self.target[n] = u
        self.bus.write_pos(IDS[n], u, time_ms=STEP_TIME_MS, speed=0)
        self.seen[n][0] = min(self.seen[n][0], u)
        self.seen[n][1] = max(self.seen[n][1], u)

    def hold_here(self, n):
        """Adopt the joint's ACTUAL position as its goal, then hold it.

        Enabling torque alone is not enough: the servo resumes seeking the goal
        still sitting in its register, which is wherever it was before you
        hand-moved it -- so it snaps back. The goal has to be overwritten with
        the present position FIRST, while torque is still off.
        """
        sid = IDS[n]
        # Median of several reads. Capture-then-hold is a feedback loop: a single
        # bad sample becomes the new goal, the joint moves there, and the next
        # capture starts from the moved position. Repeat and it walks itself into
        # an end stop. The median stops one stray sample from starting that.
        self.bus.flush_input()
        reads = [self.bus.read_pos(sid) for _ in range(5)]
        reads = sorted(r for r in reads if r is not None)
        if not reads:
            return None, None      # callers unpack (before, after)
        p = reads[len(reads) // 2]
        if p < END_MARGIN or p > 1023 - END_MARGIN:
            print(f"\n  !! {n} is at {p}, within {END_MARGIN} units of the hard stop."
                  f"\n     Not holding here -- jog it back toward the middle first.")
            return p, p            # refuse to command a pose against the end stop
        self.target[n] = p
        self.bus.write_pos(sid, p, time_ms=0, speed=0)   # goal := where it is
        self.bus.torque(sid, True)
        # Report where it actually ended up: if the joint sagged between the
        # read and the torque coming on, the servo drives it back and the two
        # numbers differ. That gap is the whole reason hand-capture is fiddly.
        time.sleep(0.4)
        self.bus.flush_input()
        after = self.bus.read_pos(sid)
        return p, after

    def status(self):
        self.bus.flush_input()
        bits = []
        for n in ORDER:
            p = self.bus.read_pos(IDS[n])
            ld = self.bus.read_load(IDS[n])
            # load is sign+magnitude: bit 10 = direction, low 10 bits = effort
            ld = None if ld is None else (ld & 0x3FF)
            mark = ">" if n == self.sel else " "
            lo, hi = self.limits.get(n, (None, None))
            if lo is not None and hi is not None:
                lo, hi = sorted((lo, hi))
                lim = f"[{lo},{hi}]"
            else:
                lim = "[--,--]"
            ctr = self.centre.get(n)
            warn = ""
            if p is not None and (p < END_MARGIN or p > 1023 - END_MARGIN):
                warn = " !STOP"      # against the servo's mechanical end of travel
            bits.append(f"{mark}{n} {str(p):>4} ({deg(p or 512):+6.1f}d) L{str(ld):>4} {lim}"
                        f"{' C' + str(ctr) if ctr else ''}{warn}")
        return "  ".join(bits)

    def save(self, path=None):
        """Write calibration.py next to the scripts. play_on_hardware.py imports
        it automatically, so nothing has to be copied by hand."""
        path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "calibration.py")
        lines = ['"""Written by jog.py. Safe to hand-edit -- this is config, not a',
                 'build artifact (unlike the clip CSVs, which get overwritten by the',
                 'next Blender export).',
                 '',
                 'LIMITS are a guard rail, not a record of how far the joint can go:',
                 'set them wider than the motion needs and well short of anything that',
                 'can jam.',
                 '"""']
        # measured this session wins; otherwise keep whatever was already on disk
        centre = {n: self.centre.get(n, self.prev_centre.get(n)) for n in ORDER}
        limits = {n: self.limits.get(n, self.prev_limits.get(n)) for n in ORDER}
        measured = set(self.centre) | set(self.limits)      # touched this session
        unknown = sorted(n for n in ORDER
                         if centre[n] is None or limits[n] is None
                         or (n in self.prev_uncal and n not in measured))

        lines.append("UNCALIBRATED = " + (repr(set(unknown)) if unknown else "set()"))
        lines.append("")
        lines.append("CENTRE = {")
        for n in ORDER:
            lines.append(f'    "{n}": {centre[n] if centre[n] is not None else 512},'
                         + ("   # NOT MEASURED" if centre[n] is None else ""))
        lines.append("}")
        lines.append("LIMITS = {")
        for n in ORDER:
            lo, hi = sorted(limits[n]) if limits[n] else (462, 562)
            lines.append(f'    "{n}": ({lo}, {hi}),'
                         + ("   # NOT MEASURED" if limits[n] is None else ""))
        lines.append("}")
        lines.append("OFFSET = {")
        for n in ORDER:
            lines.append(f'    "{n}": {(centre[n] if centre[n] is not None else 512) - 512},')
        lines.append("}")
        lines.append("INVERT = {")
        for n in ORDER:
            lines.append(f'    "{n}": False,   # flip after comparing with the render')
        lines.append("}")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def block(self):
        out = ["", "# also written to calibration.py", "LIMITS = {"]
        for n in ORDER:
            # Sorted, not as-pressed: which key walks a joint "up" depends on how
            # the horn was mounted, so [ and ] can land either way round. An
            # unsorted (lo, hi) makes clamp() collapse to a constant and the joint
            # silently freezes at one position.
            lo, hi = sorted(self.limits.get(n, self.seen[n]))
            out.append(f'    "{n}": ({lo}, {hi}),')
        out.append("}")
        out.append("OFFSET = {")
        for n in ORDER:
            out.append(f'    "{n}": {self.centre.get(n, 512) - 512},')
        out.append("}")
        out.append("# OFFSET shifts the clip's 512 onto YOUR neutral pose.")
        out.append("# If a joint runs opposite to the Blender render, set")
        out.append("# INVERT[joint]=True. That does not invalidate OFFSET: the clip's")
        out.append("# centre 512 mirrors to 511, so the trim only shifts by one unit.")
        out.append("# Which direction a jog key moves the joint does NOT tell you")
        out.append("# whether to invert -- only comparing against the render does.")
        return "\n".join(out)


def getkey():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def getkey_timeout(timeout):
    """A key if one is waiting, else None. Lets a display keep refreshing."""
    import select
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        if select.select([sys.stdin], [], [], timeout)[0]:
            return sys.stdin.read(1)
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main():
    try:
        bus, port = open_bus()                   # probes; the name is not stable
    except IOError as e:
        sys.exit(str(e))
    j = Jog(bus)
    print(__doc__)
    print("starting positions:", j.status())
    print("nothing has moved yet. press a key.\n")

    try:
        while True:
            k = getkey()
            if k in ("q", "\x03"):
                break
            elif k in "123":
                j.sel = ORDER[int(k) - 1]
            elif k == "a": j.move(-10)
            elif k == "d": j.move(+10)
            elif k == "A": j.move(-40)
            elif k == "D": j.move(+40)
            elif k == "w": j.move(+2)
            elif k == "s": j.move(-2)
            elif k == "r":
                n = j.sel
                bus.torque(IDS[n], False)
                print(f"\n{n} torque OFF. Live readout below -- move it by hand and"
                      f"\nwatch the number. Press t to hold, or any other key to stop.\n")
                # live position while relaxed, so you can read neutral off the
                # screen instead of trying to capture it at the moment of a keypress
                k2 = None
                while k2 is None:
                    p = bus.read_pos(IDS[n])
                    print(f"\r  {n} {str(p):>4}  ({deg(p or 512):+6.1f} deg)   ",
                          end="", flush=True)
                    k2 = getkey_timeout(0.2)
                print()
                if k2 == "t":
                    before, after = j.hold_here(n)
                    print(f"{n} torque ON: captured {before}, settled at {after}"
                          f" ({deg(after or 512):+.1f} deg)")
                    if before is not None and after is not None and abs(after - before) > 6:
                        print(f"  ! moved {after - before} units after torque came on --"
                              f" the joint sagged before it was captured.")
                        print(f"  Easier: leave torque ON and jog with a/d/A/D instead.")
                continue
            elif k == "t":
                before, after = j.hold_here(j.sel)
                print(f"\n{j.sel} torque ON: captured {before}, settled at {after}"
                      f" ({deg(after or 512):+.1f} deg)")
                continue
            elif k == "R":
                for sid in IDS.values():
                    bus.torque(sid, False)
                print("\nALL torque OFF -- hold the head, it will flop")
                continue
            elif k == "T":
                for n in ORDER:
                    j.hold_here(n)
                print("\nALL torque ON, holding where they are")
                continue
            elif k == "[":
                n = j.sel
                lo, hi = j.limits.get(n, (None, None))
                j.limits[n] = (j.target[n], hi if hi is not None else j.target[n])
                print(f"\n{n} MIN = {j.target[n]}")
                continue
            elif k == "]":
                n = j.sel
                lo, hi = j.limits.get(n, (None, None))
                j.limits[n] = (lo if lo is not None else j.target[n], j.target[n])
                print(f"\n{n} MAX = {j.target[n]}")
                continue
            elif k == "c":
                j.centre[j.sel] = j.target[j.sel]
                print(f"\n{j.sel} CENTRE = {j.target[j.sel]}")
                continue
            elif k == "p":
                print(j.block())
                print(f"\nsaved -> {j.save()}")
                continue
            else:
                continue
            time.sleep(STEP_TIME_MS / 1000.0)
            print("\r" + j.status() + "   ", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n" + j.block())
        try:
            print(f"\nsaved -> {j.save()}")
        except Exception as e:
            print(f"could not write calibration.py: {e}")
        for sid in IDS.values():
            try:
                bus.torque(sid, False)
            except Exception:
                pass
        bus.close()
        print("\ntorque off, port closed.")


if __name__ == "__main__":
    main()
