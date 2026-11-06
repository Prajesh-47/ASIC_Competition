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
    "(reserved) CODE": (6, {"mode": 4, "n": 3, "pair": 3}),
    "(reserved) CODE2": (6, {"mode": 4, "n": 3, "pair": 3}),
    "(reserved) CRCSR": (8, {"inv": 1, "keep": 1, "shift": 1}),
    "(reserved) ARM": (5, {"rule": 1, "trig": 3, "c": 1, "line": 3, "src": 3}),
    "(reserved) MATCH": (5, {"rule": 1, "trig": 3, "line": 3, "src": 4}),
}
ALL = {**TABLE, **RESERVED}

# Listed in the order the map should read: frequent, wide formats first.
ORDER = ["TSET", "JMP", "CALL", "DLY", "LDI", "SRI", "SET", "MODE", "OUT", "IN", "OUTB", "INB", "DJNZ",
         "(reserved) ARM", "(reserved) MATCH", "WAIT", "ADD", "CMPI", "(reserved) CODE", "(reserved) CODE2"]


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


def check(codes):
    vals = list(codes.values())
    for a in vals:
        for b in vals:
            assert a is b or not b.startswith(a), (a, b)
    for name, (bits, fields) in ALL.items():
        assert len(codes[name]) == bits
        assert bits + sum(fields.values()) <= 16, name
    assert sum(2.0 ** -len(c) for c in vals) <= 1


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
    codes = assign()
    check(codes)
    n, unused = firmware_fits()
    print(f"prefix code OK: {len(ALL)} entries, Kraft sum {sum(2.0 ** -len(c) for c in codes.values()):.3f}")
    print(f"{n} study programs fit. Not exercised by any program: {unused}")
    print()
    print("| Opcode | Instruction | Fields (bits) |")
    print("|---|---|---|")
    for name in sorted(ALL, key=lambda k: codes[k]):
        fields = ", ".join(f"{k} {v}" for k, v in ALL[name][1].items()) or "None"
        print(f"| `{codes[name]}` | `{name}` | {fields} |")


if __name__ == "__main__":
    main()
