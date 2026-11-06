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
        self.stall = 0
        self.to_left = None
        self.halted = False
        self.lr = 0
        self.msb = False
        self.side_line = 15
        self.lfsr = 0xACE1
        self.tp = self.tcnt = 0
        self.pending = False
        self.tick_now = False
        self.missed = False
        self.tsrc_line, self.tsrc_want = 0, 3
        self.last = [1] * 12


class Splice:
    FIFO_DEPTH = 4

    def __init__(self):
        self.prog = [0] * 512
        self.coverage = {}
        self.reset()

    def reset(self):
        self.two = False
        self.ctx1_pc = 0
        self.odrain = 0
        self.fast = 0
        self.s1 = [1] * 12
        self.s2 = [1] * 12
        self.prev = [1] * 12
        self.h2e, self.e2h = [], []
        self.stop()

    def stop(self):
        """Host STOP: lines released, FIFOs cleared."""
        self.h2e, self.e2h = [], []
        self._clear_run()

    def _clear_run(self):
        self.running = False
        self.prim = False
        self.cur = 0
        self.ctx = [Context(), Context()]
        self.eng = [1] * 8 + [0] * 4
        self.src = [0] * 8
        self.out = [0] * 8
        self.oe = [0] * 8
        self.irq = 0
        self.trig = 0

    def load(self, words, base=0):
        self.prog[base:base + len(words)] = list(words)

    def start(self):
        """Host START: anything the host already pushed stays in the FIFO."""
        self.e2h = []
        self._clear_run()
        self.ctx = [Context(0), Context(self.ctx1_pc)]
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
                if self.two:
                    self.cur ^= 1
        for c in self.ctx:  # a missed tick shows next cycle and beats an instruction writing F
            if c.missed:
                c.F = 1
        for x in range(8):
            partner = x ^ 4
            level = (self.s1 if (self.fast >> x) & 1 else self.s2)[partner]
            src = self.src[x]
            val = {0: None, 1: self.eng[x], 2: level, 3: 1 - level, 4: 0, 5: 1}.get(src)
            if val == 1 and (self.odrain >> x) & 1:
                val = None
            self.oe[x] = 0 if val is None else 1
            self.out[x] = 0 if val is None else val
        self.prev, self.s2, self.s1 = self.s2, self.s1, inputs
        return self.pins()

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
            if c.tcnt <= 1:
                c.tcnt = c.tp
                c.tick_now = True
            else:
                c.tcnt -= 1
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

    def shift_out(self, c):
        c.SR = (c.SR << 1) & MASK if c.msb else c.SR >> 1
        c.BC = max(c.BC - 1, 0)

    def shift_in(self, c, bit):
        c.SR = ((c.SR << 1) | bit) & MASK if c.msb else (c.SR >> 1) | (bit << 15)
        c.BC = max(c.BC - 1, 0)

    def read(self, c, code):
        if code < 4:
            return c.R[code]
        return {4: c.SR, 5: c.BC, 6: c.TO, 7: c.SR >> 8}[code]

    def exec_slot(self, c):
        prev, c.last = c.last, list(self.s2)
        if c.halted:
            return
        if c.stall:
            c.stall -= 1
            return
        op, f = decode(self.prog[c.pc & 0xFF])  # 256-word program macro
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
        elif op == "OUT":
            self.drive(f["line"], self.sbit(c))
            if not f["ns"]:
                self.shift_out(c)
        elif op == "IN":
            self.shift_in(c, self.line(f["line"]))
        elif op == "OUTB":
            self.drive(f["line"], self.sbit(c))
            self.shift_out(c)
            if c.BC:
                next_pc = (c.pc - f["rel"]) & 0x1FF
        elif op == "INB":
            self.shift_in(c, self.line(f["line"]))
            if c.BC:
                next_pc = (c.pc - f["rel"]) & 0x1FF
        elif op == "WAIT":
            n = f["line"]
            bit, was = self.line(n), prev[n] if n < 12 else 1
            met = {0: bit == 0, 1: bit == 1, 2: was == 0 and bit == 1, 3: was == 1 and bit == 0}[f["c"]]
            if met:
                c.to_left = None
            elif c.TO:
                left = (c.to_left if c.to_left is not None else c.TO) - 1
                if left > 0:
                    c.to_left = left
                    return
                c.F, c.to_left = 1, None
            else:
                return
        elif op == "WAITT":
            if not c.pending:
                return
            c.pending = False
        elif op == "TSET":
            c.tp = c.tcnt = f["p"]
            c.pending = False
        elif op == "TPHASE":
            c.tcnt = {0: c.tp - 1, 1: (c.tp >> 1) - 1, 2: c.tp + (c.tp >> 1) - 1, 3: c.R[1]}[f["mode"]] & 0x3FFF
            c.pending = False
        elif op == "TSRC":
            c.tsrc_line, c.tsrc_want = f["line"], f["want"]
            c.pending = False
        elif op == "DLY":
            dly = f["n"]
        elif op == "DLYR":
            dly = c.R[f["r"]]
        elif op == "PULL":
            if self.h2e:
                c.SR, c.F = self.h2e.pop(0), 0
            else:
                c.F = 1
        elif op == "PULLW":
            if not self.h2e:
                return
            c.SR, c.BC = self.h2e.pop(0), f["n"]
        elif op == "PUSH":
            if len(self.e2h) < self.FIFO_DEPTH:
                self.e2h.append(c.SR)
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
        elif op == "XOR":
            c.R[f["d"]] ^= self.read(c, f["s"])
            c.Z = int(c.R[f["d"]] == 0)
        elif op == "AND":
            c.R[f["d"]] &= self.read(c, f["s"])
            c.Z = int(c.R[f["d"]] == 0)
        elif op == "CMP":
            c.Z = int(self.read(c, f["a"]) == self.read(c, f["b"]))
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
            taken = {0: True, 1: c.Z == 1, 2: c.Z == 0, 3: c.F == 1, 4: c.F == 0, 5: c.BC == 0, 6: c.BC != 0, 7: False}[f["c"]]
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
        self.coverage[op] = self.coverage.get(op, 0) + 1
        c.pc = next_pc
        c.stall = dly
