# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""100BASE-TX trial on the golden model, with the trial's own decoders. Usage, from lab/explore:
    python3 eth.py [--tx FILE] [--rx FILE]
Finds the fastest symbol time at which each program still passes the trial's checks.
"""
import argparse

from harness import firmware, run, trial

T = trial
TX_BITS = (60, 40, 30, 26, 24, 22, 20, 18, 16, 14, 12, 10, 8, 6, 4, 3, 2)
RX_HALVES = (60, 40, 30, 26, 22, 20, 18, 16, 14, 12, 10, 8, 6, 5, 4, 3, 2, 1)


def tx_ok(program, bit, gap=0):
    nibbles = (list(range(16)) * 4)[:63]
    prog = firmware(program, BIT=bit)
    words = [nibbles[i] << 12 | nibbles[i + 1] << 8 | nibbles[i + 2] << 4 for i in range(0, len(nibbles), 3)] + [1]
    # The RTL test starts the program, waits 30 symbols, then starts the trace and feeds the FIFO over SPI,
    # which takes several symbols. The descrambler seeds itself from 11 symbols of idle, so wait 15 here.
    trace_at = 30 * bit
    feed_at = trace_at + 15 * bit
    pending = list(words)

    def stim(t, out, oe):
        return 0xFF

    total = trace_at + (len(nibbles) * 5 + 200) * bit
    from splice.model import Splice
    m = Splice()
    m.load(prog.words)
    m.start()
    trace, last = [], -10**9
    for t in range(total):
        if t >= feed_at and pending and len(m.h2e) < m.FIFO_DEPTH and t - last >= gap:
            m.host_push(pending.pop(0))
            last = t
        p = m.step(0xFF, 0xFF)
        if t >= trace_at:
            trace.append((p["out"], p["oe"], p["uo"]))
    try:
        bits = T.descramble(T.mlt3_decode(T.mlt3_levels(trace), bit))
        groups, before = T.eth_groups_after_jk(bits)
        wanted = [T.CODE_4B5B[n] for n in nibbles] + [T.T, T.R]
        return (groups[:len(wanted)] == wanted and all(before[20:])
                and all(g == T.IDLE for g in groups[len(wanted):-1])), len(prog.words)
    except AssertionError:
        return False, len(prog.words)


def rx_run(program, groups, half):
    prog = firmware(program)
    bits = [1] * 20 + [b for g in groups for b in T.group_bits(g)] + [1] * 20
    wave = []
    for b in bits:
        wave += [(b, 0)] * half + [(b, 1)] * half

    def stim(t, out, oe):
        data, clk = wave[t] if t < len(wave) else (1, 0)
        return 0xFC | data | clk << 1

    words, trace, _ = run(prog, len(wave) + 400, stim)
    return words, trace, len(prog.words)


def rx_ok(program, half):
    nibbles = list(range(16))
    good = [T.J, T.K] + [T.CODE_4B5B[n] for n in nibbles] + [T.T, T.R]
    words, _, n = rx_run(program, good, half)
    ok = words == nibbles + [0x0200]
    if ok and half == 60:
        w2, trace, _ = rx_run(program, [T.J, T.K, T.CODE_4B5B[3], 0x00, T.CODE_4B5B[4]], 60)
        ok = w2[:2] == [3, 0x0100] and any((t[0] >> 7) & 1 for t in trace)
    return ok, n


def fastest(fn, program, steps):
    best, words = None, None
    for s in steps:
        ok, words = fn(program, s)
        if not ok:
            break
        best = s
    return best, words


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx", default="trial/eth_tx.s")
    ap.add_argument("--rx", default="trial/eth_rx.s")
    a = ap.parse_args()
    bit, n = fastest(tx_ok, a.tx, TX_BITS)
    print(f"{a.tx}: {n} words, fastest passing symbol time {bit} cycles = {50 / bit if bit else 0:.2f} MBd")
    half, n = fastest(rx_ok, a.rx, RX_HALVES)
    print(f"{a.rx}: {n} words, fastest passing bit time {2 * half if half else 0} cycles = "
          f"{25 / half if half else 0:.2f} MBd")
