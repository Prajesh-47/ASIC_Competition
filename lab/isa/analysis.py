# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Data-driven ISA analysis on executed firmware traces.

dataflow_limit answers "what could Tomasulo buy?" with a perfect out-of-order core: unlimited
reservation stations, perfect branch prediction and zero-cost renaming. Pin operations and timed
waits keep their order, because pin order and pin timing are the program's whole purpose.

bigrams finds which adjacent instructions run together, as candidates for fusion.
"""
from collections import Counter

import checkers as ck
import firmware as fw
from sim import Chip, Core, run

ORDERED = {"SET", "OUT", "IN", "MODE", "WAIT", "WAITT", "DLY", "DLYR", "PROBE", "PUSH", "PULL", "PULLW", "HALT", "OUTB", "INB", "TSET", "ARM"}


def deps(ins):
    """(reads, writes) over registers, flags, SR and BC."""
    a, op = ins.a, ins.op
    reads, writes = set(), set()
    reg = lambda x: x if isinstance(x, str) else f"R{x}"
    if op in ("OUT", "OUTB"):
        reads |= {"SR", "BC"}
        if not a.get("ns"):
            writes |= {"SR", "BC"}
    elif op in ("IN", "INB"):
        reads |= {"SR", "BC"}
        writes |= {"SR", "BC"}
    elif op == "LDI":
        writes.add(f"R{a['r']}")
    elif op == "MOV":
        s = "SR" if a["s"] == "LOW" else reg(a["s"])
        reads.add(s)
        writes.add(reg(a["d"]))
    elif op == "ADD":
        reads.add(f"R{a['r']}")
        writes |= {f"R{a['r']}", "Z"}
    elif op in ("XOR", "AND"):
        reads |= {f"R{a['d']}", reg(a["s"])}
        writes |= {f"R{a['d']}", "Z"}
    elif op == "CMP":
        reads |= {reg(a["a"]), reg(a["b"])}
        writes.add("Z")
    elif op == "CMPI":
        reads.add(reg(a["a"]))
        writes.add("Z")
    elif op in ("SHR", "SHL"):
        reads.add(f"R{a['r']}")
        writes.add(f"R{a['r']}")
    elif op == "JMP":
        reads |= {"BC"} if a["c"] in ("BC0", "BCN") else {"Z"} if a["c"] in ("Z", "NZ") else {"F"} if a["c"] in ("F", "NF") else set()
    elif op == "DJNZ":
        reads.add(f"R{a['r']}")
        writes.add(f"R{a['r']}")
    elif op == "PULL":
        writes |= {"SR", "F"}
    elif op == "PROBE":
        writes.add("Z")
    elif op == "BCI":
        writes.add("BC")
    return reads, writes


def dataflow_limit(prog, trace):
    """Cycles an ideal out-of-order core needs for this trace, against the in-order count."""
    ready, barrier, seq = {}, 0, 0
    for pc in trace:
        ins = prog[pc]
        cost = 1 + ins.dly + (ins.a.get("n", 0) if ins.op == "DLY" else 0)
        seq += cost
        r, w = deps(ins)
        start = max([ready.get(x, 0) for x in r] + [0])
        if ins.op in ORDERED:
            start = max(start, barrier)
            barrier = start + cost
        for x in w:
            ready[x] = start + cost
    finish = max(list(ready.values()) + [barrier, 0])
    return seq, finish


def workloads():
    bytes_ = [0x55, 0xA3, 0x00, 0xFF, 0x7E]
    bit = 434

    def frames(line=0, gap=3):
        waves = [ck.uart_wave(b, bit, 300 + n * ((10 + gap) * bit)) for n, b in enumerate(bytes_)]

        def stim(t):
            v = 1
            for w in waves:
                v &= w(t)
            return {line: v}
        return stim
    span = 7 * 13 * bit
    yield "uart_tx", fw.uart_tx("V0", bit), None, [b for b in bytes_], span
    yield "uart_rx", fw.uart_rx("V0", bit), frames(), [], span
    yield "spi_master", fw.spi_master("V0", 25), None, [b << 8 for b in bytes_], 6000
    yield "uart_flip", fw.uart_flip("V0", bit, 3), frames(), [], span
    yield "uart_rewrite", fw.uart_rewrite("V0", bit, 0xA3, 0x3C), frames(gap=0), [], span
    yield "fuzz_flip", fw.fuzz_flip("V0", bit), frames(gap=4), [], span


def main():
    pairs = Counter()
    rows = []
    for name, prog, stim, fifo, cycles in workloads():
        chip = Chip()
        core = chip.add(Core(prog, chip))
        core.fifo_tx = list(fifo)
        run(chip, cycles, stim)
        # Keep only real work: drop idle spinning (a WAIT retrying, FIFO polling) and everything
        # after the last pin operation, or those idle loops would look like an out-of-order gain.
        pin_ops = {"SET", "OUT", "OUTB", "MODE", "IN", "INB", "PUSH"}
        last = max((i for i, pc in enumerate(core.trace) if prog[pc].op in pin_ops), default=0)
        kept = []
        for i, pc in enumerate(core.trace[: last + 1]):
            op = prog[pc].op
            if kept and pc == kept[-1] and op == "WAIT":
                continue
            if op in ("PULL",) or (op == "JMP" and prog[pc].a["c"] == "F"):
                continue
            kept.append(pc)
        ops = [prog[pc] for pc in kept]
        for x, y in zip(ops, ops[1:]):
            pairs[f"{x.op}+{y.op}"] += 1
        seq, ideal = dataflow_limit(prog, kept)
        rows.append((name, seq, ideal, seq / max(ideal, 1)))
        print(f"{name:14s} in-order {seq:8d}  ideal OoO {ideal:8d}  speedup x{seq / max(ideal, 1):.3f}  saved {seq - ideal:5d} cycles")
    print("top executed pairs:")
    for k, n in pairs.most_common(14):
        print(f"  {k:18s} {n}")
    return rows


if __name__ == "__main__":
    main()
