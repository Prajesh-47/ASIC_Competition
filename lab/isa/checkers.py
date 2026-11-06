# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Protocol decoders that judge a pin trace. They read only pin levels, never engine state."""


def level(sample, idle=1):
    return idle if sample is None else sample


def edges(trace, line, idle=1):
    """(cycle, new level) for every change on a line."""
    out, cur = [], None
    for t, snap in enumerate(trace):
        v = level(snap[line], idle)
        if v != cur:
            out.append((t, v))
            cur = v
    return out


def uart_decode(trace, line, bit, start=0, bits=8):
    """Sample mid-bit like a real receiver. Returns [(start cycle, byte, stop ok)]."""
    frames, t, n = [], start, len(trace)
    while t < n:
        if level(trace[t][line]) == 1:
            t += 1
            continue
        if t + bit // 2 >= n or level(trace[t + bit // 2][line]) != 0:
            t += 1
            continue
        val = 0
        end = t + bit // 2 + (bits + 1) * bit
        if end >= n:
            break
        for k in range(bits):
            val |= level(trace[t + bit // 2 + (k + 1) * bit][line]) << k
        frames.append((t, val, level(trace[end][line]) == 1))
        t = end
    return frames


def uart_bit_widths(trace, line, frame_start, bit, bits=8):
    """Edge positions within one frame, relative to its start, to check exact bit timing."""
    return [(t - frame_start, v) for t, v in edges(trace, line)
            if frame_start <= t < frame_start + (bits + 1) * bit]


def uart_exact(trace, line, bit, expect):
    """True when frames carry `expect` and every edge lands on a whole bit boundary."""
    frames = uart_decode(trace, line, bit)
    if [f[1] for f in frames][: len(expect)] != list(expect) or not all(f[2] for f in frames[: len(expect)]):
        return False
    return all(off % bit == 0 for f in frames[: len(expect)] for off, _ in uart_bit_widths(trace, line, f[0], bit))


def uart_wave(byte, bit, start, bits=8):
    """Level function for a stimulus frame."""
    levels = [0] + [(byte >> k) & 1 for k in range(bits)] + [1]

    def at(t):
        idx = (t - start) // bit
        return levels[idx] if 0 <= idx < len(levels) else 1
    return at


def spi_decode(trace, sck, mosi, cs):
    """Mode 0: MOSI is sampled on rising SCK while CS is low. Returns (bytes, rise cycles)."""
    out, rises, cur, n, prev = [], [], 0, 0, None
    for t, snap in enumerate(trace):
        c, s = level(snap[cs]), level(snap[sck], 0)
        if c == 0 and prev == 0 and s == 1:
            rises.append(t)
            cur = (cur << 1) | level(snap[mosi])
            n += 1
            if n == 8:
                out.append(cur)
                cur, n = 0, 0
        prev = s
    return out, rises


def i2c_decode(levels_scl, levels_sda):
    """Decode resolved bus levels. Returns (events, bytes with ACK bit), per the I2C rules."""
    events, bits, out = [], [], []
    for t in range(1, len(levels_scl)):
        scl, sda, psda, pscl = levels_scl[t], levels_sda[t], levels_sda[t - 1], levels_scl[t - 1]
        if scl and pscl and psda and not sda:
            events.append("S")
            bits = []
        elif scl and pscl and not psda and sda:
            events.append("P")
        elif scl and not pscl:
            bits.append(sda)
            if len(bits) == 9:
                val = 0
                for b in bits[:8]:
                    val = (val << 1) | b
                out.append((val, bits[8]))
                bits = []
    return events, out
