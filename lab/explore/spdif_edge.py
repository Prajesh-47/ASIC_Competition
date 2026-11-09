# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""S/PDIF receive on the golden model: the trial firmware against the TCAP/CMPU prototype, measured the same way.

Run from the repo root:  python3 lab/explore/spdif_edge.py
Both programs get the same waveform (biphase-mark, written from the coding rules as in test/test_trial.py,
timing rounded to the 20 ns grid), an ideal host that empties the FIFO every cycle, and the same pass rule:
after the receiver locks, at least 30 consecutive subframes decode exactly, with ERR low and no FIFO overflow.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "model"))

from splice.asm import assemble  # noqa: E402
from splice.model import Splice  # noqa: E402

FW = os.path.join(os.path.dirname(__file__), "..", "..", "model", "firmware")
PRE = {"M": [1, 1, 1, 0, 0, 0, 1, 0], "W": [1, 1, 1, 0, 0, 1, 0, 0], "B": [1, 1, 1, 0, 1, 0, 0, 0]}


def payload(n):
    sample = (n * 0x1B5A7 + 0x35) & 0xFFFFFF
    bits = [(sample >> k) & 1 for k in range(24)] + [int(n % 3 == 0), int(n % 5 == 0), int(n % 7 == 0)]
    return bits + [sum(bits) & 1]


def stream(count, start=0):
    return [("B" if i % 384 == 0 else ("W" if i % 2 else "M"), payload(i)) for i in range(start, start + count)]


def states(subframes, invert=False):
    out, level = [], 0
    for name, bits in subframes:
        out += PRE[name] if level == 0 else [1 - s for s in PRE[name]]
        level = out[-1]
        for b in bits:
            level = 1 - level
            out.append(level)
            if b:
                level = 1 - level
            out.append(level)
    return [1 - s for s in out] if invert else out


def wave(st, fs=48000, rate=1.0, lead=120):
    per_state = 50_000_000 / (fs * 128) / rate
    total = round(len(st) * per_state)
    w, index = [st[0] ^ 1] * lead, 0
    for cycle in range(total):
        while (index + 1) * per_state <= cycle and index + 1 < len(st):
            index += 1
        w.append(st[index])
    return w


def run(program, w):
    m = Splice()
    m.load(program.words)
    m.start()
    words, pins, overflow = [], [], 0
    for level in w + [w[-1]] * 200:
        p = m.step(uio=0xFE | level)
        pins.append(p["out"])
        if len(m.e2h) >= Splice.FIFO_DEPTH:
            overflow += 1
        while m.e2h:
            words.append((len(pins), m.e2h.pop(0)))
    return words, pins, overflow


def decode_trial(words):
    out = []
    for i in range(0, len(words) - 1, 2):
        first, second = words[i][1], words[i + 1][1]
        st = [(first >> k) & 1 for k in range(15, 11, -1)]
        samples = [(first >> k) & 1 for k in range(11, -1, -1)] + [(second >> k) & 1 for k in range(15, -1, -1)]
        code = st[0] << 3 | st[1] << 2 | st[2] << 1 | st[3]
        name = {0b0010: "M", 0b1101: "M", 0b0100: "W", 0b1011: "W", 0b1000: "B", 0b0111: "B"}.get(code, "?")
        prev, bits = st[3], []
        for s in samples:
            bits.append(int(s == prev))
            prev = s
        out.append((name, bits))
    return out


def decode_tcap(words):
    out = []
    for i in range(0, len(words) - 1, 2):
        w1, w2 = words[i][1], words[i + 1][1]
        name = {1: "M", 2: "W", 3: "B"}.get(w1 & 7, "?")
        levels = [(w1 >> k) & 1 for k in range(3, 16)] + [(w2 >> k) & 1 for k in range(16)]
        out.append((name, [int(levels[k] == levels[k + 1]) for k in range(28)]))
    return out


def judge(decoded, sent, need=30):
    """Subframes decoded right after lock, or 0 if it never lines up or any is wrong."""
    for skip in range(min(12, len(sent))):
        if decoded and decoded[0] == sent[skip]:
            n = min(len(decoded), len(sent) - skip)
            if decoded[:n] == sent[skip:skip + n] and n >= need:
                return n
            return 0
    return 0


PROGRAMS = {
    "trial": ("trial/spdif_rx.s", decode_trial),
    "tcap": ("explore/spdif_rx_tcap.s", decode_tcap),
}


def trial_one(name, fs=48000, rate=1.0, count=44, invert=False, sent=None):
    path, dec = PROGRAMS[name]
    prog = assemble(open(os.path.join(FW, path), encoding="utf-8").read())
    sent = sent or stream(count)
    words, pins, overflow = run(prog, wave(states(sent, invert), fs, rate))
    n = judge(dec(words), sent)
    # ERR (line 5) must stay low from the first word pushed after lock to the end of the stream
    first = words[2][0] if len(words) > 2 else len(pins)
    err = any((p >> 5) & 1 for p in pins[first:len(pins) - 400])
    return n > 0 and not overflow and not err, n, overflow, err, len(prog.words)


def main():
    rates = [32000, 44100, 48000, 64000, 88200, 96000]
    offsets = [-0.10, -0.05, -0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03, 0.05, 0.10]
    for name in PROGRAMS:
        print(f"\n== {name}: {trial_one(name)[4]} words")
        for fs in rates:
            row = []
            for off in offsets:
                ok = trial_one(name, fs, 1 + off)[0]
                row.append("Y" if ok else ".")
            print(f"{fs:6d} Hz  " + " ".join(row) + "   offsets " + " ".join(f"{o:+.0%}" for o in offsets))
        print("inverted 48k:", trial_one(name, invert=True)[:2])


if __name__ == "__main__":
    main()
