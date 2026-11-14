# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Assembler and model unit tests. Run from model/: python -m tests.test_asm"""
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from splice.asm import AsmError, assemble  # noqa: E402
from splice.isa import COND, TABLE, decode, encode  # noqa: E402
from splice.model import Splice  # noqa: E402


def test_every_opcode_roundtrips():
    for op, (_, layout) in TABLE.items():
        fields = {name: (1 << width) - 1 for name, width in layout}
        assert decode(encode(op, **fields)) == (op, fields), op


def test_opcodes_are_a_prefix_code():
    codes = [c for c, _ in TABLE.values()]
    assert not [(a, b) for a in codes for b in codes if a is not b and b.startswith(a)]


def test_labels_after_constant_expressions():
    # The bug that made this test exist: ldi with an expression shifted every later label.
    prog = assemble(".equ K, 3\n ldi r1, (K + 1) * 40 - 1\ntop: nop\n jmp top\n")
    assert prog.labels["top"] == 1 and decode(prog.words[2]) == ("JMP", {"c": COND["AL"], "to": 1})


def test_ldi_uses_two_words_only_when_needed():
    assert len(assemble("ldi r0, 255").words) == 1
    assert len(assemble("ldi r0, 256").words) == 2
    assert len(assemble(".equ N, 300\nldi r0, N").words) == 2


def test_forward_reference_grows_correctly():
    prog = assemble("ldi r0, end\n nop\nend: halt\n")
    assert prog.labels["end"] == 2 and decode(prog.words[0])[1]["v"] == 2


def test_delay_spends_exact_cycles():
    for n in (0, 1, 2, 3, 100, 4096, 4097, 9000):
        words = assemble(f"delay {n}\nset 8, 1\nhalt").words
        model = Splice()
        model.load(words)
        model.start()
        cycles = 0
        while not model.pins()["uo"] & 0x10:
            model.step()
            cycles += 1
            assert cycles < 20000
        assert cycles == n + 2, f"delay {n}: {cycles}"  # one fetch cycle, one for the SET


def test_defines_override_equ():
    assert decode(assemble(".equ B, 434\ntset B\n").words[0])[1]["p"] == 434
    assert decode(assemble(".equ B, 434\ntset B\n", {"B": 40}).words[0])[1]["p"] == 40


def test_register_operand_picks_register_form():
    assert decode(assemble("add r2, r1").words[0]) == ("ADDR", {"d": 2, "s": 1})
    assert decode(assemble("add r0, bc").words[0]) == ("ADDR", {"d": 0, "s": 5})
    assert decode(assemble("add r0, 7").words[0]) == ("ADD", {"r": 0, "v": 7})
    assert decode(assemble(".equ K, 9\nadd r3, K").words[0]) == ("ADD", {"r": 3, "v": 9})
    assert decode(assemble("tset r1").words[0]) == ("TSETR", {"s": 1})
    assert decode(assemble("tset 434").words[0]) == ("TSET", {"p": 434})
    assert decode(assemble("tsetr sr").words[0]) == ("TSETR", {"s": 4})


def _run(source, cycles):
    model = Splice()
    model.load(assemble(source).words)
    model.start()
    for _ in range(cycles):
        model.step()
    return model


def test_addr_carries_wraps_and_sets_z():
    m = _run("ldi r0, 0x00FF\nldi r1, 0x0001\nadd r0, r1\nhalt", 12)
    assert m.ctx[0].R[0] == 0x0100 and m.ctx[0].Z == 0
    m = _run("ldi r0, 0xFFFF\nldi r1, 0x0001\nadd r0, r1\nhalt", 12)
    assert m.ctx[0].R[0] == 0 and m.ctx[0].Z == 1
    m = _run("ldi r2, 0x1234\nadd r2, r2\nhalt", 12)  # source and destination the same
    assert m.ctx[0].R[2] == 0x2468


def test_tsetr_period_past_13_bits():
    # DALI's half bit at 50 MHz. The old 13-bit timer stopped at 8,191 cycles.
    period = 20833
    words = assemble(f"ldi r1, {period}\ntset r1\ntop: set.t 8, 1\n set.t 8, 0\n jmp top").words
    model = Splice()
    model.load(words)
    model.start()
    rises, was = [], 0
    for t in range(6 * period):
        now = (model.step()["uo"] >> 4) & 1
        if now and not was:
            rises.append(t)
        was = now
    assert len(rises) >= 2 and all(b - a == 2 * period for a, b in zip(rises, rises[1:])), rises


def _crc_of(data, poly, init, msb_first):
    """Run CRC over bytes on the model: each byte goes out on line 15 (no pin), and CRC steps on that bit."""
    order = "msb 1" if msb_first else "msb 0"
    shift = 8 if msb_first else 0  # SRI puts the byte where OUT shifts from first
    lines = [order, f"ldi r1, {poly}", "poly r1", f"ldi r0, {init}"]
    for byte in data:
        lines.append(f"sri {byte}, {shift}")
        lines += ["out 15", f"crc r0, {'msb' if msb_first else 'lsb'}"] * 8
    lines.append("halt")
    m = _run("\n".join(lines), 40 * len(data) + 40)
    assert m.ctx[0].halted
    return m.ctx[0].R[0]


def test_crc_matches_published_check_values():
    check = b"123456789"
    assert _crc_of(check, 0x1021, 0, True) == 0x31C3         # CRC-16/XMODEM (SD data lines)
    assert _crc_of(check, 0x1021, 0xFFFF, True) == 0x29B1    # CRC-16/IBM-3740 (CCITT-FALSE)
    assert _crc_of(check, 0xA001, 0, False) == 0xBB3D        # CRC-16/ARC, reflected
    assert _crc_of(check, 0x8408, 0xFFFF, False) ^ 0xFFFF == 0x906E  # CRC-16/X-25 (HDLC FCS-16)
    assert _crc_of(check, 0x4599 << 1, 0, True) >> 1 == 0x059E      # CRC-15/CAN, left aligned


def test_crc_uses_the_bit_last_shifted_in():
    # IN shifts the line into SR, and CRC must step on that bit, not on SR's next output bit.
    m = _run("ldi r1, 0x8000\npoly r1\nldi r0, 0\nin 8\ncrc r0, msb\nhalt", 12)  # AUX0 reads 1 when idle
    assert m.ctx[0].R[0] == 0x8000 and m.ctx[0].lastb == 1


def _serial_crc(bits, poly=0x1021):
    crc = 0
    for b in bits:
        top = ((crc >> 15) & 1) ^ b
        crc = ((crc << 1) & 0xFFFF) ^ (poly if top else 0)
    return crc


def test_lanes_shift_four_lines_and_crcw_matches_four_serial_crcs():
    # explore/isa-lanes-table. SR = 0xB5E1 goes out as nibbles B, 5, E, 1 on lines 4-7, oldest first, and
    # CRCW on each lane equals a serial CRC of that lane's 4 bits.
    m = _run("msb 1\nlanes 4\nldi r0, 0x1021\npoly r0\nldi r0, 0\nldi r1, 0\nldi r2, 0\nldi r3, 0\n"
             "ldi r0, 0xB5E1\nmov sr, r0\nldi r0, 0\nout 4 ns\ncrcw r0, msb, 0\ncrcw r1, msb, 1\n"
             "crcw r2, msb, 2\ncrcw r3, msb, 3\nhalt", 40)
    c = m.ctx[0]
    nibbles = [0xB, 0x5, 0xE, 0x1]
    for lane in range(4):
        assert c.R[lane] == _serial_crc([(n >> lane) & 1 for n in nibbles]), lane
    assert m.eng[4:8] == [1, 1, 0, 1]  # nibble B on lines 4-7, line 4 being bit 0


def test_ldp_reads_a_table_and_jmpr_jumps_to_a_register():
    m = _run("ldi r1, tab\nadd r1, 2\nldp r2, r1\nldi r3, there\njmpr r3\nhalt\nthere: mov sr, r2\npush\nhalt\n"
             "tab: .word 0x1111, 0x2222, 0xBEEF", 30)
    assert m.e2h == [0xBEEF]


def test_ldp_reaches_the_whole_512_word_memory():
    # The address is 9 bits, as in the RTL. The model once kept 8, so a table past word 255 read word addr-256
    m = _run("ldi r1, tab\nldp r2, r1\nmov sr, r2\npush\nhalt\n" + ".word 0\n" * 300 + "tab: .word 0xCAFE", 30)
    assert m.e2h == [0xCAFE]


def test_errors_are_rejected():
    for bad in ("set 16, 1", "mode 4, bogus", "jmp nowhere", "outb 4, later\nnop\nlater: nop", "ldi r9, 1",
                "dly 5000", "bogus 1", "tset 8192", "set 4, 1 side=2"):
        try:
            assemble(bad)
        except (AsmError, ValueError):
            continue
        raise AssertionError(f"accepted: {bad}")


def test_all_firmware_assembles():
    files = glob.glob(os.path.join(os.path.dirname(__file__), "..", "firmware", "*.s"))
    assert files
    for path in files:
        with open(path, encoding="utf-8") as source:
            assert assemble(source.read()).words, path


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print("PASS", fn.__name__)
    print(f"{len(tests)}/{len(tests)} passed")
