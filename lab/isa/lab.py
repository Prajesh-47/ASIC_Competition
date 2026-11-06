# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Run every workload on every ISA candidate, and judge the pin traces with independent decoders."""
import csv
import sys

import checkers as ck
import isa_enc as enc
import firmware as fw
from sim import Chip, Core, Infeasible, run

BYTES = [0x55, 0xA3, 0x00, 0xFF, 0x7E]
ALL = ["V0", "V1", "V2", "V3", "V4"]


def validate(v, prog):
    for pc, ins in enumerate(prog):
        enc.fits(v, ins)
        if ins.op in ("OUTB", "INB") and not 0 <= pc - ins.a["to"] < 16:
            raise ValueError(f"{v}: {ins.op} can only branch back up to 15 words")
    return sum(1 for i in prog if i.op != "WRAP")


def tx_case(v, bit):
    div = 1
    use = bit
    if v == "V1":
        div = max(1, round(bit / 8))
        use = 8 * div
    prog = fw.uart_tx(v, use)
    words = validate(v, prog)
    chip = Chip()
    core = chip.add(Core(prog, chip, div=div))
    core.fifo_tx = list(BYTES)
    trace = run(chip, (len(BYTES) + 2) * 11 * use + 200)
    return words, ck.uart_exact(trace, 4, use, BYTES), use


def rx_case(v, bit):
    div = 1
    use = bit
    if v == "V1":
        div = max(1, round(bit / 8))
        use = 8 * div
    prog = fw.uart_rx(v, use)
    words = validate(v, prog)
    chip = Chip()
    core = chip.add(Core(prog, chip, div=div))
    gap = 3 * use
    first = 50 + 2 * use  # after the engine's first step, even with a large divider
    waves = [ck.uart_wave(b, use, first + n * (10 * use + gap)) for n, b in enumerate(BYTES)]

    def stim(t):
        val = 1
        for w in waves:
            val &= w(t)
        return {0: val}
    run(chip, first + 50 + (len(BYTES) + 1) * (10 * use + gap), stim)
    return words, [x >> 8 for x in core.fifo_rx] == BYTES, use


def spi_case(v, half):
    div = 1
    logical = half
    if v == "V1" and half > 8:  # the delay field is short, so PIO style relies on the divider
        div = half // 4
        logical = 4
        half = div * logical
    prog = fw.spi_master(v, logical)
    words = validate(v, prog)
    chip = Chip()
    core = chip.add(Core(prog, chip, div=div))
    core.fifo_tx = [b << 8 for b in BYTES]
    miso_bytes = [0xC3, 0x5A, 0x81, 0x18, 0xE7]
    state = {"bit": 0, "psck": 0, "val": 0}

    def bus(t, out, lv):
        sck = 0 if out[4] is None else out[4]
        if state["psck"] == 1 and sck == 0:
            state["bit"] += 1
        state["psck"] = sck
        idx = state["bit"]
        byte = idx // 8
        state["val"] = (miso_bytes[byte] >> (7 - idx % 8)) & 1 if byte < len(miso_bytes) else 1
        lv[7] = state["val"]
        return lv
    trace = run(chip, 300 + (len(BYTES) + 1) * 16 * max(half, 1) + 400, None, bus)
    got, _ = ck.spi_decode(trace, 4, 5, 6)
    ok = got[: len(BYTES)] == BYTES and [x & 0xFF for x in core.fifo_rx] == miso_bytes
    return words, ok, half


def i2c_case(v, q):
    prog = fw.i2c_master(v, q)
    words = validate(v, prog)
    chip = Chip()
    chip.odrain = {4, 5}
    core = chip.add(Core(prog, chip))
    sent = [0xA2, 0x5C]
    core.fifo_tx = [b << 8 for b in sent]
    scl_log, sda_log = [], []
    st = {"pscl": 1, "n": 0, "ack": 0, "psda": 1, "active": False}

    def bus(t, out, lv):
        scl = 0 if out[4] == 0 else 1
        sda_m = 0 if out[5] == 0 else 1
        # Independent target model: ACKs the 9th clock of each byte
        if st["pscl"] == 1 and scl == 1 and st["psda"] == 1 and sda_m == 0:
            st["active"] = True
            st["n"] = 0
        if st["active"] and st["pscl"] == 1 and scl == 0:
            st["n"] += 1
            st["ack"] = st["n"] % 9 == 0  # the first falling edge is the START's own
        sda = sda_m & (0 if st["ack"] else 1)
        st["pscl"], st["psda"] = scl, sda_m
        scl_log.append(scl)
        sda_log.append(sda)
        lv[4], lv[5] = scl, sda
        return lv
    run(chip, 200 + 2 * 9 * 4 * q + 40 * q, None, bus)
    ev, data = ck.i2c_decode(scl_log, sda_log)
    return words, ev == ["S", "P"] and data == [(0xA2, 0), (0x5C, 0)], q


def flip_case(v, bit, k=3):
    prog = fw.uart_flip(v, bit, k)
    words = validate(v, prog)
    chip = Chip()
    chip.add(Core(prog, chip))
    gap = 3 * bit
    waves = [ck.uart_wave(b, bit, 50 + n * (10 * bit + gap)) for n, b in enumerate(BYTES)]

    def stim(t):
        val = 1
        for w in waves:
            val &= w(t)
        return {0: val}
    trace = run(chip, 100 + (len(BYTES) + 1) * (10 * bit + gap), stim)
    expect = [b ^ (1 << k) for b in BYTES]
    return words, ck.uart_exact(trace, 4, bit, expect), bit


def rewrite_case(v, bit, header=0xA5, new=0x3C):
    prog = fw.uart_rewrite(v, bit, header, new)
    words = validate(v, prog)
    chip = Chip()
    chip.add(Core(prog, chip))
    frames = [0x11, header, 0x77, 0x22, header, 0x99, 0x33]
    expect = [0x11, header, new, 0x22, header, new, 0x33]
    span = 10 * bit
    waves = [ck.uart_wave(b, bit, 50 + n * span) for n, b in enumerate(frames)]

    def stim(t):
        val = 1
        for w in waves:
            val &= w(t)
        return {0: val}
    trace = run(chip, 200 + (len(frames) + 1) * span, stim)
    ok = ck.uart_exact(trace, 4, bit, expect)
    first_in, first_out = 50, ck.uart_decode(trace, 4, bit)[0][0] if ck.uart_decode(trace, 4, bit) else -1
    return words, ok, first_out - first_in + 1


def react_case(v):
    prog = fw.react(v)
    validate(v, prog)
    chip = Chip()
    chip.add(Core(prog, chip))
    edge = 40
    trace = run(chip, 80, lambda t: {0: 0 if t >= edge else 1})
    for t, snap in enumerate(trace):
        if t > 5 and snap[5] == 0:
            return t - edge + 1
    return None


def dual_case(v, bit, k=3):
    bytes_a, bytes_b = [0x55, 0xA3, 0x7E], [0x0F, 0xC1, 0x3C]
    span = 10 * bit
    a_waves = [ck.uart_wave(b, bit, 60 + n * (span + 7 * bit)) for n, b in enumerate(bytes_a)]
    b_waves = [ck.uart_wave(b, bit, 60 + (37 * bit) // 10 + n * (span + 11 * bit)) for n, b in enumerate(bytes_b)]

    def stim(t):
        va, vb = 1, 1
        for w in a_waves:
            va &= w(t)
        for w in b_waves:
            vb &= w(t)
        return {0: va, 1: vb}
    tries = [None] if v in ("V4", "V5") else list(range(11, 80, 3))
    best = None
    for loop in tries:
        try:
            progs = fw.dual_flip(v, bit, k) if loop is None else fw.dual_flip(v, bit, k, loop)
        except Infeasible:
            continue
        words = sum(validate("V4" if v in ("V4", "V5") else v, p) for p in progs)
        chip = Chip()
        chip.barrel = v == "V5"
        for p in progs:
            chip.add(Core(p, chip))
        trace = run(chip, 200 + 3 * (span + 12 * bit) + 4 * span, stim)
        got_a = [f[1] for f in ck.uart_decode(trace, 4, bit)][:3]
        got_b = [f[1] for f in ck.uart_decode(trace, 5, bit)][:3]
        if got_a == [b ^ (1 << k) for b in bytes_a] and got_b == [b ^ (1 << k) for b in bytes_b]:
            exact = ck.uart_exact(trace, 4, bit, [b ^ (1 << k) for b in bytes_a]) and ck.uart_exact(trace, 5, bit, [b ^ (1 << k) for b in bytes_b])
            best = (words, True, exact)
            if exact:
                break
    return best or (0, False, False)


def fuzz_case(v, bit):
    prog = fw.fuzz_flip(v, bit)
    words = validate(v, prog)
    chip = Chip()
    chip.add(Core(prog, chip))
    gap = 4 * bit
    frames = [0x55, 0xA3, 0x00, 0xFF, 0x7E, 0x18, 0xC3, 0x99]
    waves = [ck.uart_wave(b, bit, 300 + n * (10 * bit + gap)) for n, b in enumerate(frames)]

    def stim(t):
        val = 1
        for w in waves:
            val &= w(t)
        return {0: val}
    trace = run(chip, 400 + (len(frames) + 1) * (10 * bit + gap), stim)
    lfsr, expect = 0xACE1, []
    for b in frames:  # independent reference LFSR
        low = lfsr & 1
        lfsr >>= 1
        if low:
            lfsr ^= 0xB400
        expect.append(b ^ (1 << (lfsr & 7)))
    return words, ck.uart_exact(trace, 4, bit, expect), bit


def spi_inline_case(v, half, skip=2, new=0x3C, fast=False):
    """Interposer between a master (external) and a slave (external), replacing one MISO byte."""
    prog = fw.spi_inline(v, skip, new)
    words = validate("V3" if v.endswith("T") else v, prog)
    chip = Chip()
    chip.fast_pass = fast
    chip.add(Core(prog, chip))
    slave = [0x11, 0x22, 0x33, 0x44, 0x55]
    st = {"bit": 0, "ps": 0, "miso": (slave[0] >> 7) & 1, "got": [], "cur": 0, "n": 0, "pm": 0}
    total = (len(slave) * 8) * 2 * half
    start = 60

    def bus(t, out, lv):
        m_sck = 0
        if t < 20:  # let the startup levels settle before the slave model listens
            lv[0], lv[1], lv[2] = 0, 1, st["miso"]
            st["ps"] = 0
            return lv
        if start <= t < start + total:
            m_sck = 1 if ((t - start) % (2 * half)) < half else 0
        lv[0] = m_sck
        lv[1] = 0 if start - 20 <= t < start + total + 40 else 1
        # Master samples what the interposer sends back on its own rising edge
        if m_sck and not st["pm"]:
            bit = out[6] if out[6] is not None else 1
            st["cur"] = (st["cur"] << 1) | bit
            st["n"] += 1
            if st["n"] == 8:
                st["got"].append(st["cur"])
                st["cur"], st["n"] = 0, 0
        st["pm"] = m_sck
        # The slave sees the delayed SCK, and shifts MISO on its falling edge
        s_sck = out[4] if out[4] is not None else 0
        if t > start and st["ps"] == 1 and s_sck == 0:
            st["bit"] += 1
            idx = st["bit"]
            byte = idx // 8
            st["miso"] = (slave[byte] >> (7 - idx % 8)) & 1 if byte < len(slave) else 1
        st["ps"] = s_sck
        lv[2] = st["miso"]
        return lv
    run(chip, start + total + 200, None, bus)
    expect = list(slave)
    expect[skip] = new
    return words, st["got"] == expect, half


def match_flip_case(v, bit, header=0xA5, k=3):
    """Both directions: after `header`, flip bit k of the next frame. V5 in software, V7 in hardware."""
    progs = fw.match_flip(v, bit, header, k)
    table = "V3" if v == "V5" else "V7"
    words = sum(validate(table, p) for p in progs)
    chip = Chip()
    chip.barrel = v == "V5"
    for p in progs:
        chip.add(Core(p, chip))
    fa = [0x11, header, 0x77, 0x22, header, 0x99]
    fb = [header, 0x10, 0x66, header, 0x20, 0x31]
    span = 10 * bit
    wa = [ck.uart_wave(b, bit, 80 + n * (span + 3 * bit)) for n, b in enumerate(fa)]
    wb = [ck.uart_wave(b, bit, 80 + 3 * bit + bit // 3 + n * (span + 4 * bit)) for n, b in enumerate(fb)]

    def stim(t):
        va, vb = 1, 1
        for w in wa:
            va &= w(t)
        for w in wb:
            vb &= w(t)
        return {0: va, 1: vb}
    trace = run(chip, 400 + 7 * (span + 5 * bit), stim)
    ea = [0x11, header, 0x77 ^ (1 << k), 0x22, header, 0x99 ^ (1 << k)]
    eb = [header, 0x10 ^ (1 << k), 0x66, header, 0x20 ^ (1 << k), 0x31]
    ok = [f[1] for f in ck.uart_decode(trace, 4, bit)] == ea and [f[1] for f in ck.uart_decode(trace, 5, bit)] == eb
    return words, ok, bit


def bidir_rewrite_case(v, bit, header=0xA5, new=0x3C):
    """Rewrite in both directions at once: this needs two threads of control."""
    if v == "V5":
        progs = [fw.uart_rewrite("V5", bit, header, new, RX=0, TX=4), fw.uart_rewrite("V5", bit, header, new, RX=1, TX=5)]
    else:
        progs = [fw.uart_rewrite("V3", bit, header, new, RX=0, TX=4), fw.uart_rewrite("V3", bit, header, new, RX=1, TX=5)]
    words = sum(validate("V3", p) for p in progs)
    chip = Chip()
    chip.barrel = v == "V5"
    for p in progs:
        chip.add(Core(p, chip))
    fa = [0x11, header, 0x77, 0x22, header, 0x99]
    fb = [header, 0x10, 0x66, header, 0x20, 0x31]
    span = 10 * bit
    wa = [ck.uart_wave(b, bit, 50 + n * (span + bit)) for n, b in enumerate(fa)]
    wb = [ck.uart_wave(b, bit, 50 + 3 * bit + bit // 3 + n * (span + 2 * bit)) for n, b in enumerate(fb)]

    def stim(t):
        va, vb = 1, 1
        for w in wa:
            va &= w(t)
        for w in wb:
            vb &= w(t)
        return {0: va, 1: vb}
    trace = run(chip, 300 + 7 * (span + 3 * bit), stim)
    ea, eb = [0x11, header, new, 0x22, header, new], [header, new, 0x66, header, new, 0x31]
    ga = [f[1] for f in ck.uart_decode(trace, 4, bit)]
    gb = [f[1] for f in ck.uart_decode(trace, 5, bit)]
    return words, ga == ea and gb == eb, bit


def min_rate(fn, v, lo=1, hi=70):
    """Smallest period where the decoder accepts the waveform."""
    for x in range(lo, hi):
        try:
            res = fn(v, x)
        except (Infeasible, ValueError, IndexError):
            continue
        if res[1]:
            return x, res[0], res[2]
    return None, None, None


def main(path):
    rows = []
    cases = [("uart_tx", tx_case, 434), ("uart_rx", rx_case, 434), ("spi_master", spi_case, 25),
             ("i2c_master", i2c_case, 125), ("uart_flip", flip_case, 434),
             ("uart_rewrite", rewrite_case, 434), ("fuzz_flip", fuzz_case, 434)]
    for name, fn, nominal in cases:
        for v in ALL:
            try:
                words, ok, extra = fn(v, nominal)
                err = ""
            except (Infeasible, ValueError) as e:
                words, ok, extra, err = None, False, None, str(e)
            mn, mw, _ = min_rate(fn, v)
            rows.append(dict(workload=name, variant=v, nominal_period=nominal, words=words, ok=ok,
                             min_period=mn, min_words=mw, note=err or (f"period used {extra}" if name in ("uart_tx", "uart_rx") and extra != nominal else "")))
            print(name, v, "words", words, "ok", ok, "min", mn, err[:60])
    for v in ALL:
        print("react", v, react_case(v))
        rows.append(dict(workload="react", variant=v, min_period=react_case(v), note="pin edge to pin effect, cycles"))
    for v in ALL + ["V5"]:
        best = None
        for bit in list(range(4, 60, 2)) + list(range(60, 240, 10)) + list(range(240, 600, 40)):
            words, ok, exact = dual_case(v, bit)
            if ok:
                best = (bit, words, exact)
                break
        words, ok, exact = dual_case(v, 434)
        print("dual", v, "nominal ok", ok, "exact", exact, "words", words, "min bit", best)
        rows.append(dict(workload="dual_flip", variant=v, nominal_period=434, words=words, ok=ok,
                         min_period=best[0] if best else None, min_words=best[1] if best else None,
                         note=f"exact={exact}; exact at min={best[2] if best else None}"))
    for v in ("V0", "V3T"):
        mn, mw, _ = min_rate(spi_inline_case, v, 2, 40)
        res = spi_inline_case(v, 25)
        print("spi_inline", v, "words", res[0], "ok", res[1], "min half", mn)
        rows.append(dict(workload="spi_inline", variant=v, nominal_period=25, words=res[0], ok=res[1], min_period=mn, min_words=mw, note="half SCK period, cycles"))
    for v in ("V5", "V6"):
        mn, mw, _ = min_rate(bidir_rewrite_case, v, 4, 70)
        res = bidir_rewrite_case(v, 434)
        print("bidir_rewrite", v, "words", res[0], "ok", res[1], "min bit", mn)
        rows.append(dict(workload="bidir_rewrite", variant=v, nominal_period=434, words=res[0], ok=res[1], min_period=mn, min_words=mw, note="V5=2 barrel contexts; V6=2 engines"))
    for v in ("V5", "V7"):
        mn, mw, _ = min_rate(match_flip_case, v, 4, 70)
        res = match_flip_case(v, 434)
        print("match_flip", v, "words", res[0], "ok", res[1], "min bit", mn)
        rows.append(dict(workload="match_flip", variant=v, nominal_period=434, words=res[0], ok=res[1], min_period=mn, min_words=mw, note="V5=barrel software; V7=hardware match rule"))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["workload", "variant", "nominal_period", "words", "ok", "min_period", "min_words", "note"])
        w.writeheader()
        w.writerows(rows)
    print("kraft", {k: round(enc.kraft(t), 3) for k, t in enc.VARIANTS.items()})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results.csv")
