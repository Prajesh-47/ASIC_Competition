# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Held-out flexibility test. These protocols were written after the instruction list was fixed, and
no instruction was added or changed for them. Everything must encode on the V3 table as it stood."""
import copy

import checkers as ck
import flex as F
from firmware import P
from sim import ENG, Chip, Core, run

US = 50  # cycles per microsecond at 50 MHz


# ---------------------------------------------------------------- WS2812 addressable LEDs
def ws2812_fw():
    p = P("V3")
    p.i("MSB", v=1)
    p.i("SET", line=4, v=0)
    p.i("MODE", line=4, src=ENG)
    p.i("TSET", p=62)  # 1.25 us bit period
    p.label("word")
    p.i("PULLW", n=16)
    p.label("bit")
    p.i("TSTB")
    p.i("OUT", line=15)
    p.i("JMP", c="Z", to="one")
    p.i("SET", tick=True, line=4, v=1)
    p.i("DLY", n=18)
    p.i("SET", line=4, v=0)
    p.i("JMP", c="BCN", to="bit")
    p.i("JMP", c="AL", to="word")
    p.label("one")
    p.i("SET", tick=True, line=4, v=1)
    p.i("DLY", n=38)
    p.i("SET", line=4, v=0)
    p.i("JMP", c="BCN", to="bit")
    p.i("JMP", c="AL", to="word")
    return p.done()


def ws2812_decode(trace):
    """Classify each high pulse by the datasheet windows: T0H 0.4 us, T1H 0.8 us, both +/- 150 ns."""
    bits, edges = [], ck.edges(trace, 4, 0)
    for (t0, v0), (t1, _) in zip(edges, edges[1:]):
        if v0 == 1:
            width = (t1 - t0) * 20
            if 250 <= width <= 550:
                bits.append(0)
            elif 650 <= width <= 950:
                bits.append(1)
            else:
                return None
    periods = [(b[0] - a[0]) * 20 for a, b in zip(edges[1::2], edges[3::2])]
    if any(not 650 <= x <= 1850 for x in periods):
        return None
    return bits


def ws2812_case(prog=None):
    prog = prog or ws2812_fw()
    n = F.words("V3", prog)
    chip = Chip()
    core = chip.add(Core(prog, chip))
    grb = [0x1F, 0xA0, 0x35, 0xC2, 0x07, 0xFF]
    core.fifo_tx = [grb[0] << 8 | grb[1], grb[2] << 8 | grb[3], grb[4] << 8 | grb[5]]
    trace = run(chip, 200 + 62 * 60)
    bits = ws2812_decode(trace)
    if bits is None or len(bits) != 48:
        return n, False
    got = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, 48, 8)]
    return n, got == grb


# ---------------------------------------------------------------- 1-Wire controller
def onewire_fw(cmd=0xCC):
    p = P("V3")
    L = 4
    p.i("SET", line=L, v=1)
    p.i("MODE", line=L, src=ENG)
    p.i("SET", line=L, v=0)  # reset pulse, 480 us
    p.i("LDI", r=0, v=6)
    p.label("rst")
    p.i("DLY", n=3999)
    p.i("DJNZ", r=0, to="rst")
    p.i("SET", line=L, v=1)
    p.i("DLY", n=70 * US)
    p.i("PROBE", line=L)  # a device answers by pulling low
    p.i("JMP", c="Z", to="fail")
    p.wait(410 * US)  # longer than one 12-bit delay, so it takes several words
    p.i("SRI", v=cmd)
    p.i("BCI", n=8)
    p.label("wr")
    p.i("TSTB")
    p.i("OUT", line=15)
    p.i("SET", line=L, v=0)
    p.i("JMP", c="Z", to="w1")
    p.i("DLY", n=60 * US)
    p.i("SET", line=L, v=1)
    p.i("DLY", n=10 * US)
    p.i("JMP", c="BCN", to="wr")
    p.i("JMP", c="AL", to="rd")
    p.label("w1")
    p.i("DLY", n=6 * US)
    p.i("SET", line=L, v=1)
    p.i("DLY", n=64 * US)
    p.i("JMP", c="BCN", to="wr")
    p.label("rd")
    p.i("BCI", n=8)
    p.label("rb")
    p.i("SET", line=L, v=0)
    p.i("DLY", n=6 * US)
    p.i("SET", line=L, v=1)
    p.i("DLY", n=8 * US)
    p.i("IN", line=L)
    p.i("DLY", n=55 * US)
    p.i("JMP", c="BCN", to="rb")
    p.i("PUSH")
    p.label("fail")
    p.i("HALT")
    return p.done()


class OneWireDevice:
    """Timed from the 1-Wire rules: reset over 400 us, presence, sample at 30 us, reply held 30 us."""

    def __init__(self, reply=0x5A):
        self.reply, self.prev, self.low_for, self.state = reply, 1, 0, "idle"
        self.at, self.hold, self.bits, self.cmd, self.sent, self.seen_reset = 0, 0, [], None, 0, False

    def step(self, t, level):
        if self.prev == 1 and level == 0:
            self.low_for = 0
            if self.state == "cmd":
                self.at = t + 30 * US
            elif self.state == "reply":
                bit = (self.reply >> self.sent) & 1
                self.sent += 1
                if not bit:
                    self.hold = t + 30 * US
        if level == 0:
            self.low_for += 1
        if self.prev == 0 and level == 1 and self.low_for >= 400 * US:
            self.seen_reset = True
            self.state, self.presence_on, self.presence_off = "pres", t + 15 * US, t + 75 * US
        if self.state == "pres" and t >= self.presence_off:
            self.state = "cmd"
        if self.state == "cmd" and self.at and t == self.at:
            self.bits.append(level)
            self.at = 0
            if len(self.bits) == 8:
                self.cmd = sum(b << i for i, b in enumerate(self.bits))
                self.state = "reply"
        self.prev = level
        pull = self.state == "pres" and self.presence_on <= t < self.presence_off
        return pull or t < self.hold


def onewire_case(prog=None, device=True):
    prog = prog or onewire_fw()
    n = F.words("V3", prog)
    chip = Chip()
    chip.odrain = {4}
    core = chip.add(Core(prog, chip))
    dev = OneWireDevice()
    line = F.Bus(rise=10)

    def bus(t, out, lv):
        pull = dev.step(t, line.level) if device else False
        lv[4] = line.step(out[4] == 0 or pull)
        return lv
    run(chip, 2300 * US, None, bus)
    return n, dev.seen_reset and dev.cmd == 0xCC and [x >> 8 for x in core.fifo_rx] == [0x5A]


def main():
    results = []

    def show(name, value, want=True):
        ok = value == want
        results.append(ok)
        print(("PASS " if ok else "FAIL ") + name)

    n, ok = ws2812_case()
    print(f"WS2812: {n} words, decoder accepts: {ok}")
    show("WS2812 runs", ok)
    n, ok = onewire_case()
    print(f"1-Wire: {n} words, device and host agree: {ok}")
    show("1-Wire runs", ok)
    bad = copy.deepcopy(ws2812_fw())
    next(i for i in bad if i.op == "DLY" and i.a["n"] == 38).a["n"] = 26
    show("WS2812 rejects a 1 bit with the wrong pulse width", ws2812_case(bad)[1], False)
    show("1-Wire fails with no device on the bus", onewire_case(device=False)[1], False)
    bad = copy.deepcopy(onewire_fw())
    next(i for i in bad if i.op == "DLY" and i.a["n"] == 60 * US).a["n"] = 6 * US
    show("1-Wire rejects a 0 slot that is too short", onewire_case(bad)[1], False)
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
