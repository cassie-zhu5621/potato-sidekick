"""
Minimal SCSCL layer for Feetech SCS0009 on top of `feetech-servo-sdk`.

  python3 -m pip install feetech-servo-sdk pyserial

NOTE: the pip package is `feetech-servo-sdk` but the import name is `scservo_sdk`.
That package ships ONLY the low-level `PacketHandler` — there is no `scscl` /
`sms_sts` convenience class like in Feetech's C++ SDK or their GitHub folder.
So the SCSCL register map and the WritePos packing live here.

Protocol note: SCS/SCSCL series is big-endian -> PacketHandler(1).
(STS/SMS series would be PacketHandler(0). Using the wrong one gives
byte-swapped garbage positions, not an error.)
"""
from scservo_sdk import (
    PortHandler, PacketHandler, COMM_SUCCESS, SCS_LOBYTE, SCS_HIBYTE,
)

PROTOCOL_END = 1

# ---- SCSCL control table ----
ADDR_ID               = 5
ADDR_BAUD             = 6
ADDR_MIN_ANGLE_LIMIT  = 9
ADDR_MAX_ANGLE_LIMIT  = 11
ADDR_TORQUE_ENABLE    = 40
ADDR_GOAL_POSITION    = 42
ADDR_GOAL_TIME        = 44
ADDR_GOAL_SPEED       = 46
ADDR_LOCK             = 48
ADDR_PRESENT_POSITION = 56
ADDR_PRESENT_SPEED    = 58
ADDR_PRESENT_LOAD     = 60
ADDR_PRESENT_VOLTAGE  = 62
ADDR_PRESENT_TEMP     = 63
ADDR_MOVING           = 66

# Baud rates PortHandler.getCFlagBaud() will accept. Anything else -> setBaudRate
# returns False (silently, so we raise).
SUPPORTED_BAUDS = [4800, 9600, 14400, 19200, 38400, 57600,
                   115200, 128000, 250000, 500000, 1000000]


class Bus:
    """One TTL bus behind an FE-URT2. Not thread-safe (half duplex)."""

    def __init__(self, port, baud=1_000_000):
        if baud not in SUPPORTED_BAUDS:
            raise ValueError(f"baud {baud} unsupported by the SDK; pick from {SUPPORTED_BAUDS}")
        self.ph = PortHandler(port)
        try:
            opened = self.ph.openPort()
        except Exception as e:               # nonexistent port raises, not returns
            raise IOError(f"cannot open {port}: {e}") from e
        if not opened:
            raise IOError(f"cannot open {port}")
        if not self.ph.setBaudRate(baud):
            raise IOError(f"cannot set baud {baud} on {port}")
        self.pk = PacketHandler(PROTOCOL_END)
        self.port_name, self.baud = port, baud

    # ---- reads ----
    # Reads are defensive on purpose. The vendor SDK indexes the reply buffer
    # without checking its length (read2ByteTxRx does data[1] whenever the result
    # is COMM_SUCCESS), so a short or misaligned reply raises IndexError from
    # inside the library. A status read is diagnostic -- it must never be able to
    # kill the thread that is driving the servos.
    def ping(self, sid):
        """-> (model_number, ok)"""
        try:
            model, res, err = self.pk.ping(self.ph, sid)
            return model, (res == COMM_SUCCESS)
        except Exception:
            return None, False

    def _r2(self, sid, addr):
        try:
            val, res, err = self.pk.read2ByteTxRx(self.ph, sid, addr)
        except Exception:
            self.flush_input()          # buffer is misaligned; resync
            return None
        return val if res == COMM_SUCCESS else None

    def _r1(self, sid, addr):
        try:
            val, res, err = self.pk.read1ByteTxRx(self.ph, sid, addr)
        except Exception:
            self.flush_input()
            return None
        return val if res == COMM_SUCCESS else None

    def read_pos(self, sid):
        return self._r2(sid, ADDR_PRESENT_POSITION)

    def read_load(self, sid):
        return self._r2(sid, ADDR_PRESENT_LOAD)

    def read_voltage(self, sid):
        v = self._r1(sid, ADDR_PRESENT_VOLTAGE)
        return None if v is None else v / 10.0     # register is in 0.1 V

    def read_temp(self, sid):
        return self._r1(sid, ADDR_PRESENT_TEMP)    # degrees C

    def read_angle_limits(self, sid):
        return self._r2(sid, ADDR_MIN_ANGLE_LIMIT), self._r2(sid, ADDR_MAX_ANGLE_LIMIT)

    # ---- writes ----
    def write_pos(self, sid, position, time_ms=0, speed=0):
        """SCSCL goal move. time_ms=0 & speed=0 -> go as fast as possible.
        Writes pos/time/speed as one 6-byte packet, same as Feetech's WritePos."""
        position = int(position); time_ms = int(time_ms); speed = int(speed)
        data = [SCS_LOBYTE(position), SCS_HIBYTE(position),
                SCS_LOBYTE(time_ms),  SCS_HIBYTE(time_ms),
                SCS_LOBYTE(speed),    SCS_HIBYTE(speed)]
        res, err = self.pk.writeTxRx(self.ph, sid, ADDR_GOAL_POSITION, len(data), data)
        return res == COMM_SUCCESS

    def write_pos_fast(self, sid, position, time_ms=0, speed=0):
        """Same, but fire-and-forget (no status packet read back). Use during
        clip playback so a 30 Hz stream of commands isn't stalled by 3 round
        trips per frame."""
        position = int(position); time_ms = int(time_ms); speed = int(speed)
        data = [SCS_LOBYTE(position), SCS_HIBYTE(position),
                SCS_LOBYTE(time_ms),  SCS_HIBYTE(time_ms),
                SCS_LOBYTE(speed),    SCS_HIBYTE(speed)]
        res = self.pk.writeTxOnly(self.ph, sid, ADDR_GOAL_POSITION, len(data), data)
        return res == COMM_SUCCESS

    def torque(self, sid, on):
        res, err = self.pk.write1ByteTxRx(self.ph, sid, ADDR_TORQUE_ENABLE, 1 if on else 0)
        return res == COMM_SUCCESS

    def set_id(self, old_id, new_id):
        """Unlock EEPROM, write ID, re-lock, verify.

        ID lives in EEPROM, which is write-protected by the LOCK register (48).
        Sequence: LOCK=0 -> write ID -> LOCK=1. The servo answers on the NEW id
        the instant the ID byte lands, so the re-lock must be addressed to
        new_id. Each step needs a moment: EEPROM writes are slow, and firing the
        next packet too early silently loses the write -- which shows up much
        later as an ID that reverts on the next power cycle.
        """
        import time
        D = 0.15   # EEPROM commits are slow; rushing the next packet loses the write

        # Torque off first: some firmware refuses EEPROM writes while driving.
        self.torque(old_id, False)
        time.sleep(D)

        if self.pk.write1ByteTxRx(self.ph, old_id, ADDR_LOCK, 0)[0] != COMM_SUCCESS:
            return False, "could not unlock EEPROM (LOCK=0 not acknowledged)"
        time.sleep(D)

        res, err = self.pk.write1ByteTxRx(self.ph, old_id, ADDR_ID, new_id)
        if res != COMM_SUCCESS:
            self.pk.write1ByteTxRx(self.ph, old_id, ADDR_LOCK, 1)
            return False, "ID write was not acknowledged"
        time.sleep(D)

        # The servo answers on the NEW id the moment the ID byte lands, so the
        # re-lock (which is what actually commits EEPROM) must be sent there.
        if self.pk.write1ByteTxRx(self.ph, new_id, ADDR_LOCK, 1)[0] != COMM_SUCCESS:
            return False, (f"ID is now {new_id} in RAM but the EEPROM re-lock "
                           f"failed -- it will revert on power-off")
        time.sleep(D)

        self.flush_input()
        readback = self._r1(new_id, ADDR_ID)
        self.torque(new_id, True)
        if readback != new_id:
            return False, f"read back ID={readback}, expected {new_id}"
        return True, "ok (verify by power-cycling and re-scanning)"

    def close_quiet(self):
        try:
            self.ph.closePort()
        except Exception:
            pass

    def flush_input(self):
        """Drop anything sitting in the RX buffer. Needed after write_pos_fast:
        the servo still sends a status packet, and since writeTxOnly never reads
        it, those pile up and corrupt the NEXT read if not cleared."""
        try:
            self.ph.ser.reset_input_buffer()
        except Exception:
            pass

    def close(self):
        try:
            self.ph.closePort()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Port discovery
#
# macOS names a usbmodem device after its USB LOCATION ID when the device has no
# serial number, so the CH343 in the FE-URT2 gets a different path whenever it is
# moved to another socket or comes up through a hub -- and the CoreS3 is also a
# usbmodem, so the names cannot be told apart by inspection either.
#
# Pinging is the only reliable test: the servo bus is whichever port a servo
# answers on. A hardcoded path is guaranteed to go stale, usually between
# sessions, and it fails as "the robot is dead" rather than "wrong port".
# ---------------------------------------------------------------------------
import glob as _glob
import os as _os


# Ports that are never a servo bus. Everything else is worth a ping.
_NOT_CANDIDATES = ("Bluetooth-Incoming-Port", "debug-console")


def list_candidates():
    """Serial ports that could plausibly be the servo bus.

    Deny-list, not allow-list. An allow-list of prefixes (usbmodem / usbserial /
    wch) misses whatever the next driver decides to call itself -- CP210x shows up
    as cu.SLAB_USBtoUART, for instance -- and a missed device presents as "nothing
    is plugged in", which sends you looking at cables instead of at the glob.
    /dev/cu.* only: /dev/tty.* blocks on open waiting for carrier detect and the
    script simply hangs with no error.
    """
    return sorted(p for p in _glob.glob("/dev/cu.*")
                  if not any(p.endswith(x) for x in _NOT_CANDIDATES))


def autodetect(baud=1_000_000, ids=(1, 2, 3), verbose=True):
    """-> port where a servo answers, or None. Leaves every port closed."""
    for port in list_candidates():
        try:
            bus = Bus(port, baud)
        except Exception:
            continue
        try:
            for sid in ids:
                if bus.ping(sid)[1]:
                    if verbose:
                        print(f"[scs] servo bus found on {port} (id {sid})")
                    return port
        finally:
            bus.close_quiet()
    return None


def open_bus(port=None, baud=None, ids=(1, 2, 3)):
    """The one way every tool should get a bus.

    Order: explicit argument, then NOTICEBOT_PORT if it still exists, then probe.
    """
    baud = int(baud or _os.environ.get("NOTICEBOT_BAUD", 1_000_000))
    hint = port or _os.environ.get("NOTICEBOT_PORT")
    tried = []

    if hint and _os.path.exists(hint):
        try:
            bus = Bus(hint, baud)
            if any(bus.ping(s)[1] for s in ids):
                return bus, hint
            bus.close_quiet()
            tried.append(f"{hint} (opened, no servo answered)")
        except Exception as e:
            tried.append(f"{hint} ({e})")
    elif hint:
        tried.append(f"{hint} (gone -- macOS renamed it, this is expected)")

    found = autodetect(baud, ids)
    if found:
        return Bus(found, baud), found

    cands = list_candidates()
    allp = sorted(_glob.glob("/dev/cu.*"))
    if not cands:
        raise IOError(
            "no serial device present at all -- this is not a renaming problem.\n"
            + ("  tried: " + "; ".join(tried) + "\n" if tried else "")
            + f"  /dev/cu.* contains only: {[p.split('.', 1)[1] for p in allp] or 'nothing'}\n"
            "  So the FE-URT2 is not enumerating. In order of likelihood:\n"
            "  - not plugged in, or plugged into a hub that is off\n"
            "  - a charge-only USB-C cable (no data lines) -- swap the cable\n"
            "  - try a different port on the Mac\n"
            "  Check with:  ls /dev/cu.*   before and after unplugging it.")
    raise IOError(
        "no servo bus found.\n"
        + ("  tried: " + "; ".join(tried) + "\n" if tried else "")
        + f"  probed and got no servo reply: {cands}\n"
        "  - does the servo bus have its own 5-6V supply? without it the servos\n"
        "    limp on USB back-feed and may not answer at all\n"
        "  - servo IDs still 1/2/3? run check_bus.py --scan-ids\n"
        "  - another program holding the port? close Arduino's Serial Monitor")
