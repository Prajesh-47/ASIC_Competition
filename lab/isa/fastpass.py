# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Does one less flop on the PASS path raise the inline SPI limit, and do other workloads survive it?"""
import checkers as ck
import firmware as fw
import lab
from sim import Chip, Core, run

for fast in (False, True):
    ok = [h for h in range(2, 30) if lab.spi_inline_case("V3T", h, fast=fast)[1]]
    print(f"fast_pass={fast}: smallest half period {min(ok)} cycles, SCK {50 / (2 * min(ok)):.2f} MHz; all halves from there work: {ok == list(range(min(ok), 30))}")

# Pure pass-through latency, measured at the pins.
for fast in (False, True):
    p = fw.P("V0")
    p.i("MODE", line=4, src="PASS")
    p.i("HALT")
    chip = Chip()
    chip.fast_pass = fast
    chip.add(Core(p.done(), chip))
    trace = run(chip, 60, lambda t: {0: 0 if t >= 30 else 1})
    t = next(i for i, s in enumerate(trace) if i > 10 and s[4] == 0)
    print(f"fast_pass={fast}: pin to pin {t - 30 + 1} cycles, {(t - 30 + 1) * 20} ns plus pads")

# A timed flip with PASS one cycle early: the switch lands one cycle late, relative to the data.
bit, k = 434, 3
for fast in (False, True):
    prog = fw.uart_flip("V3", bit, k)
    chip = Chip()
    chip.fast_pass = fast
    chip.add(Core(prog, chip))
    waves = [ck.uart_wave(b, bit, 50 + n * 13 * bit) for n, b in enumerate(lab.BYTES)]

    def stim(t):
        v = 1
        for w in waves:
            v &= w(t)
        return {0: v}
    trace = run(chip, 100 + 6 * 13 * bit, stim)
    want = [b ^ (1 << k) for b in lab.BYTES]
    print(f"fast_pass={fast}: uart flip decodes {[f[1] for f in ck.uart_decode(trace, 4, bit)] == want}, exact edges {ck.uart_exact(trace, 4, bit, want)}")
