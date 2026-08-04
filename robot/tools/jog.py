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

from robot import IDS, ORDER  # single source of truth: robot/__init__.py
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
        self.prev_invert = {}
        self.prev_floors = {}
        self.prev_uncal = set()
        try:
            from robot import calibration as _c
            self.prev_centre = dict(getattr(_c, "CENTRE", {}))
            self.prev_limits = {k: tuple(v) for k, v in getattr(_c, "LIMITS", {}).items()}
            # INVERT is NOT measurable here. Which way a jog key moves a joint says
            # nothing about whether a clip plays mirrored -- only comparing against
            # the render does. Earlier versions of save() rewrote all three to False,
            # silently discarding findings that cost a hardware session to establish.
            self.prev_invert = dict(getattr(_c, "INVERT", {}))
            # Measured by deadband_probe, not by jogging. Carried over for the
            # same reason as INVERT: regenerating this file must not silently
            # discard a measurement that cost a hardware session.
            self.prev_floors = dict(getattr(_c, "FLOORS", {}))
            if any(self.prev_invert.values()):
                flipped = ", ".join(sorted(k for k, v in self.prev_invert.items() if v))
                print(f"carrying over INVERT=True for: {flipped}")
                print("  -> if a horn was re-mounted, these are STALE. Re-confirm by")
                print("     playing a clip against the render before trusting it.")
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
        """Jog, but never into the servo's electrical end.

        The end stop is not a soft limit: on a fresh build, before the horn or
        the loom restricts anything, the servo will happily drive into its own
        potentiometer stop and grind. END_MARGIN keeps ~17 deg of air at each
        end, which is far more than any joint on this robot needs. If a real
        mechanical limit sits beyond the rail, find it with torque OFF and the
        joint moved by hand, not by driving into it.
        """
        n = self.sel
        want = self.target[n] + delta
        u = max(END_MARGIN, min(1023 - END_MARGIN, want))
        if u != want:
            print(f"\n  !! {n}: refusing to jog to {want} -- within {END_MARGIN} units"
                  f" of the electrical end. Held at {u}."
                  f"\n     If neutral is this close to an end, the horn is mis-mounted:"
                  f"\n     re-seat it with the servo commanded to 512.")
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

    def _lim(self, n):
        """This session's limits for n, or None if they are not usable yet.

        A half-pressed pair is NOT a limit. The old code stored `[` as
        (target, target), so one keypress produced a range of zero width; clamp()
        then froze the joint on that single unit for every frame of every clip,
        without erroring. That is how tilt ended up pinned at 513 and nod at 534.
        Both ends have to be recorded, and they have to differ.
        """
        pair = self.limits.get(n)
        if not pair:
            return None
        lo, hi = pair
        if lo is None or hi is None or lo == hi:
            return None
        return tuple(sorted((lo, hi)))

    def status(self):
        self.bus.flush_input()
        bits = []
        for n in ORDER:
            p = self.bus.read_pos(IDS[n])
            ld = self.bus.read_load(IDS[n])
            # load is sign+magnitude: bit 10 = direction, low 10 bits = effort
            ld = None if ld is None else (ld & 0x3FF)
            mark = ">" if n == self.sel else " "
            got = self._lim(n)
            if got:
                lim = f"[{got[0]},{got[1]}]"
            else:
                lo, hi = self.limits.get(n, (None, None))
                lim = f"[{lo if lo is not None else '--'},{hi if hi is not None else '--'}]"
            ctr = self.centre.get(n)
            warn = ""
            if p is not None and (p < END_MARGIN or p > 1023 - END_MARGIN):
                warn = " !STOP"      # against the servo's mechanical end of travel
            bits.append(f"{mark}{n} {str(p):>4} ({deg(p or 512):+6.1f}d) L{str(ld):>4} {lim}"
                        f"{' C' + str(ctr) if ctr else ''}{warn}")
        return "  ".join(bits)

    def save(self, path=None):
        """Write robot/calibration.py -- the file everything else imports.

        NOT next to this script. jog.py lives in robot/tools/, but every consumer
        does `from robot import calibration`, i.e. robot/calibration.py one level
        up. Writing beside the script created robot/tools/calibration.py, which
        nothing reads: the session appeared to succeed, the real file stayed
        stale, and the next clip played on the old numbers.
        """
        path = path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
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
        limits = {n: self._lim(n) or self.prev_limits.get(n) for n in ORDER}
        # Only a COMPLETE pair counts as measured. A half-pressed [ or ] must not
        # clear the UNCALIBRATED flag, or play_on_hardware's guard unlocks on a
        # limit that is one unit wide.
        measured = set(self.centre) | {n for n in ORDER if self._lim(n)}
        unknown = sorted(n for n in ORDER
                         if centre[n] is None or limits[n] is None
                         or (n in self.prev_uncal and n not in measured))
        half = [n for n in ORDER if self.limits.get(n) and not self._lim(n)]
        if half:
            print(f"  !! {', '.join(half)}: only one end recorded -- limits NOT saved"
                  f" for these. Press BOTH [ and ], at different positions.")

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
        lines.append("# INVERT is carried over, never measured here: which way a jog")
        lines.append("# key moves a joint does NOT tell you whether a clip plays")
        lines.append("# mirrored. Only playing one and comparing with the render does.")
        lines.append("# After re-mounting a horn, treat every entry below as stale.")
        lines.append("INVERT = {")
        for n in ORDER:
            v = bool(self.prev_invert.get(n, False))
            note = "" if n in self.prev_invert else "   # never confirmed against a render"
            lines.append(f'    "{n}": {v},{note}')
        lines.append("}")
        if self.prev_floors:
            lines.append("")
            lines.append("# Carried over from deadband_probe -- not measured by jog.py.")
            lines.append("FLOORS = {")
            for n in ORDER:
                if n in self.prev_floors:
                    lines.append(f'    "{n}": {self.prev_floors[n]},')
            lines.append("}")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def block(self):
        # Must resolve exactly as save() does. An earlier version fell back to
        # self.seen -- the range the joint happened to be jogged through -- so a
        # session where nothing was recorded still printed plausible-looking
        # limits, while the file kept the old ones. Two different answers in the
        # same breath, and the printed one was the lie.
        out = ["", "# what save() will write to robot/calibration.py", "LIMITS = {"]
        for n in ORDER:
            # Sorted, not as-pressed: which key walks a joint "up" depends on how
            # the horn was mounted, so [ and ] can land either way round. An
            # unsorted (lo, hi) makes clamp() collapse to a constant and the joint
            # silently freezes at one position.
            mine = self._lim(n)
            src = mine or self.prev_limits.get(n)
            tag = ("" if mine else
                   "   # HALF-PRESSED, IGNORED -- kept from disk"
                   if self.limits.get(n) and src else
                   "   # NOT SET THIS SESSION -- kept from disk" if src else
                   "   # NEVER MEASURED")
            lo, hi = sorted(src) if src else (462, 562)
            out.append(f'    "{n}": ({lo}, {hi}),{tag}')
        out.append("}")
        out.append("OFFSET = {")
        for n in ORDER:
            c = self.centre.get(n, self.prev_centre.get(n, 512))
            tag = "" if n in self.centre else "   # NOT SET THIS SESSION"
            out.append(f'    "{n}": {c - 512},{tag}')
        out.append("}")
        pending = [n for n in ORDER if n not in self.limits or n not in self.centre]
        if pending:
            out.append(f"# still to record: {', '.join(pending)}"
                       f"  (c = centre, [ = min, ] = max)")
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
                # Record ONLY this end. Filling the other end with the same value
                # produces a zero-width range that silently freezes the joint.
                j.limits[n] = (j.target[n], hi)
                other = "" if hi is not None else "   (still need ] at the other end)"
                print(f"\n{n} MIN = {j.target[n]}{other}")
                continue
            elif k == "]":
                n = j.sel
                lo, hi = j.limits.get(n, (None, None))
                j.limits[n] = (lo, j.target[n])      # only this end -- see "[" above
                other = "" if lo is not None else "   (still need [ at the other end)"
                print(f"\n{n} MAX = {j.target[n]}{other}")
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
        print("\nnot saved (press p during the session to update calibration.py)")
        for sid in IDS.values():
            try:
                bus.torque(sid, False)
            except Exception:
                pass
        bus.close()
        print("\ntorque off, port closed.")


if __name__ == "__main__":
    main()
