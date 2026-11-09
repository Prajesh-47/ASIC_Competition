# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Match reflex vs the current ISA on the golden model, measured the same way. See lab/explore/match-reflex.md.

Run from the repo root:  python3 lab/explore/match_reflex.py
Scenario 1, inline UART case flip: every byte whose bits 0-4 equal RULE has bit 5 inverted in flight.
One context does one direction (line 0 -> 4). Two contexts at half rate do both directions at once (line 0 -> 4
and line 1 -> 5), each with its own rule. Each program gets its best TPHASE preset P0, found by a sweep at the
nominal rate, then keeps it for the clock-error runs.

Pass rule for a run, the same for both programs: 16 back-to-back frames (1 stop bit), half of them matching.
"exact": the output equals the input with the chosen bit cells inverted, delayed by the fabric's pass latency,
on every cycle. "decoded": a UART receiver at the true rate, sampling mid-bit, reads the expected bytes with
valid stop bits. Clock error runs only need "decoded".
"""
import os
import random
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "model"))

from splice.asm import AsmError, assemble  # noqa: E402
from splice.model import Splice  # noqa: E402

FW = os.path.join(os.path.dirname(__file__), "..", "..", "model", "firmware", "explore")
PROGRAMS = {"base": "uart_case_base.s", "reflex": "uart_case_reflex.s"}
RULES = (0x01, 0x0E)   # direction 0: 'a' <-> 'A'; direction 1: 'n' <-> 'N'


def source(name):
    with open(os.path.join(FW, PROGRAMS[name]), encoding="utf-8") as f:
        return f.read()


def two_direction(text):
    """Context 0 serves line 0 -> 4, context 1 line 1 -> 5: the same program twice, with the second copy's
    labels and per-direction names renamed. (The assembler keeps one value per .equ name, so a name can't
    simply be redefined halfway through.)"""
    labels = re.findall(r"^(\w+):", text, re.M)
    second = text
    for name in labels + ["RX", "TX", "RULE"]:
        second = re.sub(rf"\b{name}\b", f"{name}_c1", second)
    second = second.replace(".equ RX_c1, 0", ".equ RX_c1, 1").replace(".equ TX_c1, 4", ".equ TX_c1, 5")
    second = second.replace(".equ RULE_c1, 1", f".equ RULE_c1, {RULES[1]}")
    first = text.replace(".equ RULE, 1", f".equ RULE, {RULES[0]}")
    first += "\n        halt\n"   # never reached: each copy loops forever
    second = "start_c1:\n" + second
    return first + second + "\n.ctx1 start_c1\n"


def build(name, bit, two, p0):
    text = source(name)
    if two:
        text = two_direction(text)
    return assemble(text, {"BIT": bit, "SLOT": 2 if two else 1, "P0": p0})


def frames(seed, rule, count=16):
    rnd = random.Random(seed)
    out = []
    for i in range(count):
        b = rnd.randrange(256)
        if i % 2 == 0:
            b = (b & 0xE0) | rule
        out.append(b)
    return out


def wave(data, bit, lead):
    """Levels per cycle, and the start cycle (fractional) of each frame. Frames are back to back."""
    cells = []
    for b in data:
        cells += [0] + [(b >> k) & 1 for k in range(8)] + [1]
    total = int(lead + len(cells) * bit + 4 * bit)
    w = []
    for t in range(total):
        k = (t - lead) / bit
        w.append(1 if k < 0 or k >= len(cells) else cells[int(k)])
    return w, [lead + 10 * i * bit for i in range(len(data))]


def expected(data, rule):
    return [b ^ 0x20 if (b & 0x1F) == rule else b for b in data]


def ideal(data, rule, bit, lead):
    return wave(expected(data, rule), bit, lead)[0]


def run(prog, two, inputs, cycles):
    m = Splice()
    m.two = two
    m.ctx1_pc = prog.ctx1
    m.load(prog.words)
    m.start()
    outs = {4: [], 5: []}
    for t in range(cycles):
        uio = 0xFC | inputs[0][t] | (inputs[1][t] << 1)
        p = m.step(uio=uio)
        for line in outs:
            outs[line].append((p["out"] >> line) & 1 if (p["oe"] >> line) & 1 else 1)
    return outs


def decode(level, bit, count):
    """A UART receiver at the true rate: find each start edge, sample mid-bit, check the stop bit."""
    out, t = [], 0
    while len(out) < count and t < len(level) - 1:
        if level[t] == 1 and level[t + 1] == 0:
            start = t + 1
            samples = [level[min(int(start + (k + 0.5) * bit), len(level) - 1)] for k in range(10)]
            if samples[0] != 0 or samples[9] != 1:
                return out, False
            out.append(sum(s << k for k, s in enumerate(samples[1:9])))
            t = int(start + 9.5 * bit)
        else:
            t += 1
    return out, len(out) == count


LATENCY = 2  # cycles from an input pin to a PASS output in the model (measured in test_pass_latency)


def trial(name, bit, two, p0, err=0.0, seed=1):
    """One run. Returns (exact, decoded) for every direction in use, as booleans."""
    try:
        prog = build(name, bit, two, p0)
    except AsmError:
        return False, False   # a delay went negative: too fast for this program
    true_bit = bit * (1 + err)
    lead = int(3 * true_bit) + 7
    dirs = [0, 1] if two else [0]
    data = {d: frames(seed + d, RULES[d]) for d in dirs}
    inputs = {d: wave(data[d], true_bit, lead + 3 * d)[0] for d in dirs}
    cycles = max(len(v) for v in inputs.values())
    for d in (0, 1):
        inputs.setdefault(d, [1] * cycles)
        inputs[d] = inputs[d] + [1] * (cycles - len(inputs[d]))
    outs = run(prog, two, inputs, cycles)
    exact = decoded = True
    for d in dirs:
        got = outs[4 + d]
        want = ideal(data[d], RULES[d], true_bit, lead + 3 * d)
        want = ([1] * LATENCY + want + [1] * cycles)[:cycles]
        exact &= got == want[:len(got)]
        bytes_, ok = decode(got, true_bit, len(data[d]))
        decoded &= ok and bytes_ == expected(data[d], RULES[d])
    return exact, decoded


def best_p0(name, bit, two):
    """The TPHASE preset that passes exactly at the nominal rate, nearest the middle of the working range."""
    good = [p for p in range(3 * bit // 2 - 12, 3 * bit // 2 + 4) if trial(name, bit, two, p)[0]]
    return good[len(good) // 2] if good else None


def exact_rates(name, two, bits=range(3, 41)):
    """Every cycles-per-bit at which some preset makes the output cycle-exact."""
    return [b for b in bits if best_p0(name, b, two) is not None]


def decode_p0(name, bit, two):
    """The preset that decodes at the nominal rate, nearest the middle of the working range."""
    good = [p for p in range(3 * bit // 2 - 12, 3 * bit // 2 + 4) if trial(name, bit, two, p)[1]]
    return good[len(good) // 2] if good else None


def decoded_fastest(name, two, err=0.0, top=40):
    """Smallest cycles-per-bit (down to 4) that decodes at this clock error, as does every slower rate up to
    `top`, using the preset picked at the nominal rate. A flip a cycle off still decodes."""
    best = None
    for bit in range(top, 3, -1):
        p0 = decode_p0(name, bit, two)
        if p0 is None or not trial(name, bit, two, p0, err)[1]:
            break
        best = bit
    return best


# Scenario 2: sync-word hunt. A raw NRZ stream: 16 preamble bits (0101...), then random bits with SYNC
# inserted at 6 random offsets, at least 24 bits apart and nowhere else. The bit after each sync word must come
# out inverted, cycle-exact. Each program gets its best (P0, ADJ) from a sweep at the rate under test.
SYNC = 0x2DD4
SYNC_PROGRAMS = {"base": "sync_base.s", "reflex": "sync_reflex.s"}


def sync_stream(seed, n=200, words=6):
    rnd = random.Random(seed)
    word = [(SYNC >> i) & 1 for i in range(16)]
    while True:
        bits, flips, pos = [k & 1 for k in range(16)], [], 16
        for _ in range(words):
            gap = rnd.randrange(8, 24)
            bits += [rnd.randrange(2) for _ in range(gap)] + word
            flips.append(len(bits))
        bits += [rnd.randrange(2) for _ in range(n - len(bits))] if len(bits) < n else []
        bits += [rnd.randrange(2) for _ in range(4)]
        hits = [i + 16 for i in range(len(bits) - 15) if bits[i:i + 16] == word]
        if hits == flips:
            return bits, flips
        seed += 1000
        rnd = random.Random(seed)


def cells_wave(bits, bit, lead):
    total = int(lead + len(bits) * bit + 2 * bit)
    return [bits[int((t - lead) / bit)] if 0 <= (t - lead) / bit < len(bits) else 1 for t in range(total)]


def sync_build(name, bit, two, p0, adj):
    with open(os.path.join(FW, SYNC_PROGRAMS[name]), encoding="utf-8") as f:
        text = f.read()
    if two:
        text = two_direction(text)
    return assemble(text, {"BIT": bit, "SLOT": 2 if two else 1, "P0": p0, "ADJ": adj})


def sync_trial(name, bit, two, p0, adj, seed=1):
    try:
        prog = sync_build(name, bit, two, p0, adj)
    except (AsmError, ValueError):
        return False
    lead = 3 * bit + 5
    dirs = [0, 1] if two else [0]
    streams = {d: sync_stream(seed + d) for d in dirs}
    inputs = {d: cells_wave(streams[d][0], bit, lead + 3 * d) for d in dirs}
    cycles = max(len(v) for v in inputs.values())
    for d in (0, 1):
        inputs[d] = inputs.get(d, []) + [1] * (cycles - len(inputs.get(d, [])))
    outs = run(prog, two, inputs, cycles)
    for d in dirs:
        bits, flips = streams[d]
        want_bits = [b ^ (1 if i in flips else 0) for i, b in enumerate(bits)]
        want = [1] * LATENCY + cells_wave(want_bits, bit, lead + 3 * d)
        want += [1] * cycles
        if outs[4 + d] != want[:cycles]:
            return False
    return True


def sync_best(name, bit, two):
    adjs = range(-3, 4) if name == "base" else (0,)
    for adj in adjs:
        for p0 in range(max(1, bit // 2 - 8), bit // 2 + 6):
            if sync_trial(name, bit, two, p0, adj):
                return p0, adj
    return None


def sync_fastest(name, two, top=40):
    best = None
    for bit in range(top, 1, -1):
        found = sync_best(name, bit, two)
        if found is None:
            break
        best = (bit, found)
    return best


def sync_midbit_fastest(name, two, top=40):
    """Sync hunt with the sample held mid-bit: the baseline gets only the reflex's preset, +-1."""
    best = None
    for bit in range(top, 1, -1):
        ref = sync_best("reflex", bit, two)
        if ref is None:
            break
        adjs = range(-3, 4) if name == "base" else (0,)
        if not any(sync_trial(name, bit, two, p, a) for p in range(max(1, ref[0] - 1), ref[0] + 2) for a in adjs):
            break
        best = bit
    return best


def main():
    print("Scenario 1: inline UART case flip (cycles per bit; 50 MHz / that = baud)")
    for two in (False, True):
        for name in PROGRAMS:
            words = len(build(name, 434, two, 648).words)
            print(f"  {'2 ctx' if two else '1 ctx'} {name:6s} words={words:3d} exact at: {exact_rates(name, two)}")
            print("      decoded, fastest at -2% / 0 / +2%:",
                  " / ".join(str(decoded_fastest(name, two, e)) for e in (-0.02, 0.0, 0.02)), flush=True)
    print("Scenario 2: sync-word hunt, invert the bit after each 16-bit sync word (cycles per bit, exact)")
    for two in (False, True):
        for name in SYNC_PROGRAMS:
            words = len(sync_build(name, 100, two, 47, 0).words)
            rates = [b for b in range(2, 41) if sync_best(name, b, two) is not None]
            print(f"  {'2 ctx' if two else '1 ctx'} {name:6s} words={words:3d} exact at: {rates}")
            print(f"      fastest with the sample held mid-bit: {sync_midbit_fastest(name, two)}", flush=True)


if __name__ == "__main__":
    main()
