# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Does CODE man keep exact half-bits across FIFO words? A probe for 10BASE-T's coding, on the golden model.
Usage, from lab/explore:
    python3 man10.py [--fw fw/man_tx_probe.s] [HB ...]
Compares line 4, cycle by cycle, with IEEE Manchester (a bit b is !b then b, LSB first) of six words.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "model"))
from splice.run import run  # noqa: E402

LINE = 4
WORDS = [0x00FF, 0xA5A5, 0x0F0F, 0x1234, 0xFFFF, 0x0000]


def levels_of(trace, cycles):
    levels, last_t, last_v = [], 0, 1
    for t, (out, oe, _aux, ins) in trace:
        levels += [last_v] * (t - last_t)
        last_t, last_v = t, (out >> LINE) & 1 if (oe >> LINE) & 1 else ins[LINE]
    return levels + [last_v] * (cycles - last_t)


def check(source, hb):
    want = []
    for w in WORDS:
        for i in range(16):
            b = (w >> i) & 1
            want += [1 - b] * hb + [b] * hb
    cycles = 400 + len(want)
    _prog, _model, trace, _popped = run(source, cycles, push=WORDS, defines={"HB": hb})
    got = levels_of(trace, cycles)
    # The first half-bit starts a few cycles after reset; take the best alignment
    start = min(range(200), key=lambda s: sum(a != b for a, b in zip(got[s:s + len(want)], want)))
    wrong = [i for i, (a, b) in enumerate(zip(got[start:start + len(want)], want)) if a != b]
    return wrong, len(want)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fw", default=os.path.join(HERE, "fw", "man_tx_probe.s"))
    ap.add_argument("hb", nargs="*", type=int, default=[5, 4, 3, 2])
    args = ap.parse_args()
    source = open(args.fw, encoding="utf-8").read()
    for hb in args.hb:
        wrong, n = check(source, hb)
        where = f", at offsets {wrong[:8]} (a word is {32 * hb} cycles)" if wrong else ""
        print(f"half-bit {hb} cycles: {len(wrong)} wrong cycles of {n}{where}")


if __name__ == "__main__":
    main()
