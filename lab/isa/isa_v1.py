# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""ISA v1 opcode map, generated so it can't drift from the table the firmware was checked against.

Assigns a concrete prefix code, proves no opcode is a prefix of another and that every format fits 16
bits, reassembles all study firmware against it, and prints the table used in docs/architecture.md.
"""
import isa_enc as enc

# V1 is the Loom table, the control operations from the spec, and reserved encodings.
TABLE = dict(enc.V3)
TABLE.pop("WRAP", None)
TABLE.pop("TSTB", None)
TABLE["TSTB"] = (8, {})
for op in ("IRQ", "TRIG", "CAPON", "CAPOFF", "CAPMARK"):
    TABLE[op] = (8, {})
RESERVED = {
    # v2 line coder: NRZI, bit stuffing and Manchester on single-lane OUT, OUTB, IN and INB
    "CODE": (6, {"nrzi": 1, "lvl": 1, "kind": 1, "n": 3, "man": 1, "pol": 1, "diff": 1, "sync": 1}),
    "(reserved) CODE2": (6, {"mode": 4, "n": 3, "pair": 3}),
    "(reserved) CRCSR": (8, {"inv": 1, "keep": 1, "shift": 1}),
    "(reserved) ARM": (5, {"rule": 1, "trig": 3, "c": 1, "line": 3, "src": 3}),
    "(reserved) MATCH": (5, {"rule": 1, "trig": 3, "line": 3, "src": 4}),
}
ALL = {**TABLE, **RESERVED}

# Added after v1, pinned in free codes. Assigning them canonically would renumber every 8-bit opcode.
EXTENSIONS = {
    "ADDR": ("11100110", {"d": 2, "s": 3}),   # Rd += operand: the trial's LIN checksum
    "TSETR": ("11100111", {"s": 3}),          # 16-bit timer period from a register: LIN auto-baud, DALI
    "POLY": ("11101000", {"s": 3}),           # CRC polynomial from a register, so it doesn't hold one
    "CRC": ("11101001", {"r": 2, "dir": 1, "word": 1, "lane": 3}),  # one CRC step on the bit last shifted,
                                              # or (word=1) a whole word of one lane: SD, CAN, USB, HDLC
    "CMPU": ("11101010", {"a": 3, "b": 3}),   # unsigned compare: Z equal, F less than. Interval classing
    "TCAP": ("11101011", {"r": 2, "tick": 1}),   # cycles between the last two ticks: rate-agnostic receive
    # explore/isa-lanes-int: lane-wide shifts, table reads and a computed jump. 11101100 is free: its
    # ROUTE mailbox was dropped for the link FIFOs between contexts
    "LDP": ("11101101", {"d": 2, "s": 2}),    # table read: Rd = program word at address Rs
    "JMPR": ("11101110", {"s": 3}),           # computed jump to an operand
    "LANES": ("11101111", {"w": 2}),          # OUT/IN/OUTB/INB move 1, 2, 4 or 8 lines at once
}

# Listed in the order the map should read: frequent, wide formats first.
ORDER = ["TSET", "JMP", "CALL", "DLY", "LDI", "SRI", "SET", "MODE", "OUT", "IN", "OUTB", "INB", "DJNZ",
         "(reserved) ARM", "(reserved) MATCH", "WAIT", "ADD", "CMPI", "CODE", "(reserved) CODE2"]


def assign():
    """Canonical prefix code: shortest first, each code the next free value at its length."""
    names = sorted(ALL, key=lambda k: (ALL[k][0], ORDER.index(k) if k in ORDER else 99, k))
    code, prev, out = 0, None, {}
    for name in names:
        bits = ALL[name][0]
        if prev is not None:
            code = (code + 1) << (bits - prev)
        out[name] = format(code, f"0{bits}b")
        prev = bits
    return out


def with_extensions(codes):
    out = dict(codes)
    for name, (code, _) in EXTENSIONS.items():
        out[name] = code
    return out


def fields_of(name):
    return EXTENSIONS[name][1] if name in EXTENSIONS else ALL[name][1]


def check(codes):
    vals = list(codes.values())
    for a in vals:
        for b in vals:
            assert a is b or not b.startswith(a), (a, b)
    for name, (bits, fields) in ALL.items():
        assert len(codes[name]) == bits
        assert bits + sum(fields.values()) <= 16, name
    for name, (code, fields) in EXTENSIONS.items():
        assert len(code) + sum(fields.values()) <= 16, name
    assert sum(2.0 ** -len(c) for c in vals) <= 1


def matches_model(codes):
    """The model's table, which the assembler and RTL tests use, must match this map exactly."""
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "model"))
    from splice.isa import TABLE as MODEL
    for name, (code, layout) in MODEL.items():
        assert codes.get(name) == code, (name, code, codes.get(name))
        assert dict(layout) == fields_of(name), name
    assert set(MODEL) == {n for n in codes if not n.startswith("(reserved)")}, set(MODEL) ^ set(codes)


def firmware_fits():
    import firmware as fw
    import flex_fw as ff
    import heldout as h
    progs = [fw.uart_tx("V3", 434), fw.uart_rx("V3", 434), fw.spi_master("V3", 25), fw.i2c_master("V3", 125),
             fw.uart_flip("V3", 434, 3), fw.uart_rewrite("V3", 434, 0xA5, 0x3C), fw.fuzz_flip("V3", 434),
             fw.spi_inline("V3T", 2, 0x3C), ff.jtag_idcode(25), ff.swd_read(25), ff.ps2_rx(),
             ff.spi_target(0), ff.spi_target(1), ff.i2c_target(0x50), ff.i2c_nack(27), ff.manchester_soft(20),
             ff.usb_tx_soft(33), ff.can_tx_soft(100), h.ws2812_fw(), h.onewire_fw()]
    used = set()
    for prog in progs:
        for ins in prog:
            enc.fits("V3", ins)
            assert ins.op in TABLE, ins.op
            used.add(ins.op)
    return len(progs), sorted(set(TABLE) - used)


def main():
    codes = with_extensions(assign())
    check(codes)
    matches_model(codes)
    n, unused = firmware_fits()
    print(f"prefix code OK: {len(codes)} entries, Kraft sum {sum(2.0 ** -len(c) for c in codes.values()):.3f}")
    print("model table matches")
    print(f"{n} study programs fit. Not exercised by any program: {unused}")
    print()
    print("| Opcode | Instruction | Fields (bits) |")
    print("|---|---|---|")
    for name in sorted(codes, key=lambda k: codes[k]):
        fields = ", ".join(f"{k} {v}" for k, v in fields_of(name).items()) or "None"
        print(f"| `{codes[name]}` | `{name}` | {fields} |")


if __name__ == "__main__":
    main()
