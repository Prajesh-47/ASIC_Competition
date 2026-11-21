# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""v2.1's SUB and CMPK in the assembler and the golden model. Run from model/: python -m pytest tests"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from splice.asm import AsmError, assemble  # noqa: E402
from splice.isa import decode, encode  # noqa: E402
from splice.model import Splice  # noqa: E402


def _run(source, cycles, nctx=1):
    prog = assemble(source)
    m = Splice()
    m.load(prog.words)
    m.nctx, m.ctx_pc = nctx, list(prog.ctx)
    m.start()
    trace = []
    for _ in range(cycles):
        m.step()
        trace.append(list(m.eng))
    return m, trace


def test_cmpk_is_two_words_and_cmpu_with_a_constant_picks_it():
    words = assemble("cmpk r2, 0xBEEF").words
    assert words == [encode("CMPK", a=2), 0xBEEF]
    assert assemble("cmpu r2, 0xBEEF").words == words
    assert assemble("cmpu sr, 400").words == [encode("CMPK", a=4), 400]
    assert decode(assemble("cmpu r0, r1").words[0])[0] == "CMPU"  # a register keeps the register form
    assert decode(assemble("sub r3, bc").words[0]) == ("SUB", {"d": 3, "s": 5})
    with pytest.raises(AsmError):
        assemble("cmpk r0, 0x10000")


def test_labels_after_cmpk_count_its_constant():
    prog = assemble("cmpu r0, 1000\nhere: jmp here")
    assert prog.labels["here"] == 2


@pytest.mark.parametrize("v, k, z, f", [(5, 6, 0, 1), (6, 6, 1, 0), (7, 6, 0, 0),
                                        (0, 0, 1, 0), (0, 0xFFFF, 0, 1), (0xFFFF, 0, 0, 0), (0xFFFF, 0xFFFF, 1, 0)])
def test_cmpk_is_unsigned_and_sets_z_and_f_like_cmpu(v, k, z, f):
    m, _ = _run(f"ldi r1, {v}\ncmpu r1, {k}\nhalt", 12)
    c = m.ctx[0]
    assert (c.Z, c.F) == (z, f)
    assert c.R == [0, v, 0, 0]  # no register is spent on the constant


def test_cmpk_reads_any_operand():
    m, _ = _run("sri 0x12, 8\ncmpu low, 0x12\nhalt", 12)  # LOW is SR >> 8
    assert m.ctx[0].Z == 1


def test_cmpk_takes_two_slots_and_never_runs_its_constant():
    # The constant is HALT's encoding: if it ran as an instruction, the SET after it never would.
    halt = encode("HALT")
    _, with_cmpk = _run(f"cmpk r0, {halt}\nset 0, 0\nhalt", 10)
    _, with_nops = _run("nop\nnop\nset 0, 0\nhalt", 10)
    assert with_cmpk == with_nops
    assert with_cmpk[-1][0] == 0


def test_a_jump_to_the_constant_runs_it_as_an_instruction():
    # Defined, if useless: the constant is a word like any other. The RTL must agree.
    m, _ = _run(f"jmp k\ncmpk r0, {encode('HALT')}\nset 0, 0\nhalt\n.equ k, 2", 10)
    assert m.ctx[0].halted and m.eng[0] == 1


def test_cmpk_wraps_past_the_last_word():
    m = Splice()
    m.load([0] * 510 + [encode("CMPK", a=0), 0x0001])
    m.prog[0] = encode("JMP", c=4, to=510)  # jmp nf: taken once, then CMPK sets F and it falls through
    m.prog[1] = encode("HALT")
    m.start()
    for _ in range(12):
        m.step()
    assert m.ctx[0].halted and m.ctx[0].F == 1


def test_four_contexts_each_compare_against_their_own_constant():
    src = "\n".join(f"c{k}: ldi r0, {100 * k}\ncmpu r0, {100 * k + 1 - k}\nhalt" for k in range(4))
    src += "\n.ctx1 c1\n.ctx2 c2\n.ctx3 c3"
    m, _ = _run(src, 40, nctx=4)
    # Context k compares 100k with 100k+1-k: less, equal, greater, greater
    assert [(c.Z, c.F) for c in m.ctx] == [(0, 1), (1, 0), (0, 0), (0, 0)]
    assert all(c.halted for c in m.ctx)


@pytest.mark.parametrize("a, b, z, f", [(7, 5, 0, 0), (7, 7, 1, 0), (5, 7, 0, 1), (0, 1, 0, 1), (0x8000, 1, 0, 0)])
def test_sub_wraps_and_sets_z_and_the_borrow(a, b, z, f):
    m, _ = _run(f"ldi r0, {a}\nldi r2, {b}\nsub r0, r2\nhalt", 12)
    c = m.ctx[0]
    assert c.R[0] == (a - b) & 0xFFFF and c.R[2] == b and (c.Z, c.F) == (z, f)


def test_sub_takes_bc_and_sr_operands():
    m, _ = _run("bci 3\nldi r1, 10\nsub r1, bc\nsri 4\nsub r1, sr\nhalt", 12)
    assert m.ctx[0].R[1] == 3 and m.ctx[0].F == 0
