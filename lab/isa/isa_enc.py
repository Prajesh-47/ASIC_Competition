# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""16-bit encoding tables. A candidate only counts if its firmware fits these words.

Each entry is: op -> (opcode bits, {field: bits}). `fits` rejects any instruction whose values
overflow, and `kraft` proves the opcodes form a valid prefix code.
"""
L, R, C = 4, 2, 3  # line index, register, condition
SRC = 3

BASE = {
    "NOP": (8, {}), "HALT": (8, {}), "PUSH": (8, {}), "PULL": (8, {}), "MSB": (8, {"v": 1}),
    "SET": (4, {"line": L, "v": 1, "dly": 7}),
    "MODE": (4, {"line": 3, "src": SRC, "dly": 6}),
    "WAIT": (6, {"line": L, "c": 2, "stamp": 1}),
    "DLY": (4, {"n": 12}),
    "DLYR": (8, {"r": R}),
    "IN": (4, {"line": L, "dly": 8}),
    "OUT": (4, {"line": L, "ns": 1, "dly": 7}),
    "LDI": (4, {"r": R, "hl": 1, "v": 8, }),
    "MOV": (8, {"d": 3, "s": 3}),
    "ADD": (6, {"r": R, "v": 8}),
    "XOR": (8, {"d": R, "s": 3}),
    "AND": (8, {"d": R, "s": 3}),
    "CMP": (8, {"a": 3, "b": 3}),
    "CMPI": (6, {"a": 2, "v": 8}),
    "SHR": (10, {"r": R}),
    "SHL": (10, {"r": R}),
    "JMP": (4, {"c": C, "to": 9}),
    "DJNZ": (5, {"r": R, "to": 9}),
    "PROBE": (8, {"line": L}),
    "WRAP": (16, {}),
}

# Spec v0: section 5 of architecture.md, with BC and TO loaded by MOV.
V0 = dict(BASE)

# V1: RP2040-PIO style. A clock divider, side-set shared with a short delay, autoload, wrap.
V1 = dict(BASE)
V1.update({
    "SIDE": (8, {"line": L}),
    "PULLW": (5, {"n": 5, "side": 2, "dly": 3}),
    "BCI": (8, {"n": 5}),
    "OUT": (5, {"line": L, "ns": 1, "side": 2, "dly": 3}),
    "IN": (5, {"line": L, "side": 2, "dly": 3}),
    "SET": (5, {"line": L, "v": 1, "side": 2, "dly": 2}),
    "NOP": (8, {"side": 2, "dly": 3}),
    # Short reach and 4 conditions are the price of a delay field on JMP.
    "JMP": (3, {"c": 2, "side": 2, "dly": 3, "to": 6}),
})

# V2: V0 plus a deadline timer (PRET delay-until, as a period).
V2 = dict(BASE)
V2.update({
    "TSET": (3, {"p": 13}),  # with `first` instead of `p`: restart the phase, from a preset or R1
    "WAITT": (8, {}),
})

# V3: V0 plus the timer, fused bit instructions and tick qualifiers.
V3 = dict(V2)
V3.update({
    "SIDE": (8, {"line": L}),
    "PULLW": (8, {"n": 5}),
    "BCI": (8, {"n": 5}),
    "RAND": (10, {"r": R}),
    "SET": (5, {"line": L, "v": 1, "tick": 1, "side": 2, "dly": 3}),
    "OUTB": (5, {"line": L, "tick": 1, "side": 2, "rel": 4}),
    "INB": (5, {"line": L, "tick": 1, "side": 2, "rel": 4}),
    "OUT": (5, {"line": L, "ns": 1, "tick": 1, "side": 2, "dly": 3}),
    "IN": (5, {"line": L, "tick": 1, "side": 2, "dly": 4}),
    "MODE": (5, {"line": 3, "src": SRC, "tick": 1, "dly": 4}),
    "NOP": (8, {"tick": 1, "side": 2}),
    "PROBE": (8, {"line": L, "tick": 1}),
    "SRI": (4, {"v": 8, "sh": 4}),
})

V3["TSRC"] = (8, {"line": L, "want": 2})
V3["TSTB"] = (8, {})  # flexibility study: test the next output bit
V3["CALL"] = (4, {"to": 9})  # flexibility study: one-level call with a link register
V3["RET"] = (8, {})

# V4: V3 plus hardware rules. ARM reads delay and width from registers.
V4 = dict(V3)
V4.update({"ARM": (5, {"rule": 1, "trig": 3, "c": 1, "line": 3, "src": 3})})

# V7: V4 plus a hardware byte matcher. MATCH reads period, pattern, mask and action times from registers.
V7 = dict(V4)
V7.update({"MATCH": (5, {"rule": 1, "trig": 3, "line": 3, "src": 4})})

# V8: V3 plus line-coding hardware: CRC, bit stuffing, NRZI, Manchester, arbitration.
V8 = dict(V3)
V8.update({"CODE": (6, {"mode": 4, "n": 3, "pair": 3}), "CRCSR": (8, {"inv": 1, "keep": 1, "shift": 1}),
           "CODE2": (6, {"mode": 4, "n": 3, "pair": 3})})

VARIANTS = {"V8": V8, "V0": V0, "V1": V1, "V2": V2, "V3": V3, "V4": V4, "V5": V4, "V7": V7}


def kraft(table):
    """A prefix code exists if and only if this sum is at most 1."""
    return sum(2.0 ** -(ops) for ops, _ in table.values())


def fits(variant, ins):
    """Raise if this instruction can't be encoded in 16 bits on this variant."""
    table = VARIANTS[variant]
    if ins.op not in table:
        raise ValueError(f"{variant}: no {ins.op}")
    opbits, fields = table[ins.op]
    if opbits + sum(fields.values()) > 16:
        raise ValueError(f"{variant}: {ins.op} format is {opbits + sum(fields.values())} bits")
    if ins.dly:
        if "dly" not in fields or ins.dly >= 2 ** fields["dly"]:
            raise ValueError(f"{variant}: {ins.op} dly={ins.dly} doesn't fit")
    if ins.side is not None and "side" not in fields:
        raise ValueError(f"{variant}: {ins.op} has no side-set")
    if ins.op == "TSET" and "p" in ins.a and "first" in ins.a:
        raise ValueError(f"{variant}: TSET sets either the period or the phase")
    if ins.tick and "tick" not in fields:
        raise ValueError(f"{variant}: {ins.op} has no tick qualifier")
    for name, value in ins.a.items():
        if isinstance(value, int) and name in fields and value >= 2 ** fields[name]:
            raise ValueError(f"{variant}: {ins.op}.{name}={value} doesn't fit {fields[name]} bits")
    return True
