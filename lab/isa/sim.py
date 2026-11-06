# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Cycle-exact chip model for comparing SPLICE ISA candidates. Lab code, not the golden model.

One simulator implements the union of every candidate instruction. A variant is the subset a
firmware may use, plus the encoding table in encodings.py that proves the subset fits 16 bits.

Timing rules, shared by all variants:
- An instruction takes 1 cycle plus its delay field.
- Pin effects land on the clock edge ending the instruction's first cycle.
- Inputs pass a 2-flop synchronizer, then an output register: PASS latency is 3 cycles.
- Taken jumps cost nothing extra (the macro read result is decoded the same cycle).
"""
from dataclasses import dataclass, field

HIZ, ENG, PASS, INV, F0, F1, ODB = "HIZ", "ENG", "PASS", "INV", "F0", "F1", "ODB"


class Infeasible(Exception):
    """The firmware cannot meet the requested timing on this variant."""


@dataclass
class I:
    op: str
    a: dict = field(default_factory=dict)
    dly: int = 0
    side: int | None = None  # side-set value, or None
    tick: bool = False  # wait for the next bit-timer tick before executing


class Core:
    def __init__(self, prog, chip, cid=0, div=1, period=1):
        self.prog, self.chip, self.cid = prog, chip, cid
        self.pc = 0
        self.R = [0, 0, 0, 0]
        self.SR = 0
        self.BC = 0
        self.TO = 0
        self.Z = 0
        self.F = 0
        self.stall = 0
        self.to_left = None
        self.halted = False
        self.side_line = None
        self.msb_first = False
        self.div = div
        self.div_cnt = 0
        self.period = 1  # execution rate, set >1 by a barrel scheduler
        self.lfsr = 0xACE1
        # Bit timer
        self.tp = 0
        self.tcnt = 0
        self.tick_pending = 0
        self.missed = 0
        self.tstamp = 0
        self.tsrc = None
        self.last = [1] * 12
        self.lr = 0
        # Optional line-coding hardware (variant V8)
        self.crc = 0
        self.poly = 0
        self.crc_on = False
        self.crc_top = 15
        self.stuff_n = 0  # insert a bit after this many equal bits, 0 = off
        self.stuff_ones = False  # USB counts ones only and inserts a 0; CAN counts either level
        self.run_len = 0
        self.run_val = None
        self.stuff_pending = None
        self.nrzi = False
        self.level = 1
        self.manchester = False
        self.half = None
        self.pair_line = None  # driven with the complement on the same edge
        self.arb_at = 0
        self.arb_cnt = 0
        self.arb_drv = 1
        self.arb_line = None
        self.lost = False
        self.dead = 0
        self.t = 0
        self.fifo_rx = []  # engine to host
        self.fifo_tx = []  # host to engine
        self.trace = []
        self.executed = 0
        self.wait_cycles = 0
        self.delay_cycles = 0

    def timer(self):
        self.t = (self.t + 1) & 0xFFFF
        if self.tsrc is not None:  # follow an input edge instead of the period timer
            line, want = self.tsrc
            changed = self.chip.prev[line] != self.chip.s2[line]
            if changed and (want is None or self.chip.s2[line] == want):
                if self.tick_pending:
                    self.missed += 1
                    self.F = 1
                self.tick_pending = 1
        elif self.tp:
            if self.tcnt <= 1:
                self.tcnt = self.tp
                if self.tick_pending:
                    self.missed += 1
                    self.F = 1
                self.tick_pending = 1
            else:
                self.tcnt -= 1

    def step(self, s2, prev):
        self.timer()
        if self.arb_cnt:
            self.arb_cnt -= 1
            if self.arb_cnt == 0 and self.arb_drv == 1 and s2[self.arb_line] == 0:
                self.lost = True
                self.F = 1
                self.chip.eng[self.arb_line] = 1
        if self.halted:
            return
        if self.div > 1:
            self.div_cnt = (self.div_cnt + 1) % self.div
            if self.div_cnt:
                return
        prev, self.last = self.last, list(s2)  # each engine remembers what it last saw
        if self.stall:
            self.stall -= 1
            self.delay_cycles += 1
            return
        if self.pc >= len(self.prog):
            self.halted = True
            return
        ins = self.prog[self.pc]
        while ins.op == "WRAP":  # hardware wrap, as in RP2040 PIO: no word, no cycle
            self.pc = ins.a["to"]
            ins = self.prog[self.pc]
        if ins.tick:
            if not self.tick_pending:
                self.wait_cycles += 1
                return
            self.tick_pending = 0
        if ins.side is not None:  # a side-set lands with its tick, or at once if it has none
            self.chip.eng[self.side_line] = ins.side
        self.execute(ins, s2, prev)

    def coded(self):
        return self.stuff_n or self.crc_on or self.nrzi or self.manchester or self.arb_at

    def drive(self, line, val):
        self.chip.eng[line] = val
        if self.pair_line is not None:
            self.chip.eng[self.pair_line] = 1 - val

    def wire(self, line, bit):
        """Put a logical bit on the wire through the line coder."""
        if self.lost:
            self.chip.eng[line] = 1
            return
        if self.nrzi:
            if bit == 0:
                self.level ^= 1
            self.drive(line, self.level)
        else:
            self.drive(line, bit)
        self.arb_drv, self.arb_line, self.arb_cnt = bit, line, self.arb_at

    def coded_out(self, line):
        """One tick of coded output. True when a payload bit was consumed."""
        if self.manchester and self.half is not None:  # second half-bit is the complement
            self.drive(line, 1 - self.half)
            self.half = None
            return True
        if self.stuff_pending is not None:
            self.wire(line, self.stuff_pending)
            self.run_val = self.stuff_pending
            self.run_len = 0 if self.stuff_ones else 1
            self.stuff_pending = None
            return False
        bit = self.sbit()
        if self.crc_on:
            if self.msb_first:
                fb = bit ^ ((self.crc >> self.crc_top) & 1)
                self.crc = (self.crc << 1) & ((1 << (self.crc_top + 1)) - 1)
            else:
                fb = bit ^ (self.crc & 1)
                self.crc >>= 1
            if fb:
                self.crc ^= self.poly
        if self.manchester:
            self.drive(line, bit)
            self.half = bit
            self.shift_out()
            return False
        self.wire(line, bit)
        self.shift_out()
        if self.stuff_n:
            if self.stuff_ones:
                self.run_len = self.run_len + 1 if bit else 0
            else:
                self.run_len = self.run_len + 1 if bit == self.run_val else 1
                self.run_val = bit
            if self.run_len == self.stuff_n:
                self.stuff_pending = 0 if self.stuff_ones else 1 - bit
                self.run_len = 0
        return self.stuff_pending is None

    def sbit(self):
        return (self.SR >> 15) & 1 if self.msb_first else self.SR & 1

    def shift_out(self):
        self.SR = ((self.SR << 1) & 0xFFFF) if self.msb_first else self.SR >> 1
        self.BC = max(self.BC - 1, 0)

    def shift_in(self, bit):
        self.SR = ((self.SR << 1) | bit) & 0xFFFF if self.msb_first else (self.SR >> 1) | (bit << 15)
        self.BC = max(self.BC - 1, 0)

    def cond(self, c):
        return {
            "AL": True, "Z": self.Z == 1, "NZ": self.Z == 0, "F": self.F == 1, "NF": self.F == 0,
            "BC0": self.BC == 0, "BCN": self.BC != 0,
        }[c]

    def execute(self, ins, s2, prev):
        op, a = ins.op, ins.a
        chip = self.chip
        next_pc = self.pc + 1
        self.executed += 1
        self.trace.append(self.pc)
        if op == "NOP":
            pass
        elif op == "HALT":
            self.halted = True
        elif op == "SET":
            chip.eng[a["line"]] = a["v"]
        elif op == "MODE":
            chip.src[a["line"]] = a["src"]
        elif op == "MODE2":  # two lines switch on the same edge
            chip.src[a["l1"]] = a["s1"]
            chip.src[a["l2"]] = a["s2"]
        elif op == "WAIT":
            bit, pv = s2[a["line"]], prev[a["line"]]
            met = {"LO": bit == 0, "HI": bit == 1, "RISE": pv == 0 and bit == 1, "FALL": pv == 1 and bit == 0}[a["c"]]
            if not met:
                if self.TO:
                    if self.to_left is None:
                        self.to_left = self.TO
                    self.to_left -= 1
                    if self.to_left > 0:
                        self.wait_cycles += 1
                        return
                    self.F = 1
                    self.to_left = None
                    self.pc = next_pc
                    return
                self.wait_cycles += 1
                return
            self.to_left = None
            if a.get("stamp"):
                self.tstamp = self.t
        elif op == "DLY":
            self.stall = a["n"]
            self.pc = next_pc
            return
        elif op == "DLYR":
            self.stall = self.R[a["r"]]
            self.pc = next_pc
            return
        elif op == "IN":
            self.shift_in(s2[a["line"]])
        elif op == "OUT":
            chip.eng[a["line"]] = self.sbit()
            if not a.get("ns"):  # ns: hold SR, so IN can shift for full duplex on one register
                self.shift_out()
        elif op == "INB":  # fused: sample, shift, loop while bits remain
            self.shift_in(s2[a["line"]])
            if self.BC != 0:
                next_pc = a["to"]
        elif op == "OUTB":  # fused: drive, shift, loop while bits remain
            if self.coded():
                done = self.coded_out(a["line"])
                if not done:
                    next_pc = self.pc  # a stuffed bit or second half-bit: same instruction again
                elif self.BC != 0:
                    next_pc = a["to"]
            else:
                chip.eng[a["line"]] = self.sbit()
                self.shift_out()
                if self.BC != 0:
                    next_pc = a["to"]
        elif op == "LDI":
            self.R[a["r"]] = (self.R[a["r"]] & 0xFF) | (a["hi"] << 8) if "hi" in a else a["v"] & 0xFF
        elif op == "BCI":  # load the bit counter directly
            self.BC = a["n"]
        elif op == "TOI":
            self.TO = a["n"]
        elif op == "SRI":
            self.SR = (a["v"] << a.get("sh", 0)) & 0xFFFF
        elif op == "MOV":
            val = self.read(a["s"])
            self.write(a["d"], val)
        elif op == "ADD":
            val = (self.R[a["r"]] + a["v"]) & 0xFFFF
            self.R[a["r"]] = val
            self.Z = int(val == 0)
        elif op == "XOR":
            val = self.R[a["d"]] ^ self.read(a["s"])
            self.R[a["d"]] = val
            self.Z = int(val == 0)
        elif op == "AND":
            val = self.R[a["d"]] & self.read(a["s"])
            self.R[a["d"]] = val
            self.Z = int(val == 0)
        elif op == "CMP":
            self.Z = int(self.read(a["a"]) == self.read(a["b"]))
        elif op == "CMPI":
            self.Z = int(self.read(a["a"]) == a["v"])
        elif op == "SHR":  # the bit shifted out lands in F
            self.F = self.R[a["r"]] & 1
            self.R[a["r"]] >>= 1
        elif op == "SHL":
            self.F = (self.R[a["r"]] >> 15) & 1
            self.R[a["r"]] = (self.R[a["r"]] << 1) & 0xFFFF
        elif op == "TSTB":  # Z = the bit OUT would drive next
            self.Z = self.sbit()
        elif op == "CALL":
            self.lr = next_pc
            next_pc = a["to"]
        elif op == "RET":
            next_pc = self.lr
        elif op == "CODE":  # configure the line-coding hardware
            for k, v in a.items():
                setattr(self, k, v)
            self.run_len, self.run_val, self.stuff_pending, self.half = 0, None, None, None
        elif op == "CODE2":  # start a CRC partway through a frame, keeping the run state
            for k, v in a.items():
                setattr(self, k, v)
        elif op == "CRCSR":  # load SR from the CRC, optionally inverted
            val = self.crc ^ a.get("xor", 0)
            if a.get("shift"):  # left align a CRC narrower than SR
                val <<= 1
            self.SR = val & 0xFFFF
            self.crc_on = a.get("keep", False)
        elif op == "JMP":
            if self.cond(a["c"]):
                next_pc = a["to"]
        elif op == "DJNZ":
            self.R[a["r"]] = (self.R[a["r"]] - 1) & 0xFFFF
            if self.R[a["r"]] != 0:
                next_pc = a["to"]
        elif op == "PULL":  # non-blocking: F=1 when empty
            if self.fifo_tx:
                self.SR = self.fifo_tx.pop(0)
                self.F = 0
            else:
                self.F = 1
        elif op == "PULLW":  # stalls while empty, so only idle time can vary
            if not self.fifo_tx:
                self.wait_cycles += 1
                return
            self.SR = self.fifo_tx.pop(0)
            if "n" in a:
                self.BC = a["n"]
        elif op == "PUSH":
            self.fifo_rx.append(self.read(a.get("s", "SR")))
        elif op == "TSET":  # start the bit timer; first tick after `first` cycles
            self.tp = a.get("p", self.tp)
            self.tcnt = a.get("first", self.tp)
            self.tick_pending = 0
        elif op == "TSRC":  # tick on an edge of this line (want=1 rising, 0 falling)
            self.tsrc = (a["line"], a["want"])
            self.tick_pending = 0
        elif op == "WAITT":
            if not self.tick_pending:
                self.wait_cycles += 1
                return
            self.tick_pending = 0
        elif op == "RAND":  # one 16-bit Galois LFSR step, in one cycle
            bit = self.lfsr & 1
            self.lfsr >>= 1
            if bit:
                self.lfsr ^= 0xB400
            self.R[a["r"]] = self.lfsr
        elif op == "SIDE":
            self.side_line = a["line"]
        elif op == "MSB":
            self.msb_first = bool(a["v"])
        elif op == "ARM":  # hardware rule: on trigger, after dly, apply src for width, then revert
            chip.arm(a)
        elif op == "MATCH":  # hardware rule: sample a byte, and on a match alter the next frame
            chip.match(a)
        elif op == "PROBE":  # non-blocking read of line state into Z
            self.Z = s2[a["line"]]
        else:
            raise ValueError(op)
        self.pc = next_pc
        self.stall = ins.dly

    def read(self, s):
        if isinstance(s, int):
            return self.R[s]
        return {"SR": self.SR, "BC": self.BC, "TO": self.TO, "LOW": self.SR >> 8}[s]

    def write(self, d, v):
        if isinstance(d, int):
            self.R[d] = v
        elif d == "SR":
            self.SR = v
        elif d == "BC":
            self.BC = v
        elif d == "TO":
            self.TO = v
        else:
            raise ValueError(d)


class Chip:
    """Eight bus lines, four AUX inputs, one or more engines, optional hardware rules."""

    def __init__(self):
        self.src = {x: HIZ for x in range(8)}
        self.eng = {x: 1 for x in range(16)}
        self.out = {x: None for x in range(8)}  # None = released
        self.s1 = [1] * 12
        self.s2 = [1] * 12
        self.prev = [1] * 12
        self.cores = []
        self.rules = []
        self.matchers = []
        self.odrain = set()
        self.cycle = 0
        self.barrel = False
        self.fast_pass = False  # experiment: PASS taps the first synchronizer flop
        self.odb = {}  # pair (low line) -> bridge state, spec section 4.1
        self.holdoff = 8
        self.odb_log = []

    def add(self, core):
        self.cores.append(core)
        return core

    def arm(self, a):
        self.rules.append({"a": dict(a), "state": "armed", "cnt": 0, "saved": None})

    def match(self, a):
        self.matchers.append({"a": dict(a), "state": "idle", "cnt": 0, "bits": 0, "val": 0, "armed": False, "saved": None})

    def run_matchers(self):
        for m in self.matchers:
            a = m["a"]
            line, bit = a["trig"], a["bit"]
            fell = self.prev[line] == 1 and self.s2[line] == 0
            if m["state"] == "idle":
                if fell:
                    m["cnt"], m["bits"], m["val"] = bit + bit // 2, 0, 0
                    m["state"] = "byte"
                    if m["armed"]:
                        m["armed"] = False
                        m["act"] = a["after"]
            elif m["state"] == "byte":
                m["cnt"] -= 1
                if m["cnt"] <= 0:
                    m["val"] |= self.s2[line] << m["bits"]
                    m["bits"] += 1
                    m["cnt"] = bit
                    if m["bits"] == 8:
                        m["armed"] = (m["val"] & a["mask"]) == a["value"]
                        m["state"] = "stop"
                        m["cnt"] = bit // 2
            elif m["state"] == "stop":
                m["cnt"] -= 1
                if m["cnt"] <= 0:
                    m["state"] = "idle"
            if m.get("act") is not None:
                if m["act"] == 0:
                    m["saved"] = self.src[a["line"]]
                    self.src[a["line"]] = a["src"]
                    m["act"] = None
                    m["end"] = a["width"]
                else:
                    m["act"] -= 1
            elif m.get("end") is not None:
                m["end"] -= 1
                if m["end"] <= 0:
                    self.src[a["line"]] = m["saved"]
                    m["end"] = None

    def run_rules(self):
        self.run_matchers()
        for rule in self.rules:
            a = rule["a"]
            line = a["trig"]
            if rule["state"] == "armed":
                pv, bit = self.prev[line], self.s2[line]
                if (a["c"] == "FALL" and pv == 1 and bit == 0) or (a["c"] == "RISE" and pv == 0 and bit == 1):
                    rule["state"] = "delay"
                    rule["cnt"] = a["after"]
            if rule["state"] == "delay":
                if rule["cnt"] == 0:
                    rule["saved"] = self.src[a["line"]]
                    self.src[a["line"]] = a["src"]
                    rule["state"] = "pulse"
                    rule["cnt"] = a["width"]
                else:
                    rule["cnt"] -= 1
            elif rule["state"] == "pulse":
                rule["cnt"] -= 1
                if rule["cnt"] <= 0:
                    self.src[a["line"]] = rule["saved"]
                    rule["state"] = "hold"
                    rule["cnt"] = a.get("hold", 0)
            elif rule["state"] == "hold":  # ignore edges inside this frame
                rule["cnt"] -= 1
                if rule["cnt"] <= 0:
                    rule["state"] = "armed" if a.get("rearm") else "done"

    def run_bridges(self):
        """Open-drain bridge FSM from spec section 4.1. Sides are line x and line x^4."""
        for a in range(4):
            b = a + 4
            if self.src[a] != ODB and self.src[b] != ODB:
                continue
            st = self.odb.setdefault(a, {"state": "IDLE", "cnt": 0})
            la, lb = self.s2[a], self.s2[b]
            if st["state"] == "IDLE":
                if la == 0:  # both low at once picks A
                    st["state"] = "A_HOLDS"
                elif lb == 0:
                    st["state"] = "B_HOLDS"
            elif st["state"] == "A_HOLDS":
                if la == 1:
                    st["state"], st["cnt"] = "HOLDOFF", self.holdoff
            elif st["state"] == "B_HOLDS":
                if lb == 1:
                    st["state"], st["cnt"] = "HOLDOFF", self.holdoff
            elif st["state"] == "HOLDOFF":
                st["cnt"] -= 1
                if st["cnt"] <= 0:
                    st["state"] = "IDLE"

    def tick(self, inputs):
        """Advance one clock. `inputs` holds the level at each of the 12 input pins."""
        self.run_rules()
        self.run_bridges()
        if self.barrel:  # thread interleaving: each engine executes every second cycle
            for i, core in enumerate(self.cores):
                if self.cycle % len(self.cores) == i:
                    core.step(self.s2, self.prev)
                else:
                    core.timer()
        else:
            for core in self.cores:
                core.step(self.s2, self.prev)
        for x in range(8):
            partner = x ^ 4
            source = self.src[x]
            if source == HIZ:
                val = None
            elif source == ENG:
                val = self.eng[x]
            elif source == PASS:
                val = (self.s1 if self.fast_pass else self.s2)[partner]
            elif source == INV:
                val = 1 - (self.s1 if self.fast_pass else self.s2)[partner]
            elif source == F0:
                val = 0
            elif source == F1:
                val = 1
            elif source == ODB:
                st = self.odb.get(min(x, partner), {"state": "IDLE"})["state"]
                drive_low = (st == "A_HOLDS" and x >= 4) or (st == "B_HOLDS" and x < 4)
                val = 0 if drive_low else None
            if x in self.odrain and val == 1:
                val = None
            self.out[x] = val
        self.prev = list(self.s2)
        self.s2 = list(self.s1)
        self.s1 = list(inputs)
        self.cycle += 1


def run(chip, cycles, stim=None, bus=None):
    """Clock the chip. `stim(t)` gives external pin levels; `bus` adds a wired bus model.

    Returns the per-cycle trace of the eight line outputs, with None for released.
    """
    trace = []
    levels = [1] * 12
    for t in range(cycles):
        ext = stim(t) if stim else {}
        levels = [1] * 12
        for k, v in ext.items():
            levels[k] = v
        if bus:
            levels = bus(t, chip.out, levels)
        chip.tick(levels)
        trace.append(dict(chip.out))
    return trace
