# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Cycle-exact golden model of SPLICE, working from encoded instruction words.

It implements docs/architecture.md section 5 and is written separately from the RTL. Tests run both
on the same programs and inputs, and compare every output pin on every cycle.

step() advances one clock: pass the pin levels sampled by that clock edge, and get back the outputs
after it.
"""
from .isa import decode

MASK = 0xFFFF


class Context:
    def __init__(self, pc=0):
        self.pc = pc
        self.R = [0, 0, 0, 0]
        self.SR = self.BC = self.TO = self.Z = self.F = 0
        self.M = 0  # missed tick, until JMP M reads it
        self.stall = 0
        self.to_left = None
        self.halted = False
        self.lr = 0
        self.msb = False
        self.side_line = 15
        self.lfsr = 0xACE1
        self.tp = self.tcnt = 0
        self.since = 0xFFFF  # cycles since the last tick, saturating. TCAP reads the latched interval
        self.tint = 0xFFFF
        self.pending = False
        self.tick_now = False
        self.missed = False
        self.tsrc_line, self.tsrc_want = 0, 3
        self.poly = 0
        self.lastb = 0  # the bit (lane group) most recently shifted by OUT, OUTB, IN or INB, for CRC
        self.ldp = None  # LDP waiting for its word: (register, address)
        self.lanes = 1  # LANES: how many lines one OUT, IN, OUTB or INB moves, 1, 2, 4 or 8
        self.last = [1] * 12
        # CODE, the line coder (v2): config, then the run of data bits, Manchester's half, NRZI receive level
        self.cd_nrzi = self.cd_kind = self.cd_n = self.cd_man = self.cd_pol = self.cd_diff = self.cd_sync = 0
        self.run = self.runv = self.half = 0
        self.rxp = 0


class Splice:
    FIFO_DEPTH = 8
    LINK_DEPTH = 4  # the links between contexts
    NCTX = 4  # contexts in hardware. The first `nctx` of them take turns, one slot each

    @property
    def two(self):
        return self.nctx > 1

    @two.setter
    def two(self, value):  # older callers: True means two contexts
        self.nctx = 2 if value else 1

    @property
    def ctx1_pc(self):
        return self.ctx_pc[0]

    @ctx1_pc.setter
    def ctx1_pc(self, value):
        self.ctx_pc[0] = value

    def __init__(self):
        self.prog = [0] * 512
        self.coverage = {}
        self.reset()

    def cover(self, name):
        self.coverage[name] = self.coverage.get(name, 0) + 1

    def reset(self):
        self.nctx = 1
        self.ctx_pc = [0, 0, 0]       # contexts 1-3
        self.pull_sel = [0] * 4       # per context: 0 host, 1 link 0, 2 link 1, 3 host
        self.push_sel = [0] * 4
        self.odrain = 0
        self.fast = 0
        self.holdoff = 64
        self.cap_watch = 0x3210
        self.cap_prescale = 0
        self.cap_mode = 0
        self.cap_post = 0
        self.cap = [None] * 512
        self.trig_s1 = self.trig_s2 = self.trig_prev = 0
        self.cap_clear()
        self.s1 = [1] * 12
        self.s2 = [1] * 12
        self.prev = [1] * 12
        self.h2e, self.e2h = [], []
        self.links = [[], []]
        self.stop()

    def stop(self):
        """Host STOP: lines released, FIFOs cleared."""
        self.h2e, self.e2h = [], []
        self.cap_active = False  # results stay readable until the next START
        self._clear_run()

    def _clear_run(self):
        self.running = False
        self.prim = False
        self.cur = 0
        self.ctx = [Context() for _ in range(self.NCTX)]
        self.links = [[], []]
        self.eng = [1] * 8 + [0] * 4
        self.src = [0] * 8
        self.bridge = [["IDLE", 0] for _ in range(4)]
        self.out = [0] * 8
        self.oe = [0] * 8
        self.irq = 0
        self.trig = 0

    def cap_clear(self):
        self.cap_active = self.cap_wrapped = self.cap_triggered = False
        self.cap_ptr = self.cap_dt = self.cap_pre = self.cap_post_left = 0
        self.cap_last = 0
        self.cap_first = False

    def capture_step(self, on, off, mark):
        """Capture unit, architecture.md section 6. Runs after the engine, on synchronized lines."""
        watch = [(self.cap_watch >> (4 * i)) & 15 for i in range(4)]
        states = sum(self.line(n) << i for i, n in enumerate(watch))
        trig_rise = self.trig_s2 and not self.trig_prev and self.running
        if not self.cap_active:
            if on or ((self.cap_mode >> 1) & 1 and trig_rise):
                self.cap_active, self.cap_first, self.cap_dt, self.cap_pre = True, True, 0, 0
            return
        tick = self.cap_pre == self.cap_prescale
        self.cap_pre = 0 if tick else self.cap_pre + 1
        ring, mark_trig = self.cap_mode & 1, (self.cap_mode >> 2) & 1
        change = states != self.cap_last
        repeat_due = self.cap_dt == 0xFFD
        trigger = ring and not self.cap_triggered and (trig_rise or (mark_trig and mark))
        write = not off and (self.cap_first or change or mark or repeat_due)
        entry = 0 if self.cap_first else self.cap_dt if change else 0xFFE if mark else 0xFFF
        clears = self.cap_first or change or (repeat_due and not mark)
        self.cap_dt = (0 if clears else self.cap_dt) + int(tick)
        remaining = self.cap_post_left if self.cap_triggered else self.cap_post
        if trigger:
            self.cap_triggered, self.cap_post_left = True, self.cap_post
        if write:
            self.cap[self.cap_ptr] = (states << 12) | entry
            self.cap_last, self.cap_first = states, False
            at_end = self.cap_ptr == 511
            self.cap_ptr = (self.cap_ptr + 1) & 511
            if at_end:
                self.cap_wrapped = True
                if not ring:
                    self.cap_active = False
            if self.cap_triggered:
                if remaining == 0:
                    self.cap_active = False
                else:
                    self.cap_post_left = remaining - 1
        if off:
            self.cap_active = False

    def cap_host_control(self, value):
        """Host write to register 10."""
        self.cap_mode = value & 7
        self.cap_host_cmd = [bool(value & 0x80), bool(value & 0x40), False]

    def load(self, words, base=0):
        self.prog[base:base + len(words)] = list(words)

    def start(self):
        """Host START: anything the host already pushed stays in the FIFO."""
        self.e2h = []
        self._clear_run()
        self.cap_clear()
        self.ctx = [Context(pc) for pc in [0] + self.ctx_pc]
        self.running, self.prim = True, True

    # Host side of the FIFOs
    def host_push(self, word):
        if len(self.h2e) < self.FIFO_DEPTH:
            self.h2e.append(word & MASK)

    def host_pop(self):
        return self.e2h.pop(0) if self.e2h else None

    # Clock
    def step(self, ui=0xFF, uio=0xFF):
        inputs = [(uio >> i) & 1 for i in range(8)] + [(ui >> i) & 1 for i in range(4, 8)]
        self.trig = 0
        self.cap_cmd = getattr(self, "cap_host_cmd", [False, False, False])  # on, off, mark
        self.cap_host_cmd = [False, False, False]
        for c in self.ctx:
            self.timer(c)
        if self.running:
            if self.prim:
                self.prim = False
                for c in self.ctx:
                    c.last = list(self.s2)
            else:
                c = self.ctx[self.cur]
                self.exec_slot(c)
                self.cur = (self.cur + 1) % self.nctx
        for c in self.ctx:  # a missed tick shows next cycle and beats JMP M clearing it
            if c.missed:
                c.M = 1
        self.capture_step(*self.cap_cmd)
        self.run_bridges()
        for x in range(8):
            partner = x ^ 4
            level = (self.s1 if (self.fast >> x) & 1 else self.s2)[partner]
            src = self.src[x]
            val = {0: None, 1: self.eng[x], 2: level, 3: 1 - level, 4: 0, 5: 1}.get(src)
            if src == 6 and self.bridge[x & 3][0] == ("B_HOLDS" if x < 4 else "A_HOLDS"):
                val = 0
            if val == 1 and (self.odrain >> x) & 1:
                val = None
            self.oe[x] = 0 if val is None else 1
            self.out[x] = 0 if val is None else val
        self.prev, self.s2, self.s1 = self.s2, self.s1, inputs
        self.trig_prev, self.trig_s2, self.trig_s1 = self.trig_s2, self.trig_s1, (ui >> 3) & 1
        return self.pins()

    def run_bridges(self):
        """Open-drain bridge, architecture.md section 4.1. Sides are line p and line p + 4."""
        for p, st in enumerate(self.bridge):
            if self.src[p] != 6 and self.src[p + 4] != 6:
                st[0] = "IDLE"
                continue
            line_a, line_b = self.s2[p], self.s2[p + 4]
            if st[0] == "IDLE":
                if not line_a:
                    st[0] = "A_HOLDS"  # both low together: A wins
                elif not line_b:
                    st[0] = "B_HOLDS"
            elif st[0] == "A_HOLDS" and line_a or st[0] == "B_HOLDS" and line_b:
                st[0], st[1] = "HOLDOFF", self.holdoff
            elif st[0] == "HOLDOFF":
                if st[1] <= 1:
                    st[0] = "IDLE"
                else:
                    st[1] -= 1

    def pins(self):
        aux = sum(self.eng[8 + i] << i for i in range(4))
        uo = (aux << 4) | ((1 if self.running else 0) << 3) | (self.trig << 2) | (self.irq << 1)
        return {"uo": uo, "out": sum(v << i for i, v in enumerate(self.out)), "oe": sum(v << i for i, v in enumerate(self.oe))}

    def timer(self, c):
        c.tick_now = False
        if c.tsrc_want != 3:
            bit, was = self.line(c.tsrc_line), self.prev[c.tsrc_line] if c.tsrc_line < 12 else 1
            c.tick_now = bit != was and (c.tsrc_want == 2 or bit == c.tsrc_want)
        elif c.tp:
            # CODE sync: an edge on the TSRC line re-phases the timer to half a period, so the next tick
            # lands mid-bit. A tick due this cycle still fires
            line = c.tsrc_line
            edge = c.cd_sync and self.line(line) != (self.prev[line] if line < 12 else 1)
            if c.tcnt <= 1:
                c.tick_now = True
            c.tcnt = (c.tp >> 1) if edge else c.tp if c.tcnt <= 1 else c.tcnt - 1
            if edge:
                self.cover("CODE.sync")
        if c.tick_now:  # latch the time between this tick and the one before, so firmware can read it late
            c.tint, c.since = min(c.since + 1, 0xFFFF), 0
        else:
            c.since = min(c.since + 1, 0xFFFF)
        c.missed = c.tick_now and c.pending
        if c.tick_now:
            c.pending = True

    def line(self, n):
        return self.s2[n] if n < 12 else 1

    def drive(self, line, value):
        if line < 12:
            self.eng[line] = value

    def sbit(self, c):
        return (c.SR >> 15) & 1 if c.msb else c.SR & 1

    def group_out(self, c):
        """The next lane group from SR: the top W bits in MSB order, the bottom W in LSB order."""
        w = c.lanes
        return (c.SR >> (16 - w)) & ((1 << w) - 1) if c.msb else c.SR & ((1 << w) - 1)

    def group_in(self, c, line):
        """W lines from the lane-aligned base: line base + k is bit k of the group."""
        w = c.lanes
        base = line & ~(w - 1)
        return sum(self.line(base + k) << k for k in range(w))

    def drive_group(self, c, line, group):
        w = c.lanes
        base = line & ~(w - 1)
        for k in range(w):
            self.drive(base + k, (group >> k) & 1)

    # ------------------------------------------------------------------ CODE, the line coder (v2)
    def coded_tx(self, c, op, f):
        """OUT with shift and OUTB at one lane, with any of NRZI, stuffing or Manchester on."""
        return (c.lanes == 1 and (c.cd_nrzi or c.cd_n or c.cd_man)
                and (op == "OUTB" or (op == "OUT" and not f["ns"])))

    def coded_rx(self, c, op):
        """IN and INB at one lane, with NRZI, stuffing, Manchester or biphase-mark on."""
        return c.lanes == 1 and (c.cd_nrzi or c.cd_n or c.cd_man) and op in ("IN", "INB")

    @staticmethod
    def stuff_due(c):
        return c.cd_n != 0 and c.run == c.cd_n

    @staticmethod
    def run_after(c, d, stuffed):
        """The run of data bits after bit d: ones for kind 0 (USB, HDLC), equal bits for kind 1 (CAN)."""
        if stuffed:
            return (1 if c.cd_kind else 0), d
        if c.cd_kind:
            return (min(c.run + 1, 7) if c.run and d == c.runv else 1), d
        return (min(c.run + 1, 7) if d else 0), c.runv

    def code_drive(self, c, line, v):
        self.drive(line, v)
        if c.cd_diff:  # the complement on the other line of the pair: USB D+ and D-, RS-485
            self.drive(line ^ 1, 1 - v)

    def code_tx(self, c, f, cur):
        """Drives the coded line. Returns True when the instruction repeats: a stuff bit or a first half-bit."""
        b = self.sbit(c)
        if c.cd_man:
            if c.cd_nrzi:  # biphase: every bit starts with a change, and a mark (or a space, pol) has a second
                v = 1 - cur if not c.half else cur ^ b ^ c.cd_pol
            else:
                first = (b if c.cd_pol else 1 - b)
                v = first if not c.half else 1 - first
            self.code_drive(c, f["line"], v)
            c.half = 1 - c.half
            if c.half:
                return True
            c.lastb = b
            return False
        stuffed = self.stuff_due(c)
        d = (1 - c.runv if c.cd_kind else 0) if stuffed else b
        self.code_drive(c, f["line"], (cur if d else 1 - cur) if c.cd_nrzi else d)
        c.run, c.runv = self.run_after(c, d, stuffed)
        if not stuffed:
            c.lastb = b
        return stuffed

    def code_rx(self, c, f):
        """Samples and decodes one bit. Returns (repeat, bit): repeat for a stuff bit, which sets F if wrong,
        or for a first half-bit. A half-bit pair that breaks the code also sets F: Manchester's two halves
        equal, or biphase without the change at a bit's start."""
        s = self.line(f["line"])
        if c.cd_man:
            same = s == c.rxp
            if c.cd_nrzi:
                bad, d = same and not c.half, int(not same) ^ c.cd_pol
            else:
                bad, d = same and c.half, c.rxp if c.cd_pol else s
            if bad:
                c.F = 1
            c.rxp, c.half = s, 1 - c.half
            return bool(c.half), d
        d = int(s == c.rxp) if c.cd_nrzi else s
        c.rxp = s
        stuffed = self.stuff_due(c)
        if stuffed and d != (1 - c.runv if c.cd_kind else 0):
            c.F = 1
        c.run, c.runv = self.run_after(c, d, stuffed)
        return stuffed, d

    def shift_out(self, c):
        w = c.lanes
        c.SR = (c.SR << w) & MASK if c.msb else c.SR >> w
        c.BC = max(c.BC - 1, 0)

    def shift_in(self, c, group):
        w = c.lanes
        c.SR = ((c.SR << w) | group) & MASK if c.msb else (c.SR >> w) | (group << (16 - w))
        c.BC = max(c.BC - 1, 0)

    def read(self, c, code):
        if code < 4:
            return c.R[code]
        return {4: c.SR, 5: c.BC, 6: c.TO, 7: c.SR >> 8}[code]

    def fifo_in(self, c):
        """The FIFO this context's PULL reads: the host's, or a link fed by another context."""
        sel = self.pull_sel[self.ctx.index(c)]
        return self.links[sel - 1] if sel in (1, 2) else self.h2e

    def depth(self, q):
        return self.LINK_DEPTH if q is not self.h2e and q is not self.e2h else self.FIFO_DEPTH

    def fifo_out(self, c):
        sel = self.push_sel[self.ctx.index(c)]
        return self.links[sel - 1] if sel in (1, 2) else self.e2h

    def exec_slot(self, c):
        prev, c.last = c.last, list(self.s2)
        eng_before = list(self.eng)  # the registered outputs, before this slot's side-set
        if c.halted:
            return
        if c.stall:
            c.stall -= 1
            if c.ldp:  # LDP's word arrives in the slot after it, in place of an instruction
                d, addr = c.ldp
                c.R[d], c.ldp = self.prog[addr], None
            return
        op, f = decode(self.prog[c.pc & 0x1FF])  # 512-word program macro
        if f.get("tick"):
            if not c.pending:
                return
            c.pending = False
        if f.get("side", 0) & 2:
            self.drive(c.side_line, f["side"] & 1)
        next_pc = (c.pc + 1) & 0x1FF
        dly = f.get("dly", 0)

        if op == "SET":
            self.drive(f["line"], f["v"])
        elif op == "MODE":
            self.src[f["line"]] = f["src"]
        elif op in ("OUT", "OUTB") and self.coded_tx(c, op, f):
            cur = eng_before[f["line"]] if f["line"] < 12 else 0
            self.cover(("CODE.bmc" if c.cd_nrzi else "CODE.man") if c.cd_man else "CODE.stuff.tx" if self.stuff_due(c) else
                       "CODE.nrzi.tx" if c.cd_nrzi else "CODE.data.tx")
            if self.code_tx(c, f, cur):
                next_pc = c.pc  # a stuff bit or a first half-bit: the same instruction again
            else:
                self.shift_out(c)
                if op == "OUTB" and c.BC:
                    next_pc = (c.pc - f["rel"]) & 0x1FF
        elif op in ("IN", "INB") and self.coded_rx(c, op):
            f_before = c.F
            stuffed, d = self.code_rx(c, f)
            if c.cd_man:
                self.cover(("CODE.bmc.rx" if c.cd_nrzi else "CODE.man.rx") + (".h1" if stuffed else ".h2"))
                if c.F and not f_before:
                    self.cover("CODE.codeerr")
            else:
                self.cover("CODE.stuff.rx" if stuffed else "CODE.nrzi.rx" if c.cd_nrzi else "CODE.data.rx")
            if stuffed and c.F and not f_before and not c.cd_man:
                self.cover("CODE.stufferr")
            if stuffed:
                next_pc = c.pc
            else:
                c.lastb = d
                self.shift_in(c, d)
                if op == "INB" and c.BC:
                    next_pc = (c.pc - f["rel"]) & 0x1FF
        elif op == "CODE":
            c.cd_nrzi, c.cd_kind, c.cd_n = f["nrzi"], f["kind"], f["n"]
            c.cd_man, c.cd_pol, c.rxp, c.cd_diff = f["man"], f["pol"], f["lvl"], f["diff"]
            c.cd_sync = f["sync"]
            c.run = c.runv = c.half = 0
        elif op == "OUT":
            c.lastb = self.group_out(c)
            self.drive_group(c, f["line"], c.lastb)
            if not f["ns"]:
                self.shift_out(c)
        elif op == "IN":
            c.lastb = self.group_in(c, f["line"])
            self.shift_in(c, c.lastb)
        elif op == "OUTB":
            c.lastb = self.group_out(c)
            self.drive_group(c, f["line"], c.lastb)
            self.shift_out(c)
            if c.BC:
                next_pc = (c.pc - f["rel"]) & 0x1FF
        elif op == "INB":
            c.lastb = self.group_in(c, f["line"])
            self.shift_in(c, c.lastb)
            if c.BC:
                next_pc = (c.pc - f["rel"]) & 0x1FF
        elif op == "WAIT":
            n = f["line"]
            bit, was = self.line(n), prev[n] if n < 12 else 1
            met = {0: bit == 0, 1: bit == 1, 2: was == 0 and bit == 1, 3: was == 1 and bit == 0}[f["c"]]
            if met:
                c.to_left = None
                self.cover(f"WAIT.{f['c']}.met")
            elif c.TO:
                left = (c.to_left if c.to_left is not None else c.TO) - 1
                if left > 0:
                    c.to_left = left
                    return
                c.F, c.to_left = 1, None
                self.cover(f"WAIT.{f['c']}.timeout")
            else:
                return
        elif op == "WAITT":
            if not c.pending:
                return
            c.pending = False
        elif op == "TSET":
            c.tp = c.tcnt = f["p"]
            c.pending = False
        elif op == "TSETR":  # the full 16 bits, where TSET's constant has 13
            c.tp = c.tcnt = self.read(c, f["s"])
            c.pending = False
        elif op == "TPHASE":  # the counter has 17 bits, so one and a half periods fit
            c.tcnt = {0: c.tp - 1, 1: (c.tp >> 1) - 1, 2: c.tp + (c.tp >> 1) - 1, 3: c.R[1]}[f["mode"]] & 0x1FFFF
            c.pending = False
        elif op == "TSRC":
            c.tsrc_line, c.tsrc_want = f["line"], f["want"]
            c.pending = False
        elif op == "DLY":
            dly = f["n"]
        elif op == "DLYR":
            dly = c.R[f["r"]]
        elif op == "PULL":
            src = self.fifo_in(c)
            if src:
                c.SR, c.F = src.pop(0), 0
            else:
                c.F = 1
        elif op == "PULLW":
            src = self.fifo_in(c)
            if not src:
                return
            c.SR, c.BC = src.pop(0), f["n"]
        elif op == "PUSH":
            dst = self.fifo_out(c)
            if len(dst) < self.depth(dst):
                dst.append(c.SR)
            else:
                c.F = 1
        elif op == "BCI":
            c.BC = f["n"]
        elif op == "SRI":
            c.SR = (f["v"] << f["sh"]) & MASK
        elif op == "MSB":
            c.msb = bool(f["v"])
        elif op == "SIDE":
            c.side_line = f["line"]
        elif op == "LDI":
            c.R[f["r"]] = ((c.R[f["r"]] & 0xFF) | (f["v"] << 8)) if f["hl"] else f["v"]
        elif op == "MOV":
            value, d = self.read(c, f["s"]), f["d"]
            if d < 4:
                c.R[d] = value
            elif d == 4:
                c.SR = value
            elif d == 5:
                c.BC = value & 0x1F
            elif d == 6:
                c.TO = value
            else:
                c.lfsr = value
        elif op == "ADD":
            c.R[f["r"]] = (c.R[f["r"]] + f["v"]) & MASK
            c.Z = int(c.R[f["r"]] == 0)
        elif op == "ADDR":
            c.R[f["d"]] = (c.R[f["d"]] + self.read(c, f["s"])) & MASK
            c.Z = int(c.R[f["d"]] == 0)
        elif op == "POLY":
            c.poly = self.read(c, f["s"])
        elif op == "CRC":  # one step on the bit last shifted, or a whole word of one lane from SR
            v, poly, w = c.R[f["r"]], c.poly, c.lanes
            if f["word"]:
                # One lane's bits in SR, oldest first, in one cycle: 4 steps at stride 4, or 2 at stride 8
                # with 8 lanes. Below 4 lanes it still uses stride 4, which only makes sense for 4 lanes.
                stride = max(w, 4)
                n = 16 // stride
                if c.msb:
                    bits = [(c.SR >> (16 - stride * (j + 1) + f["lane"])) & 1 for j in range(n)]
                else:
                    bits = [(c.SR >> (stride * j + f["lane"])) & 1 for j in range(n)]
            else:
                bits = [(c.lastb >> f["lane"]) & 1]
            for b in bits:
                if f["dir"]:
                    fb = ((v >> 15) & 1) ^ b
                    v = ((v << 1) & MASK) ^ (poly if fb else 0)
                else:
                    fb = (v & 1) ^ b
                    v = (v >> 1) ^ (poly if fb else 0)
            c.R[f["r"]] = v
            c.Z = int(v == 0)
        elif op == "LANES":
            c.lanes = 1 << f["w"]
        elif op == "LDP":  # a program word as data, at the address in Rs. The read takes a second slot
            c.ldp = (f["d"], c.R[f["s"]] & 0x1FF)
            dly = 1
        elif op == "JMPR":
            next_pc = self.read(c, f["s"]) & 0x1FF
        elif op == "XOR":
            c.R[f["d"]] ^= self.read(c, f["s"])
            c.Z = int(c.R[f["d"]] == 0)
        elif op == "AND":
            c.R[f["d"]] &= self.read(c, f["s"])
            c.Z = int(c.R[f["d"]] == 0)
        elif op == "CMP":
            c.Z = int(self.read(c, f["a"]) == self.read(c, f["b"]))
        elif op == "CMPU":  # unsigned: Z if equal, F if a < b
            a, b = self.read(c, f["a"]), self.read(c, f["b"])
            c.Z, c.F = int(a == b), int(a < b)
        elif op == "TCAP":  # with .t: wait for a tick, then read the interval that ended at it
            c.R[f["r"]] = c.tint
        elif op == "CMPI":
            c.Z = int(c.R[f["a"]] == f["v"])
        elif op == "SHR":
            c.F = c.R[f["r"]] & 1
            c.R[f["r"]] >>= 1
        elif op == "SHL":
            c.F = (c.R[f["r"]] >> 15) & 1
            c.R[f["r"]] = (c.R[f["r"]] << 1) & MASK
        elif op == "RAND":
            low = c.lfsr & 1
            c.lfsr = (c.lfsr >> 1) ^ (0xB400 if low else 0)
            c.R[f["r"]] = c.lfsr
        elif op == "TSTB":
            c.Z = self.sbit(c)
        elif op == "PROBE":
            c.Z = self.line(f["line"])
        elif op == "JMP":
            taken = {0: True, 1: c.Z == 1, 2: c.Z == 0, 3: c.F == 1, 4: c.F == 0, 5: c.BC == 0, 6: c.BC != 0, 7: c.M == 1}[f["c"]]
            if f["c"] == 7:
                c.M = 0
            self.cover(f"JMP.{f['c']}.{'taken' if taken else 'not'}")
            if taken:
                next_pc = f["to"]
        elif op == "DJNZ":
            c.R[f["r"]] = (c.R[f["r"]] - 1) & MASK
            if c.R[f["r"]]:
                next_pc = f["to"]
        elif op == "CALL":
            c.lr, next_pc = next_pc, f["to"]
        elif op == "RET":
            next_pc = c.lr
        elif op == "IRQ":
            self.irq = 1
        elif op == "TRIG":
            self.trig = 1
        elif op == "HALT":
            c.halted = True
        elif op == "CAPON":
            self.cap_cmd[0] = True
        elif op == "CAPOFF":
            self.cap_cmd[1] = True
        elif op == "CAPMARK":
            self.cap_cmd[2] = True
        self.cover(op)
        if op == "MODE":
            self.cover(f"MODE.{f['src']}")
        elif op == "PUSH":
            self.cover(f"PUSH.{'full' if c.F and len(self.fifo_out(c)) >= self.depth(self.fifo_out(c)) else 'ok'}")
        elif op == "TPHASE":
            self.cover(f"TPHASE.{f['mode']}")
        elif op == "TSRC":
            self.cover(f"TSRC.{f['want']}")
        if op in ("OUT", "IN", "OUTB", "INB"):
            self.cover(f"{op}.w{c.lanes}")
        elif op == "CRC" and f["word"]:
            self.cover(f"CRCW.w{c.lanes}")
        if f.get("tick"):
            self.cover(f"{op}.t")
        c.pc = next_pc
        c.stall = dly
