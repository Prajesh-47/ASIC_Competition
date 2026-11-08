# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""SPLICE assembler. Usage: python -m splice.asm firmware/uart_tx.s [-o out.hex] [-D BIT=434]

    label:
    set.t  4, 1  side=1 dly=3     ; ".t" waits for the next tick, side= also drives the SIDE line
    outb.t 4, label               ; branch back within 15 words
    out    4 ns                   ; hold SR, for full duplex
    wait   0, fall
    jmp    bcn, label             ; or: jmp label
    ldi    r0, 0x1234             ; pseudo: two words if the value needs them
    delay  1000                   ; pseudo: spend exactly this many cycles
    .equ   BIT, 434
    .ctx1  label                  ; where context 1 starts

Operands may use + - * / // << >> and parentheses over numbers and .equ names.
"""
import ast
import argparse
import operator
import re
import sys

from .isa import COND, CRCDIR, OPERAND, PHASE, SRC, TABLE, WAITC, WANT, encode


class AsmError(Exception):
    pass


OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.floordiv,
       ast.FloorDiv: operator.floordiv, ast.LShift: operator.lshift, ast.RShift: operator.rshift,
       ast.BitOr: operator.or_, ast.BitAnd: operator.and_, ast.BitXor: operator.xor}


def evaluate(text, names):
    """Evaluate a constant expression without ever calling eval()."""
    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise AsmError(f"unknown name {node.id}")
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            return OPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -walk(node.operand)
        raise AsmError(f"bad expression: {text}")
    try:
        return walk(ast.parse(text.strip(), mode="eval"))
    except SyntaxError as err:
        raise AsmError(f"bad expression: {text}") from err


class Program:
    def __init__(self, words, labels, ctx1, listing):
        self.words, self.labels, self.ctx1, self.listing = words, labels, ctx1, listing


def _clean(line):
    return line.split(";", 1)[0].strip()


def _delay_words(n):
    """Fewest instructions that spend exactly n cycles, as (op, fields) tuples."""
    if n < 0:
        raise AsmError(f"delay of {n} cycles can't be met")
    out = []
    while n > 0:
        if n == 1:
            out.append(("NOP", {}))
            n = 0
        else:
            step = min(n - 1, 4095)
            out.append(("DLY", {"n": step}))
            n -= step + 1
    return out


def _translate(mnemonic, args, flags, named, names, labels, pc, final):
    """Return a list of (op, fields). `final` is False on the sizing pass, when labels may be missing."""
    op = mnemonic.upper()
    tick = 1 if flags.get("t") else 0

    def num(text):
        try:
            return evaluate(text, {**names, **labels})
        except AsmError:
            if final:
                raise
            return 0

    def side():
        if "side" not in named:
            return 0
        value = num(named["side"])
        if value not in (0, 1):
            raise AsmError("side= must be 0 or 1")
        return 2 | value

    def key(table, text):
        if text.upper() not in table:
            raise AsmError(f"expected one of {sorted(table)}, got {text}")
        return table[text.upper()]

    def reg(text):
        m = re.fullmatch(r"[rR]([0-3])", text)
        if not m:
            raise AsmError(f"expected r0-r3, got {text}")
        return int(m.group(1))

    dly = num(named["dly"]) if "dly" in named else 0
    if op == "LDI":
        r, value = reg(args[0]), num(args[1])
        if not 0 <= value <= 0xFFFF:
            raise AsmError(f"ldi value {value} out of range")
        out = [("LDI", {"r": r, "hl": 0, "v": value & 0xFF})]
        if value > 255:  # one word when it fits. The sizing passes repeat until labels stop moving
            out.append(("LDI", {"r": r, "hl": 1, "v": value >> 8}))
        return out
    if op == "DELAY":
        return _delay_words(num(args[0])) if final else _delay_words(max(num(args[0]), 0))
    if op == "SET":
        return [(op, {"line": num(args[0]), "v": num(args[1]), "tick": tick, "side": side(), "dly": dly})]
    if op == "MODE":
        return [(op, {"line": num(args[0]), "src": key(SRC, args[1]), "tick": tick, "dly": dly})]
    if op == "OUT":
        return [(op, {"line": num(args[0]), "ns": 1 if "ns" in flags else 0, "tick": tick, "side": side(), "dly": dly})]
    if op == "IN":
        return [(op, {"line": num(args[0]), "tick": tick, "side": side(), "dly": dly})]
    if op in ("OUTB", "INB"):
        rel = pc - num(args[1]) if final else 0
        if not 0 <= rel <= 15:
            raise AsmError(f"{op} can only branch back up to 15 words, got {rel}")
        return [(op, {"line": num(args[0]), "tick": tick, "side": side(), "rel": rel})]
    if op == "JMP":
        if len(args) == 1:
            return [(op, {"c": COND["AL"], "to": num(args[0])})]
        return [(op, {"c": key(COND, args[0]), "to": num(args[1])})]
    if op == "CALL":
        return [(op, {"to": num(args[0])})]
    if op == "DJNZ":
        return [(op, {"r": reg(args[0]), "to": num(args[1])})]
    if op == "WAIT":
        return [(op, {"line": num(args[0]), "c": key(WAITC, args[1]), "stamp": 1 if "stamp" in flags else 0})]
    # A register operand picks the register form: "add r0, r1" is ADDR, "tset r1" is TSETR
    if op == "TSETR" or (op == "TSET" and args[0].upper() in OPERAND):
        return [("TSETR", {"s": key(OPERAND, args[0])})]
    if op == "TSET":
        return [(op, {"p": num(args[0])})]
    if op == "POLY":
        return [(op, {"s": key(OPERAND, args[0])})]
    if op == "CRC":
        return [(op, {"r": reg(args[0]), "dir": key(CRCDIR, args[1])})]
    if op == "TPHASE":
        return [(op, {"mode": key(PHASE, args[0])})]
    if op == "TSRC":
        if args[0].upper() == "OFF":
            return [(op, {"line": 0, "want": WANT["OFF"]})]
        return [(op, {"line": num(args[0]), "want": key(WANT, args[1])})]
    if op == "DLY":
        return [(op, {"n": num(args[0])})]
    if op == "DLYR":
        return [(op, {"r": reg(args[0])})]
    if op == "SRI":
        return [(op, {"v": num(args[0]), "sh": num(args[1]) if len(args) > 1 else 0})]
    if op == "ADDR" or (op == "ADD" and args[1].upper() in OPERAND):
        return [("ADDR", {"d": reg(args[0]), "s": key(OPERAND, args[1])})]
    if op in ("ADD", "CMPI"):
        field = "r" if op == "ADD" else "a"
        return [(op, {field: reg(args[0]), "v": num(args[1])})]
    if op in ("AND", "XOR"):
        return [(op, {"d": reg(args[0]), "s": key(OPERAND, args[1])})]
    if op == "MOV":
        return [(op, {"d": key(OPERAND, args[0]), "s": key(OPERAND, args[1])})]
    if op == "CMP":
        return [(op, {"a": key(OPERAND, args[0]), "b": key(OPERAND, args[1])})]
    if op in ("BCI", "PULLW"):
        return [(op, {"n": num(args[0])})]
    if op == "MSB":
        return [(op, {"v": num(args[0])})]
    if op == "NOP":
        return [(op, {"tick": tick, "side": side()})]
    if op == "PROBE":
        return [(op, {"line": num(args[0]), "tick": tick})]
    if op == "SIDE":
        return [(op, {"line": num(args[0])})]
    if op in ("SHL", "SHR", "RAND"):
        return [(op, {"r": reg(args[0])})]
    if op in TABLE and not TABLE[op][1]:
        return [(op, {})]
    raise AsmError(f"unknown instruction {mnemonic}")


def _parse(line):
    head, _, rest = line.partition(" ")
    parts = head.split(".")
    mnemonic, flags = parts[0], {p: True for p in parts[1:]}
    named = {}
    # name=value pairs and bare flags come after the comma-separated operands
    tail = re.findall(r"(\w+)=([^\s,]+)", rest)
    for k, v in tail:
        named[k] = v
    rest = re.sub(r"\w+=[^\s,]+", "", rest)
    for word in ("ns", "stamp"):
        if re.search(rf"\b{word}\b", rest):
            flags[word] = True
            rest = re.sub(rf"\b{word}\b", "", rest)
    args = [a.strip() for a in rest.split(",") if a.strip()]
    return mnemonic, flags, named, args


def assemble(source, defines=None):
    """Assemble text into a Program, or raise AsmError with the line number."""
    names, lines, ctx1_label = dict(defines or {}), [], None
    for number, raw in enumerate(source.splitlines(), 1):
        line = _clean(raw)
        while True:
            m = re.match(r"(\w+):\s*(.*)", line)
            if not m:
                break
            lines.append((number, m.group(1) + ":"))
            line = m.group(2)
        if line:
            lines.append((number, line))
    labels = {}
    # Sizes depend on values and values on labels, so iterate until the layout stops moving.
    for _ in range(8):
        pc, new = 0, {}
        for number, line in lines:
            try:
                if line.endswith(":"):
                    new[line[:-1]] = pc
                elif line.startswith(".equ"):
                    name, value = line[4:].split(",", 1)
                    if name.strip() not in (defines or {}):  # a -D on the command line wins
                        names[name.strip()] = evaluate(value, names)
                elif line.startswith(".ctx1"):
                    ctx1_label = line[5:].strip()
                else:
                    pc += len(_translate(*_args(line), names, labels, pc, False))
            except (AsmError, IndexError) as err:
                raise AsmError(f"line {number}: {line}: {err}") from err
        if new == labels:
            break
        labels = new
    else:
        raise AsmError("label layout didn't settle")
    words, listing, pc = [], [], 0
    for number, line in lines:
        if line.endswith(":"):
            if labels[line[:-1]] != pc:  # a wrong label would silently misdirect a jump
                raise AsmError(f"line {number}: label {line[:-1]} moved from {labels[line[:-1]]} to {pc}")
            continue
        if line.startswith("."):
            continue
        try:
            for op, fields in _translate(*_args(line), names, labels, pc, True):
                word = encode(op, **fields)
                words.append(word)
                listing.append((pc, word, line, number))
                pc += 1
        except (AsmError, ValueError, IndexError) as err:
            raise AsmError(f"line {number}: {line}: {err}") from err
    if ctx1_label is not None and ctx1_label not in labels:
        raise AsmError(f".ctx1: unknown label {ctx1_label}")
    return Program(words, labels, labels.get(ctx1_label, 0) if ctx1_label else 0, listing)


def _args(line):
    mnemonic, flags, named, args = _parse(line)
    return mnemonic, args, flags, named


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source")
    parser.add_argument("-o", "--output", help="hex file, one word per line")
    parser.add_argument("-D", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("-l", "--list", action="store_true", help="print a listing")
    opts = parser.parse_args()
    defines = {k: int(v, 0) for k, v in (d.split("=", 1) for d in opts.D)}
    try:
        prog = assemble(open(opts.source, encoding="utf-8").read(), defines)
    except AsmError as err:
        sys.exit(f"{opts.source}: {err}")
    if opts.list:
        for pc, word, line, _ in prog.listing:
            print(f"{pc:3d}  {word:04x}  {line}")
    if opts.output:
        with open(opts.output, "w", encoding="utf-8") as out:
            out.writelines(f"{w:04x}\n" for w in prog.words)
    print(f"{opts.source}: {len(prog.words)} words", file=sys.stderr)


if __name__ == "__main__":
    main()
