# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Turn capture memory into timed edges, and write VCD for PulseView or GTKWave."""

DT_REPEAT = 0xFFF
DT_MARK = 0xFFE
DT_LIMIT = 0xFFD


def in_order(words, pointer, wrapped):
    """Oldest entry first. `words` is the whole memory if wrapped, else the first `pointer` entries."""
    return words[pointer:] + words[:pointer] if wrapped else words[:pointer]


def decode(entries):
    """Returns (edges, marks): edges are (tick, 4-bit states), and marks are ticks."""
    edges, marks, tick = [], [], 0
    for word in entries:
        states, dt = word >> 12, word & 0xFFF
        if dt == DT_MARK:
            marks.append(tick)
        elif dt == DT_REPEAT:
            tick += DT_LIMIT
        else:
            tick += dt
            edges.append((tick, states))
    return edges, marks


def to_vcd(entries, tick_ns, names=("L0", "L1", "L2", "L3")):
    """VCD text for one capture. Times come from the entries, and tick_ns sets the timescale."""
    edges, marks = decode(entries)
    ids = "!\"#$"
    out = ["$timescale 1 ns $end", "$scope module splice $end"]
    out += [f"$var wire 1 {ids[i]} {names[i]} $end" for i in range(4)]
    out += ["$var event 1 % mark $end", "$upscope $end", "$enddefinitions $end"]
    previous, events = None, sorted([(t, 0, s) for t, s in edges] + [(t, 1, None) for t in marks], key=lambda e: e[:2])
    for tick, kind, states in events:
        out.append(f"#{tick * tick_ns}")
        if kind == 1:
            out.append("1%")
            continue
        for i in range(4):
            bit = (states >> i) & 1
            if previous is None or ((previous >> i) & 1) != bit:
                out.append(f"{bit}{ids[i]}")
        previous = states
    return "\n".join(out) + "\n"
