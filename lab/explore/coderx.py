# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""explore/isa-code-rx: biphase-mark and Manchester receive in the line coder, measured on the golden model.

Run from the repo root:  python3 lab/explore/coderx.py [spdif-rx|spdif-tx|dali|all]
S/PDIF receive uses spdif_edge.py's waveform, ideal host and pass rule, so the software programs and the
coded one are measured the same way. Each runs alone and beside busy contexts (2 and 4 contexts).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import spdif_edge as se  # noqa: E402
from splice.asm import assemble  # noqa: E402
from splice.model import Splice  # noqa: E402

FW = se.FW
BASE = 448  # where the busy neighbours run
BUSY = assemble(f".equ B, {BASE}\n add r0, 1\n jmp B\n").words  # a neighbour that takes every slot it gets


def load(path, **defines):
    return assemble(open(os.path.join(FW, path), encoding="utf-8").read(), defines)


def model(prog, nctx=1):
    m = Splice()
    m.load(prog.words)
    if nctx > 1:
        m.load(BUSY, BASE)
        m.nctx = nctx
        m.ctx_pc = [BASE] * 3
    m.start()
    return m


def run_rx(prog, w, nctx=1):
    m = model(prog, nctx)
    words, pins, overflow = [], [], 0
    for level in w + [w[-1]] * 200:
        p = m.step(uio=0xFE | level)
        pins.append(p["out"])
        if len(m.e2h) >= Splice.FIFO_DEPTH:
            overflow += 1
        while m.e2h:
            words.append((len(pins), m.e2h.pop(0)))
    return words, pins, overflow, m


NAMES = {0b0010: "M", 0b1101: "M", 0b0100: "W", 0b1011: "W", 0b1000: "B", 0b0111: "B"}


def decode_code(words):
    out = []
    for i in range(0, len(words) - 1, 2):
        w1, w2 = words[i][1], words[i + 1][1]
        name = NAMES.get(w1 >> 12, "?")
        bits = [(w1 >> k) & 1 for k in range(11, -1, -1)] + [(w2 >> k) & 1 for k in range(15, -1, -1)]
        out.append((name, bits))
    return out


RX = {
    "trial": ("trial/spdif_rx.s", se.decode_trial),
    "tcap": ("explore/spdif_rx_tcap.s", se.decode_tcap),
    "code": ("spdif_rx_code.s", decode_code),
}


def rx_one(name, fs=48000, rate=1.0, count=44, invert=False, nctx=1, sent=None):
    path, dec = RX[name]
    prog = load(path)
    sent = sent or se.stream(count)
    words, pins, overflow, _ = run_rx(prog, se.wave(se.states(sent, invert), fs, rate), nctx)
    n = se.judge(dec(words), sent)
    first = words[2][0] if len(words) > 2 else len(pins)
    err = any((p >> 5) & 1 for p in pins[first + 1100:len(pins) - 400])  # pins lag a subframe in "code"
    perr = any((p >> 6) & 1 for p in pins[first + 1100:len(pins) - 400])
    return n > 0 and not overflow and not err and not perr, n, overflow, err, len(prog.words)


def spdif_rx():
    rates = [32000, 44100, 48000, 64000, 88200, 96000]
    offsets = [-0.10, -0.05, -0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03, 0.05, 0.10]
    for name in RX:
        for nctx in (1, 2, 4):
            print(f"\n== {name}: {rx_one(name)[4]} words, {nctx} context(s)")
            for fs in rates:
                row = ["Y" if rx_one(name, fs, 1 + off, nctx=nctx)[0] else "." for off in offsets]
                print(f"{fs:6d} Hz  " + " ".join(row) + "   offsets " + " ".join(f"{o:+.0%}" for o in offsets))
            if nctx == 1:
                print("inverted 48k:", rx_one(name, invert=True)[:2])


def tx_one(path, parity, count=400, nctx=1):
    """Ideal host. The edge-only reader from test_trial.py decodes the line. Returns (ok, words, kHz, why)."""
    from harness import trial as T
    prog = load(path)
    feed = []
    for i in range(count):
        bits = T.spdif_payload(i)
        feed += [sum(b << k for k, b in enumerate(bits[:12])),
                 sum(b << k for k, b in enumerate(bits[12:28 if parity else 27]))]
    m = model(prog, nctx)
    levels, err = [], False
    for _ in range(round(count * T.FRAME_CYCLES / 2)):
        while feed and len(m.h2e) < Splice.FIFO_DEPTH:
            m.host_push(feed.pop(0))
        p = m.step()
        levels.append((p["out"] >> 4) & 1)
        err |= bool((p["out"] >> 5) & 1)
    try:
        states, edges, runs = T.spdif_read_line(levels)
        got = T.spdif_split(states)
    except (IndexError, ValueError):
        return False, len(prog.words), 0, "no signal"
    khz = (len(states) / 128) / ((edges[-1] - edges[0]) / 50e6) / 1000
    names = [g[0] for g in got]
    wanted = ["B" if i % 384 == 0 else ("W" if i % 2 else "M") for i in range(len(names))]
    n = min(len(got), count) - 1
    wrong = [i for i in range(n) if got[i][1] != T.spdif_payload(i)]
    why = ("ERR" if err else "order" if names != wanted else f"{len(wrong)} wrong" if wrong else
           "rate" if abs(khz - 48) / 48 >= 0.001 else "")
    return not why, len(prog.words), round(khz, 3), why


def spdif_tx():
    for path, parity in (("trial/spdif_tx.s", False), ("spdif_tx_code.s", True)):
        for nctx in (1, 2, 4):
            print(path, f"{nctx} ctx", tx_one(path, parity, nctx=nctx))


TE = 20833


def dali_phases(bits):
    out = []
    for b in [1] + bits:
        out += [0, 1] if b else [1, 0]
    return out


def bits_of(value, count):
    return [(value >> k) & 1 for k in range(count - 1, -1, -1)]


def dali_gear_case(path, frame, te, nctx=1):
    """Plays a forward frame onto the open-drain line and records the bus. Returns (FIFO words, reply levels
    sampled mid-half, gap in Te from the frame's end to the reply)."""
    prog = load(path)
    m = model(prog, nctx)
    m.odrain = 0x10
    phases = dali_phases(bits_of(frame, 16))
    t0 = 1000
    ended = t0 + len(phases) * te + 4 * te
    total = ended + 40 * TE
    level, history, words = 1, [], []
    for t in range(total):
        k = (t - t0) // te
        ext = phases[k] if 0 <= k < len(phases) else 1
        own = (m.out[4] if m.oe[4] else 1)
        bus = own & ext
        if not history or history[-1][1] != bus:
            history.append((t, bus))
        m.step(uio=0xEF | (bus << 4))
        while m.e2h:
            words.append(m.e2h.pop(0))
    falls = [t for (t, v), (_, w) in zip(history[1:], history) if t >= ended and w == 1 and v == 0]

    def sample(t):
        v = 1
        for when, lv in history:
            if when > t:
                break
            v = lv
        return v
    reply = [sample(round(falls[0] + TE * (k + 0.5))) for k in range(18)] if falls else None
    return words, reply, ((falls[0] - ended) / TE if falls else None)


def dali_gear(nctx=1):
    for path in ("dali_gear.s", "dali_gear_code.s"):
        ok = True
        for byte, addressed, te in ((5 << 1 | 1, True, TE), (0x80 | 3 << 1 | 1, True, round(TE * 1.1)),
                                    (0xFF, True, round(TE * 0.9)), (6 << 1 | 1, False, TE),
                                    (0x80 | 4 << 1 | 1, False, TE)):
            frame = byte << 8 | 0x91
            words, reply, gap = dali_gear_case(path, frame, te, nctx)
            good = words[:1] == [frame] and (0x0100 in words) == addressed
            if addressed:
                good = good and reply == dali_phases(bits_of(0xFF, 8)) and 7 <= gap <= 22
            else:
                good = good and reply is None
            ok &= good
            if not good:
                print("  fail", path, hex(frame), te, [hex(w) for w in words], gap)
        print(path, f"{len(load(path).words)} words, {nctx} ctx:", "pass" if ok else "FAIL")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("spdif-rx", "all"):
        spdif_rx()
    if what in ("spdif-tx", "all"):
        spdif_tx()
    if what in ("dali", "all"):
        for n in (1, 4):
            dali_gear(n)
