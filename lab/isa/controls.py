# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Negative controls: the decoders must reject deliberately broken traces and broken firmware."""
import copy

import checkers as ck
import firmware as fw
import lab
from sim import Chip, Core, Infeasible, run

results = []


def control(name, passed):
    results.append((name, passed))
    print(("PASS " if passed else "FAIL ") + name)


# UART: a good trace passes, and damaging it one way at a time must fail.
bit = 434
prog = fw.uart_tx("V3", bit)
chip = Chip()
core = chip.add(Core(prog, chip))
core.fifo_tx = list(lab.BYTES)
trace = run(chip, (len(lab.BYTES) + 2) * 11 * bit + 200)
control("uart_exact accepts a good trace", ck.uart_exact(trace, 4, bit, lab.BYTES))
frame = ck.uart_decode(trace, 4, bit)[0][0]
bad = copy.deepcopy(trace)
for t in range(frame + bit * 3 + bit // 2 - 5, frame + bit * 3 + bit // 2 + 5):
    bad[t][4] ^= 1
control("rejects a flipped data bit", not ck.uart_exact(bad, 4, bit, lab.BYTES))
bad = copy.deepcopy(trace)
edge = next(t for t, v in ck.edges(trace, 4) if t > frame + bit)
bad[edge][4] = bad[edge - 1][4]
control("rejects one edge that is one cycle late", not ck.uart_exact(bad, 4, bit, lab.BYTES))
control("rejects the wrong expected bytes", not ck.uart_exact(trace, 4, bit, [0x54] + lab.BYTES[1:]))
control("rejects the wrong baud", not ck.uart_exact(trace, 4, bit + 40, lab.BYTES))

# SPI: wrong replacement byte must fail, with the slave model unchanged.
res = lab.spi_inline_case("V3T", 25)
control("spi inline passes at a slow clock", res[1])
orig = fw.spi_inline
fw.spi_inline = lambda v, s, n: orig(v, s, n ^ 0x10)
control("spi inline rejects a wrong replaced byte", not lab.spi_inline_case("V3T", 25)[1])
fw.spi_inline = lambda v, s, n: orig(v, s + 1, n)
control("spi inline rejects replacing the wrong byte", not lab.spi_inline_case("V3T", 25)[1])
fw.spi_inline = orig

# I2C: a target that never ACKs must not pass, and a stuck SDA must not decode.
events, data = ck.i2c_decode([1, 1, 1, 0, 1, 0, 1, 1], [1, 0, 0, 0, 1, 1, 1, 1])
control("i2c decoder sees a START", events[0] == "S")
scl, sda = [1] * 50, [1] * 50
control("i2c decoder sees nothing on an idle bus", ck.i2c_decode(scl, sda) == ([], []))

# Encoding: an impossible field must be rejected.
import isa_enc as enc
from sim import I
try:
    enc.fits("V0", I("SET", {"line": 4, "v": 1}, dly=128))
    control("encoding rejects an oversized delay", False)
except ValueError:
    control("encoding rejects an oversized delay", True)
try:
    enc.fits("V0", I("OUTB", {"line": 4, "to": 0}))
    control("encoding rejects an instruction outside the variant", False)
except ValueError:
    control("encoding rejects an instruction outside the variant", True)

print(f"{sum(p for _, p in results)}/{len(results)} controls behaved correctly")
raise SystemExit(0 if all(p for _, p in results) else 1)
