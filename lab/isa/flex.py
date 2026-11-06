# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Flexibility study: can Loom run protocols it wasn't designed for, in controller and target roles?

Device models and decoders here are written from each protocol's rules and read only pins, apart from
firmware-to-host FIFO values, which a real host would also see.
"""
import csv
import sys

import checkers as ck
import flex_fw as ff
import isa_enc as enc
from sim import Chip, Core, Infeasible, run


def words(variant, prog):
    for ins in prog:
        enc.fits(variant, ins)
    return len(prog)


class Bus:
    """Open-drain line with a pull-up: falls at once, rises after `rise` cycles."""

    def __init__(self, rise=0):
        self.rise, self.level, self.rem = rise, 1, 0

    def step(self, pulled_low):
        if pulled_low:
            self.level, self.rem = 0, self.rise
        elif self.rem > 0:
            self.rem -= 1
        else:
            self.level = 1
        return self.level


# ------------------------------------------------------------------ JTAG
TAP = {  # IEEE 1149.1 state machine: state -> (next if TMS=0, next if TMS=1)
    "TLR": ("RTI", "TLR"), "RTI": ("RTI", "SDS"), "SDS": ("CDR", "SIS"), "CDR": ("SDR", "E1D"),
    "SDR": ("SDR", "E1D"), "E1D": ("PDR", "UDR"), "PDR": ("PDR", "E2D"), "E2D": ("SDR", "UDR"),
    "UDR": ("RTI", "SDS"), "SIS": ("CIR", "TLR"), "CIR": ("SIR", "E1I"), "SIR": ("SIR", "E1I"),
    "E1I": ("PIR", "UIR"), "PIR": ("PIR", "E2I"), "E2I": ("SIR", "UIR"), "UIR": ("RTI", "SDS"),
}
IDCODE = 0x4BA00477


def jtag_case(half):
    prog = ff.jtag_idcode(half)
    n = words("V3", prog)
    chip = Chip()
    core = chip.add(Core(prog, chip))
    st = {"tap": "RTI", "ptck": 0, "sr": 0, "tdo": 1}

    def bus(t, out, lv):
        tck = out[4] if out[4] is not None else 0
        tms = out[5] if out[5] is not None else 1
        if tck and not st["ptck"]:
            if st["tap"] == "CDR":
                st["sr"] = IDCODE
            elif st["tap"] == "SDR":
                st["sr"] >>= 1
            st["tap"] = TAP[st["tap"]][tms]
        if st["ptck"] and not tck and st["tap"] == "SDR":
            st["tdo"] = st["sr"] & 1
        st["ptck"] = tck
        lv[7] = st["tdo"]
        return lv
    run(chip, 200 + half * 2 * 60, None, bus)
    got = core.fifo_rx
    ok = len(got) == 2 and (got[0] | got[1] << 16) == IDCODE and st["tap"] == "E1D"
    return n, ok


# ------------------------------------------------------------------ SWD
DPIDR = 0x0BB11477


def swd_case(half):
    prog = ff.swd_read(half)
    n = words("V3", prog)
    chip = Chip()
    core = chip.add(Core(prog, chip))
    word = 0b001 | (DPIDR << 3) | (bin(DPIDR).count("1") % 2 << 35)
    st = {"pclk": 0, "ones": 0, "phase": "reset", "req": [], "cnt": 0, "drive": None, "good": False}

    def bus(t, out, lv):
        clk = out[4] if out[4] is not None else 0
        host = out[5]
        line = host if host is not None else (st["drive"] if st["drive"] is not None else 1)
        if clk and not st["pclk"]:  # target samples and changes on the rising edge
            if st["phase"] == "reset":
                st["ones"] = st["ones"] + 1 if line else 0
                if st["ones"] >= 50:
                    st["phase"] = "armed"
            elif st["phase"] == "armed" and line == 0:
                st["phase"] = "idle"
            elif st["phase"] == "idle" and line == 1:
                st["phase"], st["req"] = "req", [1]
            elif st["phase"] == "req":
                st["req"].append(line)
                if len(st["req"]) == 8:
                    r = st["req"]
                    st["good"] = r[0] == 1 and r[6] == 0 and r[7] == 1 and (r[1] + r[2] + r[3] + r[4]) % 2 == r[5] and r[2] == 1
                    st["phase"], st["cnt"] = "trn", 0
            elif st["phase"] == "trn":
                st["phase"], st["cnt"] = "send", 0
            elif st["phase"] == "send":
                if st["good"] and st["cnt"] < 36:
                    st["drive"] = (word >> st["cnt"]) & 1
                    st["cnt"] += 1
                else:
                    st["drive"] = None
        st["pclk"] = clk
        lv[5] = line
        return lv
    run(chip, 200 + half * 2 * 110, None, bus)
    got = core.fifo_rx
    ok = len(got) == 4 and got[0] >> 13 == 0b001 and (got[1] | got[2] << 16) == DPIDR and (got[3] >> 15) == bin(DPIDR).count("1") % 2
    return n, ok


# ------------------------------------------------------------------ PS/2
def ps2_case(bit):
    prog = ff.ps2_rx()
    n = words("V3", prog)
    chip = Chip()
    core = chip.add(Core(prog, chip))
    data = [0x1C, 0xF0, 0x5A]
    frames = []
    for byte in data:
        par = 1 - bin(byte).count("1") % 2
        frames.append([0] + [(byte >> k) & 1 for k in range(8)] + [par, 1])
    start, gap = 100, 3 * bit

    def stim(t):
        rel = t - start
        span = 11 * bit + gap
        f = rel // span
        if rel < 0 or f >= len(frames):
            return {0: 1, 1: 1}
        pos = rel - f * span
        idx = pos // bit
        if idx >= 11:
            return {0: 1, 1: 1}
        return {0: 0 if (pos % bit) >= bit // 2 else 1, 1: frames[f][idx]}
    run(chip, start + (len(frames) + 1) * (11 * bit + gap), stim)
    got = []
    for w in core.fifo_rx:
        v = (w >> 5) & 0x7FF
        ok_frame = (v & 1) == 0 and (v >> 10) & 1 == 1 and bin((v >> 1) & 0x1FF).count("1") % 2 == 1
        got.append(((v >> 1) & 0xFF) if ok_frame else None)
    return n, got == data


# ------------------------------------------------------------------ SPI target, modes 0-3
def spi_target_case(mode, half):
    prog = ff.spi_target(mode)
    n = words("V3", prog)
    chip = Chip()
    core = chip.add(Core(prog, chip))
    tx_t, tx_m = [0xA5, 0x3C], [0x96, 0x0F]
    core.fifo_tx = [b << 8 for b in tx_t]
    cpol, cpha = (mode >> 1) & 1, mode & 1
    start, nbits = 80, 16
    got = {"v": 0, "n": 0}

    def bus(t, out, lv):
        rel = t - start
        lv[1] = 0 if -30 <= rel < nbits * 2 * half + 30 else 1
        edge = rel // half if rel >= 0 else -1
        bit = min(max(edge, 0) // 2, nbits - 1)
        active = 0 <= edge < nbits * 2
        phase = edge % 2 if active else 0  # 0 until the first edge's half is over
        sck = cpol ^ (1 if active and phase == 0 else 0)
        lv[0] = sck
        # A real master changes MOSI just after its clock edge, not on the same cycle.
        late = rel - 2
        ledge = late // half if late >= 0 else -1
        lbit = min(max(ledge, 0) // 2, nbits - 1)
        if cpha == 0:  # changes on even edges, except the first bit, which is set up before edge 0
            idx = lbit + 1 if ledge >= 0 and ledge % 2 == 1 else lbit
        else:  # changes on the first edge of each bit
            idx = lbit
        idx = min(idx, nbits - 1)
        lv[2] = (tx_m[idx // 8] >> (7 - idx % 8)) & 1
        sample_now = active and rel % half == 0 and ((phase == 0) if cpha == 0 else (phase == 1))
        if sample_now and got["n"] < nbits:
            got["v"] = (got["v"] << 1) | (out[4] if out[4] is not None else 1)
            got["n"] += 1
        return lv
    run(chip, start + nbits * 2 * half + 200, None, bus)
    master = [(got["v"] >> 8) & 0xFF, got["v"] & 0xFF]
    ok = got["n"] == nbits and master == tx_t and [x & 0xFF for x in core.fifo_rx] == tx_m
    return n, ok


# ------------------------------------------------------------------ I2C controller model, shared
def i2c_timeline(bytes_):
    q = [(1, 1), (1, 1), (1, 0), (0, 0)]
    for byte in bytes_:
        for k in range(7, -1, -1):
            d = (byte >> k) & 1
            q += [(0, d), (1, d), (1, d), (0, d)]
        q += [(0, 1), (1, 1), (1, 1), (0, 1)]
    q += [(0, 0), (1, 0), (1, 1), (1, 1), (1, 1)]
    return q


class Controller:
    """Holds SCL if the target stretches it, and samples each ACK during the high phase."""

    def __init__(self, bytes_, quarter, start=40):
        self.q, self.quarter, self.start = i2c_timeline(bytes_), quarter, start
        self.i, self.cnt, self.acks, self.wait = 0, 0, [], 0
        self.ack_at = [4 + 36 * b + 33 for b in range(len(bytes_))]

    def drive(self, t, scl_level, sda_level):
        if t < self.start or self.i >= len(self.q):
            return 1, 1
        scl, sda = self.q[self.i]
        if scl == 1 and scl_level == 0:  # clock stretching: time stops
            self.wait += 1
            if self.wait > 200000:
                self.i = len(self.q)
            return scl, sda
        self.cnt += 1
        if self.cnt >= self.quarter:
            self.cnt = 0
            if self.i in self.ack_at:
                self.acks.append(sda_level)
            self.i += 1
        return scl, sda

    @property
    def done(self):
        return self.i >= len(self.q)


def i2c_target_case(quarter, rise=0):
    prog = ff.i2c_target(0x50)
    n = words("V3", prog)
    chip = Chip()
    chip.odrain = {4, 5}
    core = chip.add(Core(prog, chip))
    sequence = [0x50 << 1, 0xA5]
    ctl = Controller(sequence, quarter)
    scl, sda = Bus(rise), Bus(rise)

    def bus(t, out, lv):
        ds, dd = ctl.drive(t, scl.level, sda.level)
        lv[4] = scl.step(ds == 0)
        lv[5] = sda.step(dd == 0 or out[5] == 0)
        return lv
    run(chip, 200 + (len(ctl.q) + 4) * quarter + 10 * rise, None, bus)
    return n, ctl.acks == [0, 0] and [x & 0xFF for x in core.fifo_rx] == [0xA5] and ctl.done


# ------------------------------------------------------------------ I2C interposing through the bridge
class Target:
    """Decodes what it receives, ACKs every byte, and stretches SCL after the first ACK."""

    def __init__(self, stretch=0):
        self.pscl, self.psda, self.bits, self.bytes, self.ack, self.n = 1, 1, [], [], False, 0
        self.stretch, self.hold, self.started, self.stretched = stretch, 0, False, False

    def update(self, scl, sda):
        if scl and self.pscl and self.psda and not sda:
            self.started, self.bits, self.n, self.ack = True, [], 0, False
        if self.started:
            if scl and not self.pscl:
                if not self.ack:
                    self.bits.append(sda)
                if len(self.bits) == 8 and not self.ack:
                    self.bytes.append(int("".join(map(str, self.bits)), 2))
            if not scl and self.pscl:
                if self.ack:
                    self.ack, self.bits = False, []
                    if self.stretch and not self.stretched:
                        self.hold, self.stretched = self.stretch, True
                elif len(self.bits) == 8:
                    self.ack = True
        if self.hold:
            self.hold -= 1
        self.pscl, self.psda = scl, sda
        return (0 if self.hold else 1), (0 if self.ack else 1)


def i2c_interpose_case(quarter, rise, holdoff, nack_bit=None, stretch=0):
    sent = [0xA4, 0x5C, 0x3A, 0x77]
    if nack_bit:
        prog = ff.i2c_nack(nack_bit)
    else:
        from firmware import P
        p = P("V3")
        for line in (0, 1, 4, 5):
            p.i("MODE", line=line, src=ff.ODB)
        p.i("HALT")
        prog = p.done()
    n = words("V3", prog)
    chip = Chip()
    chip.holdoff = holdoff
    chip.odrain = {0, 1, 4, 5}
    chip.add(Core(prog, chip))
    ctl = Controller(sent, quarter)
    tgt = Target(stretch)
    a_scl, a_sda, b_scl, b_sda = Bus(rise), Bus(rise), Bus(rise), Bus(rise)
    stuck = {"max_a": 0, "run": 0, "c": [], "d": []}

    def bus(t, out, lv):
        ds, dd = ctl.drive(t, a_scl.level, a_sda.level)
        ts, td = tgt.update(b_scl.level, b_sda.level)
        lv[0] = a_scl.step(ds == 0 or out[0] == 0)
        lv[1] = a_sda.step(dd == 0 or out[1] == 0)
        lv[4] = b_scl.step(ts == 0 or out[4] == 0)
        lv[5] = b_sda.step(td == 0 or out[5] == 0)
        stuck["end"] = (lv[0], lv[1], lv[4], lv[5])
        return lv
    run(chip, 400 + (len(ctl.q) + 8) * quarter + stretch + 20 * rise, None, bus)
    want_acks = [0, 0, 1, 0] if nack_bit else [0, 0, 0, 0]
    ok = ctl.done and tgt.bytes == sent and ctl.acks == want_acks and stuck["end"] == (1, 1, 1, 1)
    return n, ok, {"bytes": tgt.bytes, "acks": ctl.acks, "end": stuck["end"], "done": ctl.done}


# ------------------------------------------------------------------ Manchester
def manchester_case(kind, half):
    prog = ff.manchester_soft(half) if kind == "soft" else ff.manchester_hw(half)
    n = words("V3" if kind == "soft" else "V8", prog)
    chip = Chip()
    core = chip.add(Core(prog, chip))
    data = [0xA55A, 0x0FF0]
    core.fifo_tx = list(data)
    trace = run(chip, 200 + half * 2 * 40, None)
    lv = [s[4] if s[4] is not None else 1 for s in trace]
    # Find the first half-bit boundary, then read pairs at the half-bit centres
    first = next((t for t in range(1, len(lv)) if chip_edge(lv, t)), None)
    if first is None:
        return n, False
    bits = []
    t = first + half // 2
    while t + half < len(lv) and len(bits) < 32:
        a, b = lv[t], lv[t + half]
        if a == b:
            break
        bits.append(a)
        t += 2 * half
    want = []
    for w in data:
        want += [(w >> k) & 1 for k in range(16)]
    return n, bits[:32] == want


def chip_edge(lv, t):
    return lv[t] != lv[t - 1]


# ------------------------------------------------------------------ USB low speed
def crc16_usb_ok(bits):
    """Residual check, written from the USB rules: MSB-style register, polynomial 0x8005."""
    crc = 0xFFFF
    for b in bits:
        fb = b ^ (crc >> 15)
        crc = (crc << 1) & 0xFFFF
        if fb:
            crc ^= 0x8005
    return crc == 0x800D


def usb_decode(trace, bit, dm=5, dp=4):
    idle = [(s[dm], s[dp]) for s in trace]
    start = next((t for t in range(1, len(idle)) if idle[t] != idle[t - 1] and idle[t] == (0, 1)), None)
    if start is None:
        return None
    bits, prev, ones, t, se0 = [], (1, 0), 0, start + bit // 2, 0
    raw = []
    while t < len(idle):
        now = idle[t]
        if now == (0, 0):
            se0 += 1
            if se0 >= 2:
                break
            t += bit
            continue
        val = 1 if now == prev else 0
        prev = now
        if ones == 6:
            if val != 0:
                return None  # a stuffing error
            ones = 0
        else:
            raw.append(val)
            ones = ones + 1 if val else 0
        t += bit
    return raw if se0 >= 2 else None


def usb_case(kind, bit, barrel=False):
    prog = ff.usb_tx_soft(bit) if kind == "soft" else ff.usb_tx_hw(bit)
    n = words("V3" if kind == "soft" else "V8", prog)
    chip = Chip()
    chip.barrel = barrel
    core = chip.add(Core(prog, chip))
    if barrel:
        chip.add(Core([], chip))
    sync, pid, d = 0x80, 0xC3, [0xFF, 0x3F]  # the data forces several stuffed bits
    core.fifo_tx = [sync | pid << 8, d[0] | d[1] << 8]
    trace = run(chip, 300 + 60 * bit)
    raw = usb_decode(trace, bit)
    if raw is None or len(raw) != 48:
        return n, False
    def byte(i):
        return sum(raw[i * 8 + k] << k for k in range(8))
    return n, byte(0) == sync and byte(1) == pid and byte(2) == d[0] and byte(3) == d[1] and crc16_usb_ok(raw[16:])


# ------------------------------------------------------------------ CAN
def can_frame_bits(cid=0x2A5, data=0xA7):
    bits = [0] + [(cid >> k) & 1 for k in range(10, -1, -1)] + [0, 0, 0] + [0, 0, 0, 1]
    return bits + [(data >> k) & 1 for k in range(7, -1, -1)]


def can_crc(bits):
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


def can_case(kind, bit, compete_at=None):
    prog = ff.can_tx_soft(bit) if kind == "soft" else ff.can_tx_hw(bit)
    n = words("V3" if kind == "soft" else "V8", prog)
    chip = Chip()
    core = chip.add(Core(prog, chip))
    frame = can_frame_bits()
    w1 = sum(b << (15 - i) for i, b in enumerate(frame[:16]))
    w2 = sum(b << (15 - i) for i, b in enumerate(frame[16:]))
    core.fifo_tx = [w1, w2]
    log = []
    drove = []
    sof = {"t": None}

    def bus(t, out, lv):
        own = out[4] if out[4] is not None else 1
        drove.append(own)
        if own == 0 and sof["t"] is None:
            sof["t"] = t
        other = 1
        if compete_at is not None and sof["t"] is not None:
            rel = (t - sof["t"]) // bit
            if rel == compete_at:
                other = 0
        lv[4] = own & other
        log.append(lv[4])
        return lv
    run(chip, 200 + 90 * bit, None, bus)
    if compete_at is not None:
        # It must back off: no dominant drive once the competitor has won that bit.
        after = sof["t"] + (compete_at + 1) * bit + 2 if sof["t"] is not None else 0
        return n, sof["t"] is not None and all(v == 1 for v in drove[after:])
    if sof["t"] is None:
        return n, False
    # Destuff from the wire and check every field
    t, out, run_val, run_len = sof["t"] + bit // 2, [], None, 0
    while t < len(log) and len(out) < 27 + 15:
        v = log[t]
        if run_len == 5:
            if v == run_val:
                return n, False
            run_val, run_len = v, 1
        else:
            out.append(v)
            if v == run_val:
                run_len += 1
            else:
                run_val, run_len = v, 1
        t += bit
    if len(out) < 42 or out[:27] != frame:
        return n, False
    return n, sum(b << (14 - i) for i, b in enumerate(out[27:42])) == can_crc(frame)


def smallest(fn, lo, hi):
    """Smallest passing period, plus whether every period above it up to `hi` passes too."""
    passed = []
    for x in range(lo, hi):
        try:
            if fn(x)[1]:
                passed.append(x)
        except (Infeasible, ValueError, IndexError, StopIteration):
            pass
    return (min(passed), passed == list(range(min(passed), hi))) if passed else (None, False)


def main(path):
    rows = []

    def add(proto, role, cfg, nominal, n, ok, mn=None, solid=None, note=""):
        rows.append(dict(protocol=proto, role=role, config=cfg, nominal=nominal, words=n, passes=ok, min_period=mn, all_above_pass=solid, note=note))
        print(f"{proto:14s} {role:11s} {cfg:12s} nominal {str(nominal):6s} words {str(n):4s} pass {str(ok):5s} min {mn} solid {solid} {note}")

    for name, fn, nominal, lo, hi, role in (
        ("JTAG", jtag_case, 25, 2, 40, "controller"), ("SWD", swd_case, 25, 2, 40, "controller"),
        ("PS/2", ps2_case, 4000, 6, 60, "receiver")):
        n, ok = fn(nominal)
        mn, solid = smallest(fn, lo, hi)
        add(name, role, "Loom", nominal, n, ok, mn, solid)
    for mode in range(4):
        fn = lambda h, m=mode: spi_target_case(m, h)
        n, ok = fn(25)
        mn, solid = smallest(fn, 3, 40)
        add(f"SPI mode {mode}", "target", "Loom", 25, n, ok, mn, solid)
    for q, label in ((125, "100 kHz"), (31, "400 kHz")):
        n, ok = i2c_target_case(q, rise=q // 3)
        add("I2C", "target", label, q, n, ok)
    for kind, label in (("soft", "Loom"), ("hw", "Loom+coder")):
        fn = lambda h, k=kind: manchester_case(k, h)
        mn, solid = smallest(fn, 1, 30)
        n, _ = fn(20)
        add("Manchester", "transmit", label, 3, n, fn(3)[1], mn, solid, "half-bit, cycles. 10BASE-T needs 2.5 at 50 MHz")
    for kind, label in (("soft", "Loom"), ("hw", "Loom+coder")):
        fn = lambda b, k=kind: usb_case(k, b)
        n, ok = fn(33)
        mn, solid = smallest(fn, 4, 50)
        add("USB LS", "transmit", label, 33, n, ok, mn, solid, "33 cycles is 1% from 1.5 Mbit/s")
        fn2 = lambda b, k=kind: usb_case(k, b, True)
        n, ok = fn2(33)
        mn, solid = smallest(fn2, 4, 70)
        add("USB LS", "transmit", label + " 2ctx", 33, n, ok, mn, solid, "second context idle, half rate")
    for kind, label in (("soft", "Loom"), ("hw", "Loom+coder")):
        fn = lambda b, k=kind: can_case(k, b)
        n, ok = fn(100)
        mn, solid = smallest(fn, 8, 120)
        add("CAN", "transmit", label, 100, n, ok, mn, solid, "100 cycles is 500 kbit/s")
        n, ok = can_case(kind, 100, compete_at=2)
        add("CAN arbitration", "transmit", label, 100, n, ok, None, None, "competitor wins bit 2: must back off")
    # The bridge: pass-through alone, then a forced NACK on the 3rd byte, with clock stretching.
    for q, label, rise in ((125, "100 kHz", 50), (31, "400 kHz", 15)):
        n, ok, info = i2c_interpose_case(q, rise, 2 * rise + 8)
        add("I2C bridge", "interpose", label, q, n, ok, note=f"pass-through, rise {rise} cycles")
        n, ok, info = i2c_interpose_case(q, rise, 2 * rise + 8, stretch=400)
        add("I2C bridge", "interpose", label + " stretch", q, n, ok, note="target stretches SCL 8 us")
        n, ok, info = i2c_interpose_case(q, rise, 2 * rise + 8, nack_bit=27)
        add("I2C NACK", "interpose", label, q, n, ok, note=f"3rd ACK forced to NACK; acks seen {info['acks']}")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    # Holdoff window
    print("holdoff window: pass-through OK? rows are rise cycles, columns are holdoff")
    hs = [0, 1, 2, 4, 8, 16, 24, 32, 48, 64, 96, 128, 192]
    table = []
    for q, label in ((125, "100k"), (31, "400k")):
        for rise in (0, 5, 15, 25, 50):
            line = [i2c_interpose_case(q, rise, h)[1] for h in hs]
            print(f"  {label} rise {rise:3d}: " + " ".join("Y" if x else "." for x in line))
            table.append(dict(rate=label, rise=rise, **{f"h{h}": v for h, v in zip(hs, line)}))
    print("  holdoff values:", hs)
    with open(path.replace(".csv", "_holdoff.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(table[0].keys()))
        w.writeheader()
        w.writerows(table)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "flex_results.csv")
