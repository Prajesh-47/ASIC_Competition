# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Run a SPLICE program on the golden model, with no simulator or board.

    python -m splice.run firmware/uart_tx.s --push 0x55 0xA3 --cycles 12000 --vcd uart.vcd
    python -m splice.run firmware/uart_tx.s --push 0x55 --wire 4:0 --cycles 6000

The model is the one the RTL is compared with on every pin, every cycle, so what a program does here is
what the chip does, up to pad electricals. It prints every change on the pins, the words the program
pushed to the host, and where each context stopped. `--vcd` writes a waveform for GTKWave or PulseView.

Lines 0-7 are the bus lines (uio), 8-11 the AUX outputs. `--wire A:B` feeds line A's pin back into line
B, for loopback. `--in LINE=bits@period` drives a line from a bit string, one bit every `period`
cycles, then holds the last bit. Undriven lines read 1, as with a pull-up.
"""
import argparse
import sys

from .asm import AsmError, assemble
from .model import Splice


def run(source, cycles, push=(), wires=(), stimuli=(), defines=None, contexts=None, routes=()):
    """Returns (model, trace, popped). trace is a list of (cycle, levels, driven) at every change."""
    prog = assemble(source, defines)
    model = Splice()
    model.load(prog.words)
    starts = prog.ctx
    model.nctx = contexts or 1 + max([k for k, pc in enumerate(starts, 1) if pc] or [0])
    model.ctx_pc = list(starts)
    for ctx, pull, push_to in routes:
        model.pull_sel[ctx], model.push_sel[ctx] = pull, push_to
    for word in push:
        model.host_push(word)
    model.start()
    trace, popped, pins, last = [], [], {"out": 0, "oe": 0, "uo": 0}, None
    for t in range(cycles):
        levels = [(pins["out"] >> i) & 1 if (pins["oe"] >> i) & 1 else 1 for i in range(8)]
        for a, b in wires:
            if (pins["oe"] >> a) & 1:
                levels[b] = (pins["out"] >> a) & 1
        for line, bits, period in stimuli:
            if not (pins["oe"] >> line) & 1:
                levels[line] = bits[min(t // period, len(bits) - 1)]
        pins = model.step(ui=0xFF, uio=sum(v << i for i, v in enumerate(levels)))
        now = (pins["out"], pins["oe"], pins["uo"] >> 4, tuple(levels))
        if now != last:
            trace.append((t, now))
            last = now
        while (word := model.host_pop()) is not None:
            popped.append((t, word))
    return prog, model, trace, popped


def write_vcd(path, trace, cycles, period_ns=20):
    names = [f"line{i}" for i in range(8)] + [f"oe{i}" for i in range(8)] + [f"aux{i}" for i in range(4)]
    ids = [chr(33 + i) for i in range(len(names))]
    with open(path, "w", encoding="utf-8") as out:
        out.write(f"$timescale 1ns $end\n$scope module splice $end\n")
        for name, ident in zip(names, ids):
            out.write(f"$var wire 1 {ident} {name} $end\n")
        out.write("$upscope $end\n$enddefinitions $end\n")
        previous = [None] * len(names)
        for t, (pin_out, oe, aux, levels) in trace:
            # A line shows what is on the wire: the chip's level where it drives, the input otherwise
            wire = [(pin_out >> i) & 1 if (oe >> i) & 1 else levels[i] for i in range(8)]
            values = wire + [(oe >> i) & 1 for i in range(8)] + [(aux >> i) & 1 for i in range(4)]
            changed = [f"{v}{ident}" for v, ident, old in zip(values, ids, previous) if v != old]
            if changed:
                out.write(f"#{t * period_ns}\n" + "\n".join(changed) + "\n")
            previous = values
        out.write(f"#{cycles * period_ns}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="splice.run", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source")
    parser.add_argument("--cycles", type=int, default=10000, help="clock cycles to run (20 ns each at 50 MHz)")
    parser.add_argument("--push", nargs="*", default=[], metavar="WORD", help="words the host pushes before start")
    parser.add_argument("--wire", action="append", default=[], metavar="A:B", help="feed line A back into line B")
    parser.add_argument("--in", dest="stim", action="append", default=[], metavar="LINE=BITS@PERIOD")
    parser.add_argument("--contexts", type=int, help="how many contexts run (default: from the .ctx lines)")
    parser.add_argument("--route", action="append", default=[], metavar="CTX:PULL:PUSH",
                        help="a context's PULL source and PUSH target: 0 host, 1 link 0, 2 link 1")
    parser.add_argument("-D", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--vcd", help="write the pins as a VCD waveform")
    parser.add_argument("--quiet", action="store_true", help="don't list pin changes")
    opts = parser.parse_args(argv)
    try:
        stimuli = []
        for item in opts.stim:
            line, rest = item.split("=", 1)
            bits, period = rest.split("@", 1)
            stimuli.append((int(line), [int(b) for b in bits], int(period)))
        prog, model, trace, popped = run(
            open(opts.source, encoding="utf-8").read(), opts.cycles,
            push=[int(w, 0) for w in opts.push],
            wires=[tuple(int(x) for x in w.split(":")) for w in opts.wire],
            stimuli=stimuli,
            defines={k: int(v, 0) for k, v in (d.split("=", 1) for d in opts.D)},
            contexts=opts.contexts,
            routes=[tuple(int(x) for x in r.split(":")) for r in opts.route])
    except AsmError as err:
        sys.exit(f"{opts.source}: {err}")
    for warning in prog.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"{opts.source}: {len(prog.words)} words, {model.nctx} context{'s' if model.nctx > 1 else ''}, "
          f"{opts.cycles} cycles")
    if not opts.quiet:
        print("  cycle  lines 7..0 (. = not driven)  aux 3..0")
        rows, shown = [], None
        for t, (pin_out, oe, aux, _) in trace:
            lines = "".join(str((pin_out >> i) & 1) if (oe >> i) & 1 else "." for i in range(7, -1, -1))
            if (lines, aux) != shown:  # the trace also holds input changes, which the VCD shows
                rows.append(f"{t:7d}  {lines}                     {aux:04b}")
                shown = (lines, aux)
        print("\n".join(rows[:200]))
        if len(rows) > 200:
            print(f"  ... {len(rows) - 200} more changes (use --vcd to see them all)")
    print("pushed to the host:", " ".join(f"{w:04x}@{t}" for t, w in popped) or "nothing")
    for k, c in enumerate(model.ctx[:model.nctx]):
        print(f"context {k}: pc {c.pc}, R0-R3 {' '.join(f'{r:04x}' for r in c.R[:4])}")
    if opts.vcd:
        write_vcd(opts.vcd, trace, opts.cycles)
        print(f"wrote {opts.vcd}")


if __name__ == "__main__":
    main()
