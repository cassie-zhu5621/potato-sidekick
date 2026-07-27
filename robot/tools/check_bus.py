#!/usr/bin/env python3
"""
STEP 1 diagnostic: find the FE-URT2 port + baud, and confirm the three
SCS0009 servos answer on IDs 1 (pan) / 2 (tilt) / 3 (nod).

Install first:
  python3 -m pip install feetech-servo-sdk pyserial
  (pip name is feetech-servo-sdk; the import name is scservo_sdk)

Usage:
  python3 check_bus.py                       # auto-scan /dev/cu.usb* x all bauds
  python3 check_bus.py --port /dev/cu.usbmodemSN234567892
  python3 check_bus.py --port ... --baud 1000000
  python3 check_bus.py --port ... --scan-ids       # sweep IDs 0..20
  python3 check_bus.py --port ... --set-id 1 3     # one servo on the bus only!

Read-only except for --set-id. Nothing here moves the robot.
"""
import argparse, glob, os, sys

# Run me directly: the repo root is two levels up from robot/tools/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

try:
    from robot.scs import Bus, SUPPORTED_BAUDS
except ImportError as e:
    # Two very different faults used to print the same message, which sent people
    # looking at paths when the real problem was an environment. Say which it is.
    missing = getattr(e, "name", "") or str(e)
    if "scservo" in missing:
        sys.exit("the Feetech SDK is not installed in THIS python environment.\n"
                 f"  python:  {sys.executable}\n"
                 "  fix:     pip install feetech-servo-sdk\n"
                 "  note:    the pip name is feetech-servo-sdk, the import name\n"
                 "           is scservo_sdk, and `scservo-sdk` does not exist on\n"
                 "           PyPI. If you have a conda env for this project, you\n"
                 "           are probably in the wrong one -- activate it first.")
    if "serial" in missing:
        sys.exit(f"pyserial is not installed in {sys.executable}\n"
                 "  fix: pip install pyserial")
    sys.exit(f"cannot import robot.scs -- run this from the repo root: {e}")

# most likely first; FE-URT2 + SCS0009 ship at 1 Mbps
BAUDS = [1_000_000, 500_000, 115_200, 250_000, 128_000, 57_600, 38_400, 19_200, 9_600]
NAMES = {1: "pan", 2: "tilt", 3: "nod"}


def candidate_ports():
    # macOS: always /dev/cu.*, never /dev/tty.* -- tty.* blocks on open waiting
    # for carrier detect and the script just hangs with no error.
    # CH343 (this board's USB chip) enumerates as cu.usbmodem*, not cu.usbserial*.
    return sorted(set(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")))


def probe(port, bauds, ids):
    hits = []
    for baud in bauds:
        try:
            bus = Bus(port, baud)
        except Exception as e:
            print(f"  --  {port} @ {baud:>7}  {e}")
            continue
        try:
            found = []
            for sid in ids:
                model, ok = bus.ping(sid)
                if ok:
                    found.append((sid, model, bus.read_pos(sid),
                                  bus.read_voltage(sid), bus.read_temp(sid)))
            if found:
                for sid, model, pos, volt, temp in found:
                    print(f"  OK  {port} @ {baud:>7}  ID {sid:<3} ({NAMES.get(sid,'?'):<4}) "
                          f"model={model}  pos={pos}  {volt}V  {temp}C")
                hits.append((baud, [f[0] for f in found], found))
                return hits          # right baud found, stop sweeping
            print(f"  --  {port} @ {baud:>7}  no reply")
        finally:
            bus.close()
    return hits


def main():
    ap = argparse.ArgumentParser()
    # Defaults come from the environment so you don't retype the port every time:
    #   export NOTICEBOT_PORT=/dev/cu.usbmodem5B790340551
    # (a shell variable holding "--port X --baud Y" does NOT work in zsh -- zsh
    #  doesn't word-split unquoted expansions, so it arrives as one argument.)
    ap.add_argument("--port", default=os.environ.get("NOTICEBOT_PORT"))
    ap.add_argument("--baud", type=int,
                    default=int(os.environ.get("NOTICEBOT_BAUD", 0)) or None)
    ap.add_argument("--scan-ids", action="store_true", help="sweep IDs 0..20")
    ap.add_argument("--watch", type=int, metavar="ID", nargs="?", const=1,
                    help="live voltage/position readout for one servo. Wiggle the "
                         "power wires and watch the number move. Ctrl-C to stop.")
    ap.add_argument("--noise", type=int, metavar="ID",
                    help="measure how steady one servo's position reading is, "
                         "torque on then off. Turns 'it feels jumpy' into numbers.")
    ap.add_argument("--set-id", nargs=2, type=int, metavar=("OLD", "NEW"),
                    help="change a servo ID. Requires --port and --baud, and "
                         "only ONE servo connected to the bus.")
    a = ap.parse_args()

    if a.noise is not None:
        if not (a.port and a.baud):
            sys.exit("--noise needs --port and --baud (or NOTICEBOT_PORT/_BAUD)")
        import time, statistics
        bus = Bus(a.port, a.baud)
        sid = a.noise

        def sample(n=120, dt=0.04):
            pos, volts, fails = [], [], 0
            for _ in range(n):
                bus.flush_input()
                p = bus.read_pos(sid)
                v = bus.read_voltage(sid)
                (pos.append(p) if p is not None else None)
                if p is None:
                    fails += 1
                if v is not None:
                    volts.append(v)
                time.sleep(dt)
            return pos, volts, fails

        def report(label, pos, volts, fails):
            if not pos:
                print(f"{label}: no successful reads at all ({fails} failures)")
                return None
            spread = max(pos) - min(pos)
            sd = statistics.pstdev(pos) if len(pos) > 1 else 0.0
            print(f"{label}: n={len(pos)}  min={min(pos)} max={max(pos)} "
                  f"spread={spread} units ({spread/(1023/300.0):.1f} deg)  sd={sd:.1f}")
            print(f"{'':<{len(label)}}  failed reads: {fails}"
                  f"   volts {min(volts) if volts else '?'}..{max(volts) if volts else '?'}")
            return spread

        try:
            if not bus.ping(sid)[1]:
                sys.exit(f"id {sid} not responding")
            print("Hold still / don't touch the robot.\n")
            bus.torque(sid, True); time.sleep(0.5)
            on = report("torque ON ", *sample())
            input("\nNow torque will be released -- SUPPORT THE JOINT so it cannot "
                  "move, then press Enter.")
            bus.torque(sid, False); time.sleep(0.3)
            off = report("torque OFF", *sample())
            bus.torque(sid, True)

            print("\nreading it:")
            print("  spread <= 3 units          -> normal, sensor is fine")
            print("  failed reads > 0           -> bus/comms problem: supply sag, thin")
            print("                                battery leads, or the splitter board")
            print("  big spread, 0 failures, torque ON  -> the servo is hunting, or the")
            print("                                pot is worn/noisy")
            print("  big spread only when torque OFF    -> the joint really is moving")
            print("                                (gravity + backlash), sensor is fine")
        finally:
            bus.close()
        return

    if a.watch:
        if not (a.port and a.baud):
            sys.exit("--watch needs --port and --baud")
        bus = Bus(a.port, a.baud)
        print(f"watching ID {a.watch}. ~4.3V = running off USB back-feed only; "
              f"~6V = external supply reaching the bus. Ctrl-C to stop.")
        try:
            import time
            while True:
                v, p, t = (bus.read_voltage(a.watch), bus.read_pos(a.watch),
                           bus.read_temp(a.watch))
                bar = "" if v is None else "#" * int(max(0, min(20, (v - 3.5) * 8)))
                print(f"\r  {str(v)+'V':>7}  pos={str(p):>5}  {t}C  {bar:<20}",
                      end="", flush=True)
                time.sleep(0.3)
        except KeyboardInterrupt:
            print()
        finally:
            bus.close()
        return

    if a.set_id:
        if not (a.port and a.baud):
            sys.exit("--set-id needs --port and --baud")
        old, new = a.set_id
        bus = Bus(a.port, a.baud)
        try:
            # Safe on a mixed bus as long as exactly one servo answers on `old`
            # and nothing already holds `new`.
            if not bus.ping(old)[1]:
                sys.exit(f"no servo answering on ID {old} -- nothing to rename")
            if bus.ping(new)[1]:
                sys.exit(f"ID {new} is already taken. Pick a free ID, or park the "
                         f"existing one somewhere else first.")
            print(f"one servo answers on {old}; {new} is free. Changing {old} -> {new}")
            if input("type yes to continue: ").strip() != "yes":
                sys.exit("aborted")
            ok, msg = bus.set_id(old, new)
            print(("ok -- " if ok else "FAILED -- ") + msg)
            if ok:
                print("  Now POWER CYCLE and re-scan. The ID only really counts")
                print("  as set if it survives losing power.")
        finally:
            bus.close()
        return

    ports = [a.port] if a.port else candidate_ports()
    if not ports:
        print("No /dev/cu.usbmodem* or /dev/cu.usbserial* found.\n"
              "  - FE-URT2 plugged in over Type-C?\n"
              "  - try another cable (charge-only cables have no data lines)\n"
              "  - compare `ls /dev/cu.*` with the board unplugged vs plugged")
        return
    bauds = [a.baud] if a.baud else BAUDS
    ids = list(range(0, 21)) if a.scan_ids else [1, 2, 3]

    print(f"ports: {ports}\nbauds: {bauds}\nids  : {ids[0]}..{ids[-1]}\n")

    results = {}
    for p in ports:
        print(p)
        h = probe(p, bauds, ids)
        if h:
            results[p] = h[0]
        print()

    if not results:
        print("NOTHING RESPONDED.")
        print("  0. ID COLLISION -- check this FIRST if a single servo worked a")
        print("     moment ago and adding more killed it. Servos sharing an ID all")
        print("     answer at once and garble each other, so the bus goes silent")
        print("     rather than half-working. Unplug back to one servo to confirm.")
        print("  Otherwise, in likelihood order:")
        print("  1. SERVO POWER. USB does not power the bus. The blue screw terminal")
        print("     needs an external supply -- SCS0009 is a 6V-class servo, so use")
        print("     5-6V. The board accepts up to 12V but 12V will cook an SCS0009.")
        print("  2. LEVEL SWITCH. Set the serial signal level slider to 5V, not 3.3V.")
        print("     SCS TTL servos expect 5V logic; at 3.3V the servo may never see")
        print("     a valid high. Move the switch with power OFF.")
        print("  3. Servos in the TTL-Bus 3-pin sockets (G/V1/S), not the RS485 XH4")
        print("     sockets, and the daisy chain seated at every joint.")
        print("  4. Don't drive the UART header and Type-C at the same time --")
        print("     the board says pick one mode.")
        print("  5. Still nothing: --scan-ids to widen, or check the port is the")
        print("     FE-URT2 and not your other usbmodem device.")
        return

    for port, (baud, found_ids, detail) in results.items():
        if found_ids != [1, 2, 3]:
            print(f"{port}: found IDs {found_ids}, expected [1, 2, 3].")
            if found_ids == [1]:
                print("  If only ONE servo is physically connected, this is fine -- plug in")
                print("  the rest and re-run. If all three are connected, they are probably")
                print("  still on factory default ID 1 and")
                print("  are colliding on the bus. Unplug all but one servo, then:")
                print(f"    python3 check_bus.py --port {port} --baud {baud} --set-id 1 2")
                print("  Repeat for the third as ID 3. Do NOT play a clip until 1/2/3.")
        else:
            volts = [d[3] for d in detail if d[3]]
            print(f"All three servos responding on {port} @ {baud}.")
            if volts and min(volts) < 4.5:
                print(f"  ! bus voltage reads {min(volts)}V -- low, check the supply.")
            print("Put these in play_on_hardware.py:")
            print(f'  PORT = "{port}"')
            print(f"  BAUD = {baud}")


if __name__ == "__main__":
    main()
