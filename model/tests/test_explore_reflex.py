# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Match reflex (ARM/MATCH) in the golden model, and the two scenarios of lab/explore/match-reflex.md."""
import os
import sys

from splice.asm import assemble
from splice.isa import SRC, decode, encode
from splice.model import Splice

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lab", "explore"))
import match_reflex as mr  # noqa: E402


def machine(text, two=False):
    prog = assemble(text)
    m = Splice()
    m.two = two
    m.ctx1_pc = prog.ctx1
    m.load(prog.words)
    m.start()
    return m, prog


def trace(m, cycles, line=4, uio=0xFF):
    """(cycle, pc of context 0 before the step, src of `line` after it) for every cycle."""
    out = []
    for t in range(cycles):
        pc = m.ctx[0].pc
        m.step(uio=uio)
        out.append((t, pc, m.src[line]))
    return out


def first_change(rows, start_src):
    for t, pc, src in rows:
        if src != start_src:
            return t, pc
    return None


def test_encoding_uses_the_reserved_codes():
    word = encode("ARM", when=2, once=1, trig=0, line=4, src=3)
    assert word >> 11 == 0b10101
    assert decode(word) == ("ARM", {"when": 2, "once": 1, "trig": 0, "line": 4, "src": 3})
    word = encode("MATCH", v=2, m=3, pe=1, pos=0)
    assert word >> 11 == 0b10110
    assert decode(word)[0] == "MATCH"
    prog = assemble("arm 5, f0, now once trig\nmatch r2, r3 pos=7\narm off")
    assert decode(prog.words[0]) == ("ARM", {"when": 0, "once": 1, "trig": 1, "line": 5, "src": SRC["F0"]})
    assert decode(prog.words[1]) == ("MATCH", {"v": 2, "m": 3, "pe": 1, "pos": 7})
    assert decode(prog.words[2])[1]["when"] == 3


SETUP = """
        mode    4, pass
        ldi     r0, 0x8000
        ldi     r1, 0x8000
        match   r0, r1
        arm     4, f0, now once trig
        sri     0
        nop
        in      0
        nop
        halt
"""


def test_now_switches_on_the_shifting_instruction():
    m, prog = machine(SETUP)
    rows = []
    trig_at = None
    for t in range(14):
        pc = m.ctx[0].pc
        p = m.step(uio=0xFF)
        rows.append((t, pc, m.src[4]))
        if p["uo"] & 4 and trig_at is None:
            trig_at = pc
    in_pc = next(pc for pc, _, line, _ in prog.listing if line.startswith("in"))
    t, pc = first_change([r for r in rows if r[2] != 0], SRC["PASS"])
    assert pc == in_pc, "the line must switch on the same edge as the IN that completed the match"
    assert trig_at == in_pc, "trig pulses TRIG_OUT on the hit"


def test_masked_mismatch_and_position_gate():
    miss = SETUP.replace("ldi     r0, 0x8000", "ldi     r0, 0x0000")
    m, _ = machine(miss)
    assert all(src == SRC["PASS"] for _, _, src in trace(m, 14)[2:])
    gated = SETUP.replace("match   r0, r1", "bci 3\n        match   r0, r1 pos=1")
    m, _ = machine(gated)   # one shift leaves BC at 2, not 1: no hit
    assert all(src == SRC["PASS"] for _, _, src in trace(m, 16)[2:])
    gated = SETUP.replace("match   r0, r1", "bci 2\n        match   r0, r1 pos=1")
    m, _ = machine(gated)
    assert trace(m, 16)[-1][2] == SRC["F0"]


def test_once_disarms_and_continuous_refires():
    body = """
        mode    4, pass
        ldi     r0, 0x8000
        ldi     r1, 0x8000
        match   r0, r1
        arm     4, f0, now {once}
        in      0
        mode    4, pass
        in      0
        nop
        halt
"""
    m, _ = machine(body.format(once="once"))
    assert trace(m, 14)[-1][2] == SRC["PASS"]
    m, _ = machine(body.format(once=""))
    assert trace(m, 14)[-1][2] == SRC["F0"]
    m, _ = machine(body.format(once="").replace("        in      0\n        nop", "        arm off\n        in      0"))
    assert trace(m, 14)[-1][2] == SRC["PASS"]


def test_pulse_spans_one_period_between_half_points():
    # Ticks every 20 cycles. The hit queues the pulse: INV from the next half point, back to PASS 20 cycles later.
    m, _ = machine("""
        mode    4, pass
        tset    20
        ldi     r0, 0x8000
        ldi     r1, 0x8000
        match   r0, r1
        arm     4, inv, pulse once
lp:     in.t    0
        jmp     lp
""")
    rows = trace(m, 90)
    inv = [t for t, _, src in rows if src == SRC["INV"]]
    assert len(inv) == 20 and inv == list(range(inv[0], inv[0] + 20))
    c = m.ctx[0]
    assert c.mstate == "idle" and not c.armed   # a "once" pulse ends idle and disarmed, with PASS restored
    assert rows[-1][2] == SRC["PASS"]


def test_pulse_lands_on_the_half_period_point_exactly():
    m, _ = machine("""
        mode    4, pass
        tset    20
        ldi     r0, 0x8000
        ldi     r1, 0x8000
        match   r0, r1
        arm     4, inv, pulse once
        in.t    0
        halt
""")
    ticks, inv = [], []
    for t in range(80):
        m.step(uio=0xFF)
        if m.ctx[0].tick_now:
            ticks.append(t)
        if m.src[4] == SRC["INV"]:
            inv.append(t)
    # IN runs on the first tick. The pulse starts tp - tp>>1 = 10 cycles after it and lasts one period.
    assert inv[0] == ticks[0] + 10 and len(inv) == 20


def test_contexts_keep_their_own_rules():
    text = """
        mode    4, pass
        ldi     r0, 0x8000
        ldi     r1, 0x8000
        match   r0, r1
        arm     4, f0, now once
        in      0
        halt
c1:     mode    5, pass
        ldi     r0, 0x0000
        ldi     r1, 0x8000
        match   r0, r1
        arm     5, f1, now once
        in      1
        halt
.ctx1 c1
"""
    m, _ = machine(text, two=True)
    for _ in range(30):
        m.step(uio=0xFD)   # line 0 high, line 1 low
    assert m.src[4] == SRC["F0"] and m.src[5] == SRC["F1"]
    m, _ = machine(text, two=True)
    for _ in range(30):
        m.step(uio=0xFE)   # line 0 low, line 1 high: neither matches
    assert m.src[4] == SRC["PASS"] and m.src[5] == SRC["PASS"]


def test_pass_latency():
    m, _ = machine("mode 4, pass\nhalt")
    levels = [1] * 10 + [0] * 10
    out = []
    for lv in levels:
        p = m.step(uio=0xFE | lv)
        out.append((p["out"] >> 4) & 1)
    assert out.index(0, 3) - levels.index(0) == mr.LATENCY


# Scenario results (numbers in lab/explore/match-reflex.md). Kept to rates that run quickly.

def test_uart_case_flip_both_programs_at_40_cycles():
    for name in mr.PROGRAMS:
        p0 = mr.best_p0(name, 40, False)
        assert p0 is not None and mr.trial(name, 40, False, p0) == (True, True), name


def test_uart_case_flip_reflex_wins_at_6_cycles():
    assert mr.best_p0("base", 6, False) is None, "the current ISA can't place the flip at 6 cycles a bit"
    p0 = mr.best_p0("reflex", 6, False)
    assert p0 is not None and mr.trial("reflex", 6, False, p0) == (True, True)


def test_uart_case_flip_two_directions_reflex():
    p0 = mr.best_p0("reflex", 40, True)
    assert p0 is not None and mr.trial("reflex", 40, True, p0) == (True, True)


def test_sync_hunt_both_programs_at_24_cycles():
    for name in mr.SYNC_PROGRAMS:
        assert mr.sync_best(name, 24, False) is not None, name
