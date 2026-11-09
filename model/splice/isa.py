# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""ISA v1: one table for the assembler and the golden model. See docs/architecture.md section 5.

Fields are packed left-aligned straight after the opcode, in the order listed. Unused low bits are 0.
"""

# mnemonic: (opcode bits, ((field, width), ...))
TABLE = {
    "TSET": ("000", (("p", 13),)),
    "JMP": ("0010", (("c", 3), ("to", 9))),
    "CALL": ("0011", (("to", 9),)),
    "DLY": ("0100", (("n", 12),)),
    "LDI": ("0101", (("r", 2), ("hl", 1), ("v", 8))),
    "SRI": ("0110", (("v", 8), ("sh", 4))),
    "SET": ("01110", (("line", 4), ("v", 1), ("tick", 1), ("side", 2), ("dly", 3))),
    "MODE": ("01111", (("line", 3), ("src", 3), ("tick", 1), ("dly", 4))),
    "OUT": ("10000", (("line", 4), ("ns", 1), ("tick", 1), ("side", 2), ("dly", 3))),
    "IN": ("10001", (("line", 4), ("tick", 1), ("side", 2), ("dly", 4))),
    "OUTB": ("10010", (("line", 4), ("tick", 1), ("side", 2), ("rel", 4))),
    "INB": ("10011", (("line", 4), ("tick", 1), ("side", 2), ("rel", 4))),
    "DJNZ": ("10100", (("r", 2), ("to", 9))),
    # Match reflex, in the codes reserved for it: compare SR on each shift-in, switch a line's source on a hit
    "ARM": ("10101", (("when", 2), ("once", 1), ("trig", 1), ("line", 3), ("src", 3))),
    "MATCH": ("10110", (("v", 2), ("m", 2), ("pe", 1), ("pos", 5))),
    "WAIT": ("101110", (("line", 4), ("c", 2), ("stamp", 1))),
    "ADD": ("101111", (("r", 2), ("v", 8))),
    "CMPI": ("110000", (("a", 2), ("v", 8))),
    "AND": ("11001101", (("d", 2), ("s", 3))),
    "BCI": ("11001110", (("n", 5),)),
    "CAPMARK": ("11001111", ()),
    "CAPOFF": ("11010000", ()),
    "CAPON": ("11010001", ()),
    "CMP": ("11010010", (("a", 3), ("b", 3))),
    "DLYR": ("11010011", (("r", 2),)),
    "HALT": ("11010100", ()),
    "IRQ": ("11010101", ()),
    "MOV": ("11010110", (("d", 3), ("s", 3))),
    "MSB": ("11010111", (("v", 1),)),
    "NOP": ("11011000", (("tick", 1), ("side", 2))),
    "PROBE": ("11011001", (("line", 4), ("tick", 1))),
    "PULL": ("11011010", ()),
    "PULLW": ("11011011", (("n", 5),)),
    "PUSH": ("11011100", ()),
    "RET": ("11011101", ()),
    "SIDE": ("11011110", (("line", 4),)),
    "TPHASE": ("11011111", (("mode", 2),)),
    "TRIG": ("11100000", ()),
    "TSRC": ("11100001", (("line", 4), ("want", 2))),
    "TSTB": ("11100010", ()),
    "WAITT": ("11100011", ()),
    "XOR": ("11100100", (("d", 2), ("s", 3))),
    # Added after v1 in free codes, so no existing opcode moved
    "ADDR": ("11100110", (("d", 2), ("s", 3))),
    "TSETR": ("11100111", (("s", 3),)),
    "POLY": ("11101000", (("s", 3),)),
    "CRC": ("11101001", (("r", 2), ("dir", 1))),
    "RAND": ("1110010100", (("r", 2),)),
    "SHL": ("1110010101", (("r", 2),)),
    "SHR": ("1110010110", (("r", 2),)),
}

COND = {"AL": 0, "Z": 1, "NZ": 2, "F": 3, "NF": 4, "BC0": 5, "BCN": 6}
WAITC = {"LO": 0, "HI": 1, "RISE": 2, "FALL": 3}
WANT = {"FALL": 0, "RISE": 1, "BOTH": 2, "OFF": 3}
PHASE = {"PERIOD": 0, "HALF": 1, "THREEHALF": 2, "R1": 3}
CRCDIR = {"LSB": 0, "MSB": 1}  # LSB: shift right, reflected polynomial. MSB: shift left, left-aligned polynomial
WHEN = {"NOW": 0, "HALF": 1, "PULSE": 2, "OFF": 3}  # ARM: on the hit, at the next half-period point, or a one-period pulse
SRC = {"HIZ": 0, "ENG": 1, "PASS": 2, "INV": 3, "F0": 4, "F1": 5, "ODB": 6}
# 3-bit register operand. As a MOV destination, 7 seeds the LFSR; as a source it is SR >> 8.
OPERAND = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "SR": 4, "BC": 5, "TO": 6, "LOW": 7, "SEED": 7}


def encode(op, **fields):
    """Pack one instruction. Raises ValueError if a value doesn't fit its field."""
    code, layout = TABLE[op]
    word, pos = int(code, 2) << (16 - len(code)), 16 - len(code)
    for name, width in layout:
        value = fields.pop(name, 0)
        if not 0 <= value < (1 << width):
            raise ValueError(f"{op}: {name}={value} doesn't fit {width} bits")
        pos -= width
        word |= value << pos
    if fields:
        raise ValueError(f"{op}: unknown fields {sorted(fields)}")
    return word


def decode(word):
    """(mnemonic, fields) for a word. Reserved encodings decode as ("NOP", {}) with no qualifiers."""
    bits = format(word & 0xFFFF, "016b")
    for op, (code, layout) in TABLE.items():
        if bits.startswith(code):
            pos, fields = len(code), {}
            for name, width in layout:
                fields[name] = int(bits[pos:pos + width], 2)
                pos += width
            return op, fields
    return "NOP", {"tick": 0, "side": 0}
