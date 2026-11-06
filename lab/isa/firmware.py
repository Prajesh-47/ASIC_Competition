# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Firmware for each workload on each ISA candidate. Every program is run and judged by a decoder."""
from sim import ENG, F0, HIZ, INV, PASS, I, Infeasible


class P:
    """Tiny assembler: labels and exact delays, with an error if timing can't be met."""

    def __init__(self, variant):
        self.v, self.code, self.labels = variant, [], {}

    def label(self, name):
        self.labels[name] = len(self.code)

    def i(self, op, dly=0, side=None, tick=False, **a):
        self.code.append(I(op, a, dly, side, tick))

    def wait(self, n):
        """Spend exactly n cycles using the fewest words. n=0 adds nothing."""
        if n < 0:
            raise Infeasible(f"needs {n} delay cycles")
        while n > 0:
            if n == 1:
                self.i("NOP")
                n = 0
            else:
                step = min(n - 1, 4095)
                self.i("DLY", n=step)
                n -= step + 1

    def timed(self, op, cycles, **a):
        """Run `op` so it takes `cycles` in total, using its delay field when possible."""
        if cycles < 1:
            raise Infeasible(f"{op} needs {cycles} cycles")
        limit = FIELD_LIMITS[self.v].get(op, 0)
        if cycles - 1 <= limit:
            self.i(op, dly=cycles - 1, **a)
        else:
            self.i(op, **a)
            self.wait(cycles - 1)

    def ldi(self, r, value):
        self.i("LDI", r=r, v=value & 0xFF)
        if value > 255:
            self.i("LDI", r=r, hi=value >> 8)

    def set_bc(self, n, scratch=3):
        if self.v == "V0" or self.v == "V2":
            self.i("LDI", r=scratch, v=n)
            self.i("MOV", d="BC", s=scratch)
        else:
            self.i("BCI", n=n)

    def done(self):
        for ins in self.code:
            if "to" in ins.a and isinstance(ins.a["to"], str):
                ins.a["to"] = self.labels[ins.a["to"]]
        return self.code


FIELD_LIMITS = {
    "V0": {"SET": 127, "OUT": 127, "IN": 255, "MODE": 63},
    "V2": {"SET": 127, "OUT": 127, "IN": 255, "MODE": 63},
    "V1": {"SET": 3, "OUT": 7, "IN": 7},
    "V3": {"SET": 7, "OUT": 7, "IN": 15, "MODE": 15},
    "V4": {"SET": 7, "OUT": 7, "IN": 15, "MODE": 15},
    "V5": {"SET": 7, "OUT": 7, "IN": 15, "MODE": 15},
}
TX, RX = 4, 0


# ---------------------------------------------------------------- UART TX
def uart_tx(v, bit):
    p = P(v)
    p.i("MODE", line=TX, src=ENG)
    if v in ("V0", "V2"):
        p.i("SET", line=TX, v=1)
        if v == "V2":
            p.i("TSET", p=bit)
        p.label("top")
        p.i("PULL")
        p.i("JMP", c="F", to="top")
        p.set_bc(8)
        if v == "V0":
            p.timed("SET", bit, line=TX, v=0)
            p.label("bit")
            p.timed("OUT", bit - 1, line=TX)
            p.i("JMP", c="BCN", to="bit")
            p.timed("SET", bit, line=TX, v=1)
        else:
            p.i("WAITT")
            p.i("SET", line=TX, v=0)
            p.i("NOP")
            p.label("bit")
            p.i("WAITT")
            p.i("OUT", line=TX)
            p.i("JMP", c="BCN", to="bit")
            p.i("WAITT")
            p.i("SET", line=TX, v=1)
            p.i("WAITT")
        p.i("JMP", c="AL", to="top")
    elif v == "V1":  # RP2040 PIO uart_tx, with 8 clocks per bit and a clock divider
        p.i("SIDE", line=TX)
        p.label("top")
        p.i("PULLW", side=1, dly=7, n=8)
        p.i("NOP", side=0, dly=7)
        p.label("bit")
        p.i("OUT", line=TX)
        p.i("JMP", c="BCN", to="bit", dly=6)
        p.i("WRAP", to="top")  # hardware wrap, as PIO: no word and no cycle
    else:
        p.i("SET", line=TX, v=1)
        p.i("TSET", p=bit)
        p.label("top")
        p.i("PULLW", n=8)
        p.i("SET", tick=True, line=TX, v=0)
        p.label("bit")
        p.i("OUTB", tick=True, line=TX, to="bit")
        p.i("SET", tick=True, line=TX, v=1)
        p.i("JMP", c="AL", to="top")
    return p.done()


# ---------------------------------------------------------------- UART RX
def uart_rx(v, bit):
    p = P(v)
    mid = bit + bit // 2
    if v == "V0":
        p.label("top")
        p.i("WAIT", line=RX, c="FALL")
        p.set_bc(8)
        p.wait(mid - 3)
        p.label("bit")
        p.timed("IN", bit - 1, line=RX)
        p.i("JMP", c="BCN", to="bit")
        p.i("PUSH")
        p.i("JMP", c="AL", to="top")
    elif v == "V2":
        p.i("TSET", p=bit)
        p.label("top")
        p.i("WAIT", line=RX, c="FALL")
        p.i("TSET", first=mid - 1)
        p.set_bc(8)
        p.label("bit")
        p.i("WAITT")
        p.i("IN", line=RX)
        p.i("JMP", c="BCN", to="bit")
        p.i("PUSH")
        p.i("JMP", c="AL", to="top")
    elif v == "V1":
        # WAIT, then a delay field cannot reach 1.5 bits of 8 clocks without a NOP
        p.label("top")
        p.i("WAIT", line=RX, c="HI")  # a divided clock can miss an edge, so PIO waits on levels
        p.i("WAIT", line=RX, c="LO")
        p.i("BCI", n=8)
        p.i("NOP", dly=7)
        p.i("NOP", dly=1)
        p.label("bit")
        p.i("IN", line=RX)
        p.i("JMP", c="BCN", to="bit", dly=6)
        p.i("PUSH")
        p.i("JMP", c="AL", to="top")
    else:
        p.i("TSET", p=bit)
        p.label("top")
        p.i("WAIT", line=RX, c="FALL")
        p.i("TSET", first=mid - 1)
        p.i("BCI", n=8)
        p.label("bit")
        p.i("INB", tick=True, line=RX, to="bit")
        p.i("PUSH")
        p.i("JMP", c="AL", to="top")
    return p.done()


# ---------------------------------------------------------------- SPI master, mode 0, full duplex
SCK, MOSI, CS, MISO = 4, 5, 6, 7


def spi_master(v, half):
    p = P(v)
    p.i("SET", line=SCK, v=0)  # set values before taking the lines, or SCK glitches at startup
    for line in (SCK, MOSI, CS):
        p.i("MODE", line=line, src=ENG)
    p.i("SET", line=CS, v=0)
    p.i("MSB", v=1)
    if v in ("V0", "V2"):
        p.label("top")
        p.i("PULL")
        p.i("JMP", c="F", to="top")
        p.set_bc(8)
        p.label("bit")
        # SCK low phase: drive MOSI, then sample MISO just before the rising edge
        p.i("SET", line=SCK, v=0)
        p.i("OUT", line=MOSI, ns=1)
        p.wait(half - 3)
        p.i("IN", line=MISO)
        p.timed("SET", half - 1, line=SCK, v=1)
        p.i("JMP", c="BCN", to="bit")
        p.i("PUSH")
        p.i("JMP", c="AL", to="top")
    elif v == "V1":
        p.i("SIDE", line=SCK)
        p.label("top")
        p.i("PULLW", side=0, n=8)
        p.label("bit")
        p.i("OUT", side=0, line=MOSI, ns=1, dly=half - 2)
        p.i("IN", line=MISO)
        p.i("JMP", c="BCN", to="bit", side=1, dly=half - 1)
        p.i("PUSH")
        p.i("JMP", c="AL", to="top")
    else:
        p.i("SIDE", line=SCK)
        p.i("TSET", p=half)
        p.label("top")
        p.i("PULLW", n=8)
        p.label("bit")
        p.i("OUT", tick=True, side=0, line=MOSI, ns=1)
        p.i("INB", tick=True, side=1, line=MISO, to="bit")
        p.i("PUSH")
        p.i("JMP", c="AL", to="top")
    return p.done()


# ---------------------------------------------------------------- I2C master byte writes
SCL, SDA = 4, 5


def i2c_master(v, q):
    """START, two bytes each with ACK read, STOP. One full SCL period is 4 quarter-periods."""
    p = P(v)
    p.i("MODE", line=SCL, src=ENG)
    p.i("MODE", line=SDA, src=ENG)
    p.i("MSB", v=1)
    p.i("LDI", r=0, v=2)
    if v in ("V3", "V4", "V5"):
        p.i("TSET", p=q)
        p.i("SET", tick=True, line=SDA, v=0)
        p.i("SET", tick=True, line=SCL, v=0)
        p.label("byte")
        p.i("PULLW", n=8)
        p.label("bit")
        p.i("OUT", tick=True, line=SDA)
        p.i("SET", tick=True, line=SCL, v=1)
        p.i("NOP", tick=True)
        p.i("SET", tick=True, line=SCL, v=0)
        p.i("JMP", c="BCN", to="bit")
        p.i("SET", tick=True, line=SDA, v=1)  # release for ACK
        p.i("SET", tick=True, line=SCL, v=1)
        p.i("PROBE", tick=True, line=SDA)
        p.i("SET", tick=True, line=SCL, v=0)
        p.i("JMP", c="Z", to="nack")
        p.i("DJNZ", r=0, to="byte")
        p.label("nack")
        p.i("SET", tick=True, line=SDA, v=0)
        p.i("SET", tick=True, line=SCL, v=1)
        p.i("SET", tick=True, line=SDA, v=1)
        p.i("HALT")
        return p.done()
    # Delay-counted versions: the same waveform with each interval spent explicitly.
    w = lambda n: p.wait(n)
    w(q)
    p.i("SET", line=SDA, v=0)
    w(q - 1)
    p.i("SET", line=SCL, v=0)
    w(q - 1)
    p.label("byte")
    if v == "V1":
        p.i("PULLW", n=8)
    else:
        p.label("pull")
        p.i("PULL")
        p.i("JMP", c="F", to="pull")
        p.set_bc(8)
    p.label("bit")
    p.i("OUT", line=SDA)
    w(q - 1)
    p.i("SET", line=SCL, v=1)
    w(2 * q - 1)
    p.i("SET", line=SCL, v=0)
    w(q - 2)
    p.i("JMP", c="BCN", to="bit")
    p.i("SET", line=SDA, v=1)
    w(q - 1)
    p.i("SET", line=SCL, v=1)
    w(q - 1)
    p.i("PROBE", line=SDA)
    w(q - 1)
    p.i("SET", line=SCL, v=0)
    p.i("JMP", c="Z", to="nack")
    w(q - 3)
    p.i("DJNZ", r=0, to="byte")
    p.label("nack")
    p.i("SET", line=SDA, v=0)
    w(q - 1)
    p.i("SET", line=SCL, v=1)
    w(q - 1)
    p.i("SET", line=SDA, v=1)
    p.i("HALT")
    return p.done()


# ---------------------------------------------------------------- Inline bit flip, UART
def uart_flip(v, bit, k):
    p = P(v)
    p.i("MODE", line=TX, src=PASS)
    at = (k + 1) * bit
    hold = 19 * bit // 2 - (k + 2) * bit - 2  # from the end of the flip to mid stop bit
    if v == "V4" or v == "V5":
        p.ldi(0, at)
        p.ldi(1, bit)
        p.ldi(2, hold)
        p.i("ARM", rule=0, trig=RX, c="FALL", line=TX, src=INV, after=at, width=bit, hold=hold, rearm=True)
        p.i("HALT")
        return p.done()
    if v in ("V2", "V3"):
        p.i("TSET", p=bit)
        p.ldi(1, at - 1 if v == "V3" else at - 2)  # phase comes from R1
    p.label("top")
    p.i("WAIT", line=RX, c="FALL")
    if v in ("V2", "V3"):
        p.i("TSET", first=at - 1 if v == "V3" else at - 2)
        if v == "V3":
            p.i("MODE", tick=True, line=TX, src=INV)
            p.i("MODE", tick=True, line=TX, src=PASS)
        else:
            p.i("WAITT")
            p.i("MODE", line=TX, src=INV)
            p.i("WAITT")
            p.i("MODE", line=TX, src=PASS)
        p.wait(hold)
        p.i("JMP", c="AL", to="top")
        return p.done()
    p.wait(at - 1)
    if v == "V1":
        p.i("MODE", line=TX, src=INV)
        p.wait(bit - 1)
    else:
        p.timed("MODE", bit, line=TX, src=INV)
    p.i("MODE", line=TX, src=PASS)
    p.wait(hold)
    p.i("JMP", c="AL", to="top")
    return p.done()


# ---------------------------------------------------------------- Inline rewrite with no added frame
def uart_rewrite(v, bit, header, new, RX=RX, TX=TX):
    """Pass everything through. After `header`, replace the next byte on the wire, with no delay."""
    p = P(v)
    mid = bit + bit // 2
    p.i("MODE", line=TX, src=PASS)
    p.i("SET", line=TX, v=0)
    if v in ("V3", "V4", "V5"):
        p.i("TSET", p=bit)
    p.label("top")
    p.i("WAIT", line=RX, c="FALL")
    if v in ("V3", "V4", "V5"):
        p.i("TSET", first=mid - 1)
        p.i("BCI", n=8)
        p.label("rx")
        p.i("INB", tick=True, line=RX, to="rx")
        p.i("MOV", d=0, s="LOW")
        p.i("CMPI", a=0, v=header)
        p.i("JMP", c="NZ", to="top")
        p.i("SRI", v=new)
        p.i("BCI", n=8)
        p.i("WAIT", line=RX, c="FALL")
        p.i("TSET", first=bit - 1)
        p.i("MODE", line=TX, src=ENG)  # within the start bit: both sources are low
        p.label("tx")
        p.i("OUTB", tick=True, line=TX, to="tx")
        p.i("SET", tick=True, line=TX, v=1)
        p.i("MODE", line=TX, src=PASS)  # within the stop bit: both sources are high
        p.i("SET", line=TX, v=0)
        p.i("JMP", c="AL", to="top")
        return p.done()
    p.set_bc(8)
    p.wait(mid - 3)
    p.label("rx")
    p.timed("IN", bit - 1, line=RX)
    p.i("JMP", c="BCN", to="rx")
    p.i("MOV", d=0, s="LOW")
    p.i("CMPI", a=0, v=header)
    p.i("JMP", c="NZ", to="top")
    p.ldi(1, new)
    p.i("MOV", d="SR", s=1)
    p.set_bc(8)
    p.i("WAIT", line=RX, c="FALL")
    p.wait(bit // 2 - 1)
    p.i("MODE", line=TX, src=ENG)
    p.wait(bit - bit // 2 - 1)
    p.label("tx")
    p.timed("OUT", bit - 1, line=TX)
    p.i("JMP", c="BCN", to="tx")
    p.i("SET", line=TX, v=1)
    p.wait(bit // 2 - 1)
    p.i("MODE", line=TX, src=PASS)
    p.i("SET", line=TX, v=0)
    p.i("JMP", c="AL", to="top")
    return p.done()


# ---------------------------------------------------------------- Reaction latency
def react(v):
    p = P(v)
    p.i("MODE", line=5, src=ENG)
    if v in ("V4", "V5"):
        p.i("ARM", rule=0, trig=RX, c="FALL", line=5, src=F0, after=0, width=100000)
        p.i("HALT")
    else:
        p.label("top")
        p.i("WAIT", line=RX, c="FALL")
        p.i("MODE", line=5, src=F0)
        p.i("HALT")
    return p.done()


# ---------------------------------------------------------------- Two directions at once
A_IN, A_OUT, B_IN, B_OUT = 0, 4, 1, 5


def dual_flip(v, bit, k, LOOP=13):
    """Flip bit k in both directions, on frames that arrive at unrelated times."""
    at = (k + 1) * bit
    p = P(v)
    p.i("MODE", line=A_OUT, src=PASS)
    p.i("MODE", line=B_OUT, src=PASS)
    hold = 19 * bit // 2 - (k + 2) * bit - 2
    if v == "V4":
        p.ldi(0, at)
        p.ldi(1, bit)
        p.ldi(2, hold)
        p.i("ARM", rule=0, trig=A_IN, c="FALL", line=A_OUT, src=INV, after=at, width=bit, hold=hold, rearm=True)
        p.i("ARM", rule=1, trig=B_IN, c="FALL", line=B_OUT, src=INV, after=at, width=bit, hold=hold, rearm=True)
        p.i("HALT")
        return [p.done()]
    if v == "V5":
        return [_one_dir("V5", bit // 2, k, A_IN, A_OUT), _one_dir("V5", bit // 2, k, B_IN, B_OUT)]
    # One software event loop polling both directions. Each pass costs about LOOP cycles.
    frame = (19 * bit // 2) // LOOP
    if frame > 255 or bit // LOOP < 1:
        raise Infeasible("loop can't resolve this baud")
    start, end = at // LOOP, (at + bit) // LOOP
    p.ldi(2, start)
    p.ldi(3, end)
    p.i("LDI", r=0, v=0)
    p.i("LDI", r=1, v=0)
    p.label("loop")
    for r, line_in, line_out, tag in ((0, A_IN, A_OUT, "a"), (1, B_IN, B_OUT, "b")):
        p.i("CMPI", a=r, v=0)
        p.i("JMP", c="NZ", to=f"run{tag}")
        p.i("PROBE", line=line_in)
        p.i("JMP", c="Z", to=f"next{tag}")  # Z=1: line high, nothing has started
        p.i("ADD", r=r, v=1)
        p.label(f"next{tag}")
        p.i("JMP", c="AL", to=f"sk{tag}")
        p.label(f"run{tag}")
        p.i("ADD", r=r, v=1)
        p.i("CMP", a=r, b=2)
        p.i("JMP", c="NZ", to=f"e{tag}")
        p.i("MODE", line=line_out, src=INV)
        p.label(f"e{tag}")
        p.i("CMP", a=r, b=3)
        p.i("JMP", c="NZ", to=f"f{tag}")
        p.i("MODE", line=line_out, src=PASS)
        p.label(f"f{tag}")
        p.i("CMPI", a=r, v=frame)  # hold off until this frame's stop bit
        p.i("JMP", c="NZ", to=f"sk{tag}")
        p.i("LDI", r=r, v=0)
        p.label(f"sk{tag}")
    p.wait(max(LOOP - 13, 0))  # pad each pass so the 8-bit counters reach a whole frame
    p.i("JMP", c="AL", to="loop")
    return [p.done()]


def _one_dir(v, bit, k, line_in, line_out):
    """Single direction program for a barrel context. Delays count context cycles."""
    p = P(v)
    p.i("MODE", line=line_out, src=PASS)
    p.label("top")
    p.i("WAIT", line=line_in, c="FALL")
    p.wait((k + 1) * bit - 1)
    p.i("MODE", line=line_out, src=INV)
    p.wait(bit - 1)
    p.i("MODE", line=line_out, src=PASS)
    p.wait(19 * bit // 2 - (k + 2) * bit - 2)
    p.i("JMP", c="AL", to="top")
    return p.done()


# ---------------------------------------------------------------- Closed-loop fuzz step
def fuzz_flip(v, bit):
    """Each frame: choose a bit with an LFSR and flip it, so the campaign is seeded and repeatable."""
    p = P(v)
    p.i("MODE", line=TX, src=PASS)
    p.ldi(2, 0xACE1)
    p.ldi(3, 7)
    if v in ("V3", "V4", "V5"):
        p.i("TSET", p=bit)
    p.label("top")
    if v in ("V3", "V4", "V5"):
        p.i("RAND", r=0)
    else:
        # 16-bit Galois LFSR in software: R0 holds the state, R1 the mask
        p.i("MOV", d=0, s=2)
        p.ldi(1, 1)
        p.i("AND", d=1, s=0)
        p.i("SHR", r=0)
        p.i("JMP", c="Z", to="nomask")
        p.ldi(1, 0xB400)
        p.i("XOR", d=0, s=1)
        p.label("nomask")
        p.i("MOV", d=2, s=0)
    p.i("AND", d=0, s=3)  # keep 3 bits: which data bit to flip
    p.i("MOV", d=1, s=0)
    p.i("XOR", d=1, s=3)  # R1 = 7 - k: whole bits left after the flip, for the holdoff
    p.i("ADD", r=0, v=1)  # N = k + 1 bit times after the start edge
    if v in ("V3", "V4", "V5"):
        p.i("WAIT", line=RX, c="FALL")
        p.i("TSET", first=bit - 1)
        p.label("seek")
        p.i("DJNZ", r=0, to="more")
        p.i("MODE", tick=True, line=TX, src=INV)
        p.i("MODE", tick=True, line=TX, src=PASS)
        p.i("ADD", r=1, v=1)
        p.label("hold")
        p.i("WAITT")
        p.i("DJNZ", r=1, to="hold")
        p.i("JMP", c="AL", to="top")
        p.label("more")
        p.i("WAITT")
        p.i("JMP", c="AL", to="seek")
    else:
        p.i("WAIT", line=RX, c="FALL")
        p.wait(bit - 2)
        p.label("seek")
        p.i("DJNZ", r=0, to="more")
        p.i("MODE", line=TX, src=INV)
        p.wait(bit - 1)
        p.i("MODE", line=TX, src=PASS)
        p.i("ADD", r=1, v=1)
        p.label("hold")
        p.wait(bit - 1)
        p.i("DJNZ", r=1, to="hold")
        p.i("JMP", c="AL", to="top")
        p.label("more")
        p.wait(bit - 2)
        p.i("JMP", c="AL", to="seek")
    return p.done()


# ---------------------------------------------------------------- Inline SPI: replace one byte of MISO
I_SCK, I_CS, I_MISO, O_SCK, O_CS, O_MISO = 0, 1, 2, 4, 5, 6


def spi_inline(v, skip_bytes, new):
    """Pass SCK, CS and MISO through. Replace MISO byte number `skip_bytes`, following SCK's edges."""
    p = P(v)
    for line in (O_SCK, O_CS, O_MISO):
        p.i("MODE", line=line, src=PASS)
    p.i("MSB", v=1)
    count = skip_bytes * 8 - 1  # falling edge number 8*skip starts the byte to replace
    if v in ("V3T", "V5T"):
        p.ldi(0, count)
        p.i("SRI", v=new & 0x7F, sh=9)  # the first bit goes out through SET
        p.i("BCI", n=7)
        p.i("SET", line=O_MISO, v=(new >> 7) & 1)
        p.i("TSRC", line=I_SCK, want=0)  # every falling SCK is a tick
        p.label("skip")
        p.i("NOP", tick=True)
        p.i("DJNZ", r=0, to="skip")
        p.i("MODE", tick=True, line=O_MISO, src=ENG)
        p.label("tx")
        p.i("OUTB", tick=True, line=O_MISO, to="tx")
        p.i("MODE", tick=True, line=O_MISO, src=PASS)
        p.i("HALT")
        return p.done()
    p.ldi(0, count)
    p.ldi(1, (new & 0x7F) << 9)
    p.i("MOV", d="SR", s=1)
    p.set_bc(7)
    p.i("SET", line=O_MISO, v=(new >> 7) & 1)
    p.label("skip")
    p.i("WAIT", line=I_SCK, c="FALL")
    p.i("DJNZ", r=0, to="skip")
    p.i("WAIT", line=I_SCK, c="FALL")
    p.i("MODE", line=O_MISO, src=ENG)
    p.label("tx")
    p.i("WAIT", line=I_SCK, c="FALL")
    p.i("OUT", line=O_MISO)
    p.i("JMP", c="BCN", to="tx")
    p.i("WAIT", line=I_SCK, c="FALL")
    p.i("MODE", line=O_MISO, src=PASS)
    p.i("HALT")
    return p.done()


# ---------------------------------------------------------------- Match a byte, alter the next frame, both ways
def match_flip(v, bit, header, k):
    at, lines = (k + 1) * bit, ((0, 4), (1, 5))
    if v == "V7":
        p = P("V4")
        for _, out in lines:
            p.i("MODE", line=out, src=PASS)
        p.ldi(0, bit)
        p.ldi(1, header | (0xFF << 8))  # pattern and mask share a register
        p.ldi(2, at)
        for n, (rx, tx) in enumerate(lines):
            p.i("MATCH", rule=n, trig=rx, line=tx, src=INV, bit=bit, value=header, mask=0xFF, after=at, width=bit)
        p.i("HALT")
        return [p.done()]
    progs = []
    for rx, tx in lines:
        p = P("V5")
        p.i("MODE", line=tx, src=PASS)
        p.i("TSET", p=bit)
        p.ldi(1, at - 1)
        p.label("top")
        p.i("WAIT", line=rx, c="FALL")
        p.i("TSET", first=bit + bit // 2 - 1)
        p.i("BCI", n=8)
        p.label("in")
        p.i("INB", tick=True, line=rx, to="in")
        p.i("MOV", d=0, s="LOW")
        p.i("CMPI", a=0, v=header)
        p.i("JMP", c="NZ", to="top")
        p.i("WAIT", line=rx, c="FALL")
        p.i("TSET", first=at - 1)
        p.i("MODE", tick=True, line=tx, src=INV)
        p.i("MODE", tick=True, line=tx, src=PASS)
        p.wait(19 * bit // 2 - (k + 2) * bit)
        p.i("JMP", c="AL", to="top")
        code = p.done()
        progs.append(code)
    return progs
