# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# Second blind adaptability trial (lab/explore/trial2.md). Four protocols drawn at random from
# lab/explore/protocol-candidates.md (seed 20261111), implemented in firmware only, with no RTL or ISA change.
# Every device model and decoder here is written from the protocol's specification, not from the firmware.
# Kept out of the default run: make COCOTB_TEST_MODULES=test_trial2

import cocotb
from cocotb.triggers import ClockCycles, Edge, First, Timer
from cocotb.utils import get_sim_time

import os

from test import HERE, assemble, capture, fifo_xfer, firmware, levels, load_program, reset, start, stop, write_reg

CYCLE_NS = 20


def pad(out, oe, line, idle=1):
    """What a line's pad shows: the chip's level when it drives it, else the idle level."""
    return (out >> line) & 1 if (oe >> line) & 1 else idle


def combined(first, second, **defines):
    """Two programs in one image: the second starts context 1. Their labels must not clash."""
    def source(name):
        with open(os.path.join(HERE, "..", "model", "firmware", name), encoding="utf-8") as f:
            return f.read()
    text = source(first) + "\n.ctx1 second_start\nsecond_start:\n" + source(second)
    return assemble(text, defines)


async def set_two(dut, prog, pull_sel=0, push_sel=0, nctx=2):
    await write_reg(dut, 0, nctx - 1)
    for reg, pc in ((4, prog.ctx[0]), (16, prog.ctx[1]), (18, prog.ctx[2])):
        await write_reg(dut, reg, pc >> 8)
        await write_reg(dut, reg + 1, pc & 0xFF)
    await write_reg(dut, 20, pull_sel)
    await write_reg(dut, 21, push_sel)


async def clear_contexts(dut):
    await stop(dut)
    for reg in (0, 1, 20, 21):
        await write_reg(dut, reg, 0)


class Host:
    """A host that only uses XFER: each exchange pushes what fits in the room the last one reported and pops
    what the level says is waiting, as a real host polling over SPI would."""

    def __init__(self, dut):
        self.dut, self.got, self.free = dut, [], 0

    async def exchange(self, words=()):
        """One XFER. Returns the words that didn't fit."""
        words = list(words)
        n = min(len(words), self.free)
        level, free, got, _ = await fifo_xfer(self.dut, words[:n], slots=max(n, 8))
        self.got.extend(got)
        self.free = max(free - n, 0)
        return words[n:]

    async def send(self, words, poll=300):
        words = list(words)
        while True:
            words = await self.exchange(words)
            if not words:
                return
            await wait_cycles(poll)

    async def collect(self, count, poll=500, limit=20000):
        """Poll until `count` words have come in, and return them (leaving any extra for later)."""
        for _ in range(limit):
            if len(self.got) >= count:
                break
            await self.exchange()
            if len(self.got) < count:
                await wait_cycles(poll)
        out, self.got = self.got[:count], self.got[count:]
        return out

    async def poll_for(self, cycles, poll=20000):
        """Keep the engine-to-host FIFO drained for a while."""
        end = cycles_now() + cycles
        while cycles_now() < end:
            await self.exchange()
            await wait_cycles(poll)


def cycles_now():
    return get_sim_time("ns") // CYCLE_NS


async def wait_cycles(n):
    """A plain timer: ClockCycles wakes Python every clock, which makes millisecond waits crawl."""
    if n > 0:
        await Timer(int(n) * CYCLE_NS, "ns")


async def wait_until(cycle):
    now = cycles_now()
    if cycle > now:
        await Timer(int(cycle - now) * CYCLE_NS, "ns")


class LineLog:
    """Records every change of the given lines' pads, event by event: {line: [(cycle, level)]}."""

    def __init__(self, dut, lines, idle=0):
        self.dut, self.lines, self.idle = dut, lines, idle
        self.edges = {n: [] for n in lines}
        self.task = cocotb.start_soon(self.watch())

    async def watch(self):
        while True:
            out, oe = int(self.dut.uio_out.value), int(self.dut.uio_oe.value)
            for n in self.lines:
                v = pad(out, oe, n, self.idle)
                if not self.edges[n] or self.edges[n][-1][1] != v:
                    self.edges[n].append((cycles_now(), v))
            await First(Edge(self.dut.uio_out), Edge(self.dut.uio_oe))

    def close(self):
        self.task.cancel()


async def play(dut, edges, lines, base=0xFF):
    """Drive input lines from [(cycle offset, level or tuple of levels)], one level per line in `lines`."""
    t0 = cycles_now()
    value = base
    dut.uio_in.value = value  # idle levels from the start, not whatever the last test left
    for t, levels in edges:
        await wait_until(t0 + t)
        levels = levels if isinstance(levels, tuple) else (levels,)
        for n, v in zip(lines, levels):
            value = value & ~(1 << n) | (v << n)
        dut.uio_in.value = value


# ------------------------------------------------------------------ BiSS-C
# From the TI BiSS-C master design guide (TIDU794A) and Renishaw's BiSS C-mode data sheet (L-9709-9005-05-A):
# MA and SLO idle high. The master starts clocking MA; at the 2nd rising edge the encoder pulls SLO low
# (Ack), longer if it needs processing time; then a start bit (1), CDS, position MSB first, nE, nW (both
# active low), then a 6-bit CRC, x^6 + x + 1, start value 0, over position, nE and nW, sent MSB first and
# inverted. The encoder changes SLO on MA's rising edges. After the last bit it holds SLO low (timeout)
# until it is ready, then high. CDM is the inverse of MA's level during the timeout. Line delay is the
# time from the master's 2nd MA rise to the Ack's falling edge on SLO.

def biss_crc6(bits):
    """Bitwise long division by x^6 + x + 1, register starting at 0."""
    reg = 0
    for b in bits:
        top = (reg >> 5) & 1
        reg = (reg << 1) & 0x3F
        if top ^ b:
            reg ^= 0x03
    return reg


class BissEncoder:
    """A BiSS-C slave seen through a cable: MA reaches it `delay` cycles late, and SLO comes back as late again.
    window > 0 makes SLO correct only from `window` cycles after each MA rise to `window` before the next,
    and wrong outside, so a master sampling away from the middle of the bit reads errors."""

    def __init__(self, bits, ack=1, delay=0, timeout=600, period=12, window=0, crc_flip=0, latency=2):
        self.bits, self.ack, self.delay, self.timeout = list(bits), ack, delay, timeout
        self.period, self.window, self.crc_flip, self.latency = period, window, crc_flip, latency
        self.cdm_log = []
        self.cds = 0
        self.ma_line = [1] * (delay + 1)
        self.slo_line = [1] * (delay + latency + 1)
        self.state, self.rises, self.ma, self.quiet, self.since_rise = "ready", 0, 1, 0, 10**6
        self.out, self.frames, self.rise_times = [], 0, []
        self.slo, self.sent = 1, 0
        self.round_trip = 2 * delay + latency + 2  # pad to pad, through both delay lines

    def frame_bits(self):
        crc = biss_crc6(self.bits) ^ self.crc_flip
        return [1, self.cds] + self.bits + [((~crc) >> (5 - k)) & 1 for k in range(6)]

    def step(self, ma_master):
        self.ma_line.append(ma_master)
        ma = self.ma_line.pop(0)
        rise, fall = ma and not self.ma, self.ma and not ma
        self.ma = ma
        self.quiet = 0 if (rise or fall) else self.quiet + 1
        self.since_rise = 0 if rise else self.since_rise + 1
        if self.state == "ready" and fall:
            self.state, self.rises = "request", 0
        elif self.state in ("request", "data") and rise:
            self.rises += 1
            if self.rises == 2:
                self.state, self.out = "data", [0] * self.ack + self.frame_bits() + [0]
                self.frames, self.sent = self.frames + 1, 0
            if self.state == "data":
                self.slo = self.out.pop(0) if self.out else 0
                if not self.out:
                    self.state = "timeout"
        elif self.state == "data" and self.quiet > self.timeout:
            self.state = "ready"  # the master stopped early: the encoder resets
            self.slo = 1
        if self.state == "timeout" and self.quiet >= self.timeout:
            self.cdm_log.append(1 - ma)
            self.cds = 1 - ma  # a stand-in for control communication: echo the last CDM as CDS
            self.state, self.slo = "ready", 1
        level = self.slo
        if self.state == "data" and self.since_rise == 0:
            self.sent += 1
        # The Ack's falling edge is clean: it's the reference the master measures. Every later bit is
        # wrong near its edges
        if self.window and self.sent > 1 and self.state in ("data", "timeout") and self.since_rise < self.period:
            if self.since_rise < self.window or self.since_rise >= self.period - self.window:
                level = 1 - level
        self.slo_line.append(level)
        return self.slo_line.pop(0)


def biss_request(nbits, cdm=0):
    nfull = (nbits - 1) // 16
    m0 = nbits - 16 * nfull
    return cdm << 15 | m0 << 8 | nfull


def biss_value(words, nbits):
    """Data words from the master back to one integer of nbits, MSB first."""
    nfull = (nbits - 1) // 16
    m0 = nbits - 16 * nfull
    value = words[0] & ((1 << m0) - 1)
    for w in words[1:1 + nfull]:
        value = value << 16 | w
    return value


def bits_of(value, n):
    return [(value >> (n - 1 - k)) & 1 for k in range(n)]


async def biss_session(dut, encoder, frames, period=12):
    """Run the BiSS master against `encoder`: frames is [(position, nbits, cdm)]. Returns each frame's reply
    as (value, status)."""
    prog = firmware("trial2_biss.s", P=period)
    await stop(dut)
    await load_program(dut, prog.words)
    await set_two(dut, prog, pull_sel=0x04, push_sel=0x01)  # receiver pulls link 0, clock context pushes to it
    await start(dut)
    host = Host(dut)
    await host.exchange()

    def wire(t, out, oe):
        ma = pad(out, oe, 0)
        return 0xFC | encoder.step(ma) << 1 | ma  # the MA pad reads back what the chip drives

    task = cocotb.start_soon(capture(dut, 10**7, wire))
    replies = []
    for value, nbits, cdm in frames:
        encoder.bits = bits_of(value, nbits)
        await host.send([biss_request(nbits, cdm)])
        words = await host.collect((nbits - 1) // 16 + 2, poll=200)
        replies.append((biss_value(words, nbits), words[-1]))
    task.cancel()
    await clear_contexts(dut)
    return replies, len(prog.words)


def biss_check(replies, frames, encoder, crc_bad=False):
    for (value, status), (want, nbits, _) in zip(replies, frames):
        assert status & 0x2000 == 0, f"no Ack or start bit: {status:#06x}"
        assert value == want, f"position {value:#x}, sent {want:#x}"
        assert bool(status & 0x4000) == crc_bad, f"CRC flag in {status:#06x}"
        assert status & 0x1FFF == encoder.round_trip, f"delay {status & 0x1FFF}, cable {encoder.round_trip}"


@cocotb.test()
async def test_biss_line_delay_compensation(dut):
    """Delays from none to over 4 bit times, with SLO only valid in the middle 2 cycles of each 12-cycle bit,
    so a sample point a cycle off the centre fails. 26-bit position plus nE and nW, 4.17 MHz."""
    await reset(dut)
    period = 12
    for delay in (0, 1, 6, 13, 25, 50):
        encoder = BissEncoder([], ack=1 + delay % 3, delay=delay, period=period, window=period // 2 - 1)
        frames = [(0x2A5F00D | 3, 28, 0), (0x0123457, 28, 0)]
        replies, words = await biss_session(dut, encoder, frames, period)
        biss_check(replies, frames, encoder)
        dut._log.info(f"delay {delay}: measured {replies[0][1] & 0x1FFF} cycles, {words} words")


@cocotb.test()
async def test_biss_lengths_rates_and_processing_time(dut):
    """18, 26+2, 36+2 and 64-bit data, MA from 4.17 MHz down to 1.04 MHz (the period must be a multiple of 4), and Ack stretched up to 20 bits."""
    await reset(dut)
    for period, nbits, ack in ((12, 20, 1), (16, 38, 7), (24, 64, 20), (48, 28, 3)):
        encoder = BissEncoder([], ack=ack, delay=9, period=period, window=period // 4)
        frames = [((0xC0FFEE123456789A >> (64 - nbits)) | 1, nbits, 0), (1 << (nbits - 1), nbits, 0)]
        replies, _ = await biss_session(dut, encoder, frames, period)
        biss_check(replies, frames, encoder)


@cocotb.test()
async def test_biss_cdm_cds_and_errors(dut):
    """CDM reaches the encoder as MA's level in the timeout, and comes back as CDS in the next frame. A bad CRC
    is flagged. With no encoder (SLO stays high) the master gives up and says so."""
    await reset(dut)
    encoder = BissEncoder([], delay=4, period=12)
    frames = [(0x155, 28, cdm) for cdm in (1, 0, 1, 1, 0)]
    replies, _ = await biss_session(dut, encoder, frames)
    assert encoder.cdm_log[:5] == [1, 0, 1, 1, 0], encoder.cdm_log
    assert [s >> 15 for _, s in replies] == [0, 1, 0, 1, 1], [hex(s) for _, s in replies]
    encoder = BissEncoder([], delay=4, period=12, crc_flip=0x21)
    replies, _ = await biss_session(dut, encoder, [(0x155, 28, 0)])
    biss_check(replies, [(0x155, 28, 0)], encoder, crc_bad=True)

    class Absent(BissEncoder):
        def step(self, ma):
            return 1

    replies, _ = await biss_session(dut, Absent([]), [(0, 28, 0), (0, 28, 0)])
    assert [s for _, s in replies] == [0x2000, 0x2000], [hex(s) for _, s in replies]


# ------------------------------------------------------------------ NEC infrared
# From SB-Projects' NEC page and Altium's NEC protocol note: a 9 ms burst and 4.5 ms space lead a frame of
# 32 bits, LSB first: address, inverted address, command, inverted command (extended NEC uses 16 address
# bits). A 0 is a 562.5 us burst and 562.5 us space, a 1 the burst and 1.6875 ms of space. A final burst ends
# the frame. A held key sends repeat codes, a 9 ms burst, 2.25 ms space and a burst, every 108 ms from the
# start of the frame. Bursts are a 38 kHz carrier, with a duty cycle of 1/3 or 1/4 recommended.
NEC_UNIT = 28125  # 562.5 us in 20 ns cycles


def nec_word32(address, command):
    return address | (~address & 0xFF) << 8 | command << 16 | (~command & 0xFF) << 24


def nec_bursts(edges, gap=4000):
    """Group carrier pulses into bursts. edges: [(cycle, level)] of the LED line. Returns a list of
    (start, end, pulse starts, pulse widths), end being the last pulse's falling edge."""
    bursts, rise = [], None
    for t, level in edges:
        if level and rise is None:
            rise = t
        elif not level and rise is not None:
            if bursts and rise - bursts[-1][1] < gap:
                bursts[-1][1] = t
                bursts[-1][2].append(rise)
                bursts[-1][3].append(t - rise)
            else:
                bursts.append([rise, t, [rise], [t - rise]])
            rise = None
    return bursts


def nec_check_carrier(bursts, scale=1):
    """Every pulse inside every burst at 38 kHz (times `scale`) +-1% and a duty cycle between 1/4 and 1/2.
    Returns the worst period and duty seen."""
    periods = [b - a for burst in bursts for a, b in zip(burst[2], burst[2][1:])]
    duties = [w for burst in bursts for w in burst[3]]
    lo, hi = min(periods), max(periods)
    assert 50e6 / hi > 38000 * scale * 0.99 and 50e6 / lo < 38000 * scale * 1.01, f"carrier period {lo}..{hi}"
    assert all(0.25 * lo <= w <= 0.5 * hi for w in duties), f"pulse widths {min(duties)}..{max(duties)}"
    return lo, hi, min(duties), max(duties)


def nec_decode_tx(bursts, tol=0.02, unit=NEC_UNIT):
    """Decode transmitted bursts by their start-to-start times, as units of 562.5 us. Returns a list of
    ('frame', value, start) and ('repeat', None, start), checking every burst length and interval."""
    def near(x, n):
        return abs(x - n * unit) <= tol * n * unit

    def burst(b, n):  # first rise to last fall: up to one carrier period short of the envelope
        return -unit / 20 - tol * n * unit <= (b[1] - b[0]) - n * unit <= tol * n * unit

    out, k = [], 0
    while k < len(bursts):
        start, end = bursts[k][0], bursts[k][1]
        assert burst(bursts[k], 16), f"leader burst {end - start} cycles at {start}"
        gap = bursts[k + 1][0] - start
        if near(gap, 20):
            assert burst(bursts[k + 1], 1)
            out.append(("repeat", None, start))
            k += 2
            continue
        assert near(gap, 24), f"leader to first bit {gap} cycles"
        value = 0
        for i in range(32):
            b = bursts[k + 1 + i]
            assert burst(b, 1), f"bit {i} burst {b[1] - b[0]} cycles"
            step = bursts[k + 2 + i][0] - b[0]
            assert near(step, 2) or near(step, 4), f"bit {i} start-to-start {step} cycles"
            value |= int(near(step, 4)) << i
        last = bursts[k + 33]
        assert burst(last, 1), "final burst"
        out.append(("frame", value, start))
        k += 34
    return out


def nec_rx_wave(items, scale=1.0, widen=0, start=2000, gap_units=40, unit=NEC_UNIT):
    """The output of a demodulating IR receiver: low during bursts. items: ('frame', 32-bit value) or
    ('repeat',) or ('raw', [(level, units), ...]). scale stretches every time (transmitter clock error);
    widen lengthens every burst by that many cycles and shortens the next space as much, as receiver modules
    do. Returns [(cycle, level)] edges."""
    u = unit * scale
    edges, t = [], start
    for item in items:
        if item[0] == "frame":
            v = item[1]
            seq = [(0, 16), (1, 8)] + sum(([(0, 1), (1, 3 if (v >> i) & 1 else 1)] for i in range(32)), []) + [(0, 1)]
        elif item[0] == "repeat":
            seq = [(0, 16), (1, 4), (0, 1)]
        else:
            seq = item[1]
        for level, units in seq + [(1, gap_units)]:
            edges.append((round(t + (widen if level else 0)), level))
            t += units * u
    return edges


def nec_rx_parse(words):
    """Host side: split the receiver's words into frames, repeats and errors."""
    out, k = [], 0
    while k < len(words):
        w = words[k]
        if w >> 12 == 0xF and (w >> 8) & 0xF == 0:
            out.append(("frame", w & 3, words[k + 1] | words[k + 2] << 16))
            k += 3
        elif w == 0xF100:
            out.append(("repeat",))
            k += 1
        else:
            out.append(("error", w))
            k += 1
    return out


# ------------------------------------------------------------------ NMRA DCC
# From NMRA S-9.1: a command station sends a 1 as two halves of 55 to 61 us (nominal 58) differing by at most
# 3 us, and a 0 as two halves of 95 to 9900 us (nominal 100), 12 ms at most in all. A decoder must accept 1
# halves of 52 to 64 us and 0 halves of 90 to 10000 us, and can't know the polarity. From S-9.2: a packet is
# a preamble of at least 14 ones as sent (a decoder needs 10), a 0 start bit, then bytes each followed by a 0
# when another byte follows, or a 1 after the last, which is the XOR of all the others. The idle packet is
# FF 00 FF.
US = 50  # cycles per microsecond


def dcc_packet_bits(data, preamble=14, spoil=0):
    """spoil: XORed into the error-detection byte, to make a bad packet."""
    check = spoil
    for b in data:
        check ^= b
    out = [1] * preamble
    for b in list(data) + [check]:
        out += [0] + [(b >> (7 - k)) & 1 for k in range(8)]
    return out + [1]


def dcc_halves(bits, one=58, zero=100, stretch=None):
    """Half-bit lengths in us. stretch: {bit index: (first half, second half)} for stretched zeros."""
    out = []
    for i, b in enumerate(bits):
        if stretch and i in stretch:
            out += list(stretch[i])
        else:
            out += [one, one] if b else [zero, zero]
    return out


def dcc_edges(halves, start=1000, level=1):
    """Edges of the track signal, as (cycle, level), starting high (or low, for the other polarity)."""
    edges, t = [], start
    for h in halves:
        edges.append((round(t), level))
        level ^= 1
        t += h * US
    edges.append((round(t), level))
    return edges


def dcc_decode_tx(edges, complement=None):
    """A command-station checker: every half within S-9.1's transmit limits, then packets found by their
    preamble. Returns [(bytes, preamble length)]."""
    halves = [(b[0] - a[0]) / US for a, b in zip(edges, edges[1:])]
    kinds = []
    for h in halves:
        if 55 <= h <= 61:
            kinds.append(1)
        elif 95 <= h <= 9900:
            kinds.append(0)
        else:
            raise AssertionError(f"half bit of {h} us is neither a 1 nor a 0 half")
    packets, k, ones = [], 0, 0
    while k + 1 < len(kinds):
        if kinds[k] == 1:
            ones, k = ones + 1, k + 1
            continue
        if not packets and ones < 28:  # the signal began mid-packet: wait for a whole preamble
            ones, k = 0, k + 1
            continue
        # a 0 half: the packet start bit, aligned from here
        assert kinds[k + 1] == 0 and ones >= 28, f"start bit after {ones} one halves"
        pre, k, data = ones // 2, k + 2, []
        while True:
            if k + 18 > len(kinds):
                return packets
            pairs = [(kinds[k + 2 * j], kinds[k + 2 * j + 1]) for j in range(9)]
            assert all(a == b for a, b in pairs), f"halves disagree in {pairs}"
            for j in range(0, 18, 2):
                assert abs(halves[k + j] - halves[k + j + 1]) <= 3 or kinds[k + j] == 0
            bits = [a for a, _ in pairs]
            data.append(sum(b << (7 - j) for j, b in enumerate(bits[:8])))
            k += 18
            if bits[8] == 1:
                break
        check = 0
        for b in data:
            check ^= b
        assert check == 0, f"packet {data} fails its error-detection byte"
        packets.append((data, pre))
        ones = 0
    return packets


# ------------------------------------------------------------------ ARINC 429
# From Abaco's ARINC 429 electrical notes and the 1553couplers ARINC 429 page: bipolar return-to-zero, HI for
# a 1 and LO for a 0 in the first half of each bit and NULL in the second, at 100 kbit/s (+-1%) or
# 12 to 14.5 kbit/s. A word is 32 bits, bit 1 first, and at least 4 bit times of NULL separate words. Bits
# 1-8 are the label, sent with its most significant bit first; bit 32 is odd parity. The two logic lines
# here stand for a driver's (or receiver's) HI and LO signals.

def arinc_bits(label, data, parity_ok=True):
    """32 bits in sending order. data: ARINC bits 9..31, bit 9 first, as an integer (bit 9 in bit 0)."""
    bits = [(label >> (7 - k)) & 1 for k in range(8)] + [(data >> k) & 1 for k in range(23)]
    p = 1 - sum(bits) % 2
    return bits + [p if parity_ok else 1 - p]


def arinc_host_words(bits):
    """The host's view: w0 = bits 16..9 << 8 | label (usual order), w1 = bits 32..17 (bit 17 in bit 0)."""
    label = sum(b << (7 - k) for k, b in enumerate(bits[:8]))
    w0 = label | sum(b << (8 + k) for k, b in enumerate(bits[8:16]))
    w1 = sum(b << k for k, b in enumerate(bits[16:32]))
    return w0, w1


def arinc_edges(words, bit=500, start=2000, gap_bits=4, jitter=None, width=0.5):
    """Line receiver outputs for a list of 32-bit lists. Returns [(cycle, (hi, lo))]."""
    out, t = [], start
    for bits in words:
        for k, b in enumerate(bits):
            j = jitter(k) if jitter else 0
            out.append((round(t + j), (b, 1 - b)))
            out.append((round(t + j + width * bit), (0, 0)))
            t += bit
        t += gap_bits * bit
    return out


def arinc_decode_tx(edges_a, edges_b, bit, tol=0.01):
    """Words from the driver's two lines, checking the waveform: never both high, each pulse half a bit
    (+-5%), pulses a bit apart within each word (+-tol), and at least 4 bit times between words."""
    times = sorted({t for t, _ in edges_a} | {t for t, _ in edges_b})

    def level(edges, t):
        v = 0
        for when, lv in edges:
            if when > t:
                break
            v = lv
        return v

    pulses = []
    for t in times:
        a, b = level(edges_a, t), level(edges_b, t)
        assert not (a and b), f"HI and LO together at {t}"
        if a or b:
            pulses.append([t, None, a])
        elif pulses and pulses[-1][1] is None:
            pulses[-1][1] = t
    words, cur = [], []
    for k, (t0, t1, v) in enumerate(pulses):
        assert t1 is not None and abs((t1 - t0) - bit / 2) <= 0.05 * bit / 2, f"pulse {t1 and t1 - t0} at {t0}"
        if cur and t0 - cur[-1][0] > 1.5 * bit:
            assert len(cur) == 32, f"word of {len(cur)} bits"
            assert t0 - cur[-1][0] >= 5 * bit - tol * bit, f"gap {t0 - cur[-1][0]} cycles"
            words.append([b for _, b in cur])
            cur = []
        if cur:
            assert abs(t0 - cur[-1][0] - bit) <= tol * bit, f"bit period {t0 - cur[-1][0]}"
        cur.append((t0, v))
    if len(cur) == 32:
        words.append([b for _, b in cur])
    return words


# ------------------------------------------------------------------ the tests: NEC
async def run_program(dut, name, nctx=1, odrain=0, **defines):
    prog = firmware(name, **defines)
    await stop(dut)
    await load_program(dut, prog.words)
    await write_reg(dut, 1, odrain)
    if nctx > 1:
        await set_two(dut, prog, nctx=nctx)
    await start(dut)
    return prog


# The NEC tests at full rate take 11.5k cycles a second of simulation, so a 108 ms repeat interval costs
# about 8 minutes. The full-rate tests cover a frame and a repeat each way. The sweeps run the same firmware
# with every time constant divided by 5 (NEC_FAST): the code paths and counts are the same, only the cycle
# constants change, as if the chip ran at 250 MHz.
NEC_FAST_UNIT = NEC_UNIT // 5
NEC_FAST_TX = dict(UNIT=NEC_FAST_UNIT, CAR=263, HI=88)


async def nec_tx_capture(dut, commands, units, unit=NEC_UNIT, **defines):
    await run_program(dut, "trial2_nec_tx.s", **defines)
    log = LineLog(dut, [0], idle=0)
    host = Host(dut)
    await host.exchange()
    await host.send(commands)
    await wait_cycles(units * unit)
    log.close()
    await clear_contexts(dut)
    return nec_bursts(log.edges[0][1:], gap=3 * unit // 7)


@cocotb.test()
async def test_nec_tx_frame_and_repeat(dut):
    """Full rate: a frame and a repeat code. Every burst on the 562.5 us grid to 0.05%, 108 ms between starts,
    a 38 kHz carrier with a third duty cycle."""
    await reset(dut)
    v = nec_word32(0x59, 0x16)
    bursts = await nec_tx_capture(dut, [0x0001, v & 0xFFFF, v >> 16], 192 + 30)
    lo, hi, wmin, wmax = nec_check_carrier(bursts)
    got = nec_decode_tx(bursts, tol=0.0005)
    assert [(k, x) for k, x, _ in got] == [("frame", v), ("repeat", None)], got
    assert abs(got[1][2] - got[0][2] - 192 * NEC_UNIT) <= 2, got
    dut._log.info(f"carrier period {lo}-{hi} cycles, pulses {wmin}-{wmax}, starts {[s for _, _, s in got]}")


@cocotb.test()
async def test_nec_tx_fast_repeats_extended_and_repeat_only(dut):
    """At NEC_FAST: a frame with two repeats, extended NEC, then a repeat-only command, which waits out the
    108 ms (192 units) before it starts."""
    await reset(dut)
    u = NEC_FAST_UNIT
    v = nec_word32(0x59, 0x16)
    x = 0x7F80 | 0x1D << 16 | (~0x1D & 0xFF) << 24
    bursts = await nec_tx_capture(dut, [0x0002, v & 0xFFFF, v >> 16, 0x0000, x & 0xFFFF, x >> 16, 0x8001],
                                  5 * 192 + 30, unit=u, **NEC_FAST_TX)
    nec_check_carrier(bursts, scale=5)
    got = nec_decode_tx(bursts, tol=0.0005, unit=u)
    assert [(k, w) for k, w, _ in got] == [("frame", v), ("repeat", None), ("repeat", None), ("frame", x),
                                           ("repeat", None)], got
    starts = [s for _, _, s in got]
    assert all(abs(b - a - 192 * u) <= 2 for a, b in zip(starts[:3], starts[1:3])), starts
    assert all(192 * u <= b - a <= 193 * u for a, b in zip(starts[2:], starts[3:])), starts


async def nec_receive(dut, items, extra_units=60, unit=NEC_UNIT, **wave):
    await run_program(dut, "trial2_nec_rx.s", UNIT=unit)
    host = Host(dut)
    await host.exchange()
    edges = nec_rx_wave(items, unit=unit, **wave)
    player = cocotb.start_soon(play(dut, edges, [4]))
    await host.poll_for(edges[-1][0] + extra_units * unit, poll=40 * unit)
    await player
    await host.exchange()
    words = host.got
    await clear_contexts(dut)
    return nec_rx_parse(words)


@cocotb.test()
async def test_nec_rx_frame_and_repeat(dut):
    """Full rate: a frame and a repeat code from a receiver module."""
    await reset(dut)
    v = nec_word32(0x59, 0x16)
    got = await nec_receive(dut, [("frame", v), ("repeat",)], gap_units=20, extra_units=8)
    assert got == [("frame", 3, v), ("repeat",)], got


@cocotb.test()
async def test_nec_rx_fast_tolerance(dut):
    """At NEC_FAST: extended NEC, the sender's clock 10% fast or slow, and bursts 120 us longer or shorter
    than nominal (receiver modules distort them), alone and combined."""
    await reset(dut)
    u = NEC_FAST_UNIT
    v, x = nec_word32(0x59, 0x16), 0x12345678
    got = await nec_receive(dut, [("frame", x), ("repeat",)], unit=u)
    assert got == [("frame", 0, x), ("repeat",)], got
    for wave in (dict(scale=0.9), dict(scale=1.1), dict(widen=6000 // 5), dict(widen=-6000 // 5),
                 dict(scale=0.9, widen=6000 // 5), dict(scale=1.1, widen=-6000 // 5)):
        got = await nec_receive(dut, [("frame", v), ("repeat",)], unit=u, **wave)
        assert got == [("frame", 3, v), ("repeat",)], f"{wave}: {got}"


@cocotb.test()
async def test_nec_rx_fast_errors(dut):
    """At NEC_FAST: a bit with a 2-unit space, a frame cut off after 5 bits, and a 5 ms leader burst are
    reported. A 100 us glitch is ignored, and the frame after it still decodes."""
    await reset(dut)
    v = nec_word32(0x01, 0xFE)
    bad_space = [(0, 16), (1, 8), (0, 1), (1, 2), (0, 1)]
    short = [(0, 16), (1, 8)] + [(0, 1), (1, 1)] * 5
    got = await nec_receive(dut, [("raw", bad_space), ("raw", short), ("raw", [(0, 9)]),
                                  ("raw", [(0, 0.18)]), ("frame", v)], unit=NEC_FAST_UNIT)
    # The cut-off frame reports a bit space (4), not a bit burst (3): its five bits are whole, and what's
    # wrong is the space after the last one, which never ends
    assert got == [("error", 0xE004), ("error", 0xE004), ("error", 0xE001), ("frame", 3, v)], got


class IrChannel:
    """An IR link from line 0's LED to a demodulating receiver on line 4: the output goes low at a carrier
    pulse and comes back high `hold` cycles after the last one."""

    def __init__(self, dut, hold=3000 // 5):
        self.dut, self.hold = dut, hold
        self.task = cocotb.start_soon(self.run())

    def set(self, v):
        self.dut.uio_in.value = int(self.dut.uio_in.value) & ~0x10 | v << 4

    async def run(self):
        while True:
            await Edge(self.dut.uio_out)
            if not pad(int(self.dut.uio_out.value), int(self.dut.uio_oe.value), 0, 0):
                continue
            self.set(0)
            deadline = cycles_now() + self.hold
            while cycles_now() < deadline:
                await First(Edge(self.dut.uio_out), Timer((deadline - cycles_now()) * CYCLE_NS, "ns"))
                if pad(int(self.dut.uio_out.value), int(self.dut.uio_oe.value), 0, 0):
                    deadline = cycles_now() + self.hold
            self.set(1)

    def close(self):
        self.task.cancel()


@cocotb.test()
async def test_nec_loopback_two_contexts(dut):
    """At NEC_FAST: transmitter and receiver at once, in two contexts of one chip, through a modelled IR
    link. The transmitter pulls from the host, the receiver pushes to it."""
    await reset(dut)
    prog = combined("trial2_nec_tx.s", "trial2_nec_rx.s", NCTX=2, **NEC_FAST_TX)
    await stop(dut)
    await load_program(dut, prog.words)
    await set_two(dut, prog)
    await start(dut)
    channel = IrChannel(dut)
    host = Host(dut)
    await host.exchange()
    v = nec_word32(0xA5, 0x3C)
    await host.send([0x0001, v & 0xFFFF, v >> 16])
    await host.poll_for(2 * 192 * NEC_FAST_UNIT, poll=40 * NEC_FAST_UNIT)
    await host.exchange()
    channel.close()
    await clear_contexts(dut)
    assert nec_rx_parse(host.got) == [("frame", 3, v), ("repeat",)], host.got


# ------------------------------------------------------------------ the tests: DCC
async def dcc_tx_capture(dut, packets, cycles):
    await run_program(dut, "trial2_dcc_tx.s")
    log = LineLog(dut, [0, 1], idle=1)
    host = Host(dut)
    await host.exchange()
    for data in packets:
        await host.send([len(data)] + list(data))
    await wait_cycles(cycles)
    log.close()
    await clear_contexts(dut)
    return log.edges


@cocotb.test()
async def test_dcc_tx_packets_and_idle(dut):
    """Two host packets and the idle packets around them: every half within S-9.1's transmit limits, 14 ones of
    preamble, the error byte right, and the second line always the complement of the first."""
    await reset(dut)
    packets = [[0x03, 0x3F, 0x9A], [0xC1, 0x23, 0x45, 0x67, 0x89]]
    edges = await dcc_tx_capture(dut, packets, 1500000)
    e0, e1 = edges[0], edges[1]
    start = e0[2][0]  # the first edge after both lines are the engine's
    assert [t for t, _ in e0 if t >= start] == [t for t, _ in e1 if t >= start], "lines don't switch together"
    assert all(a != b for (_, a), (_, b) in zip([x for x in e0 if x[0] >= start], [x for x in e1 if x[0] >= start]))
    got = dcc_decode_tx([x for x in e0 if x[0] >= start])
    datas = [d[:-1] for d, _ in got]
    # the first packet's count can include the tail of one cut short when the capture began
    assert all(pre >= 14 for _, pre in got) and all(pre == 14 for _, pre in got[1:]), got
    hosts = [d for d in datas if d != [0xFF, 0x00]]
    assert hosts == packets, datas
    assert datas[0] == [0xFF, 0x00] or datas[0] == packets[0], datas


async def dcc_receive(dut, halves, level=1, program="trial2_dcc_rx.s"):
    await run_program(dut, program)
    host = Host(dut)
    await host.exchange()
    edges = dcc_edges(halves, level=level)
    player = cocotb.start_soon(play(dut, edges, [4]))
    await host.poll_for(edges[-1][0] + 20000, poll=50000)
    await player
    await host.exchange()
    await clear_contexts(dut)
    return host.got


def dcc_expect(*packets):
    out = []
    for data, status in packets:
        check = 0
        for b in data:
            check ^= b
        out += list(data) + [check, status] if status == 0xA000 else list(data) + [status]
    return out


@cocotb.test()
async def test_dcc_rx_packets_polarity_and_limits(dut):
    """Packets at nominal timing in both polarities, then at the edges of S-9.1's receive window: 1 halves of
    52 and 64 us and 0 halves of 90 us."""
    await reset(dut)
    bits = dcc_packet_bits([0x03, 0x3F, 0x9A]) + dcc_packet_bits([0xFF, 0x00]) + [1] * 4
    want = dcc_expect(([0x03, 0x3F, 0x9A], 0xA000), ([0xFF, 0x00], 0xA000))
    for level in (1, 0):
        got = await dcc_receive(dut, dcc_halves(bits), level)
        assert got == want, f"polarity {level}: {[hex(w) for w in got]}"
    for one, zero in ((52, 90), (64, 90), (52, 400), (64, 120)):
        got = await dcc_receive(dut, dcc_halves(bits, one=one, zero=zero))
        assert got == want, f"one {one} zero {zero}: {[hex(w) for w in got]}"


@cocotb.test()
async def test_dcc_rx_stretched_zero_and_errors(dut):
    """A stretched zero (100 us and 9,800 us halves) decodes. A bad error byte, a bit with a 1 half and a 0
    half, halves of 48 and 75 us, and a 9-bit preamble are all rejected."""
    await reset(dut)
    bits = dcc_packet_bits([0x41, 0x76])
    zero_at = bits.index(0, 14 + 1)  # a 0 inside the first byte
    got = await dcc_receive(dut, dcc_halves(bits + [1] * 4, stretch={14: (100, 9800), zero_at: (9800, 100)}))
    assert got == dcc_expect(([0x41, 0x76], 0xA000)), [hex(w) for w in got]
    got = await dcc_receive(dut, dcc_halves(dcc_packet_bits([0x41, 0x76], spoil=0x10) + [1] * 4))
    assert got == [0x41, 0x76, 0x76 ^ 0x41 ^ 0x10, 0xE001], [hex(w) for w in got]
    halves = dcc_halves(bits + [1] * 4)
    mixed = list(halves)
    mixed[2 * 20 + 1] = 58  # second half of a 0 bit in the first byte made short
    got = await dcc_receive(dut, mixed)
    assert got[-1] == 0xE002 and len(got) == 1, [hex(w) for w in got]
    for one in (48, 75):
        got = await dcc_receive(dut, dcc_halves(bits + [1] * 4, one=one))
        assert got == [], f"one halves of {one} us: {[hex(w) for w in got]}"
    got = await dcc_receive(dut, dcc_halves([1] * 9 + bits[14:] + [1] * 4))
    assert got == [], [hex(w) for w in got]


@cocotb.test()
async def test_dcc_loopback_two_contexts(dut):
    """Command station and decoder in two contexts, the track wired from line 0 to line 4."""
    await reset(dut)
    prog = combined("trial2_dcc_tx.s", "trial2_dcc_rx.s")
    await stop(dut)
    await load_program(dut, prog.words)
    await set_two(dut, prog)
    await start(dut)

    async def wire():
        while True:
            await Edge(dut.uio_out)
            v = pad(int(dut.uio_out.value), int(dut.uio_oe.value), 0)
            dut.uio_in.value = int(dut.uio_in.value) & ~0x10 | v << 4

    task = cocotb.start_soon(wire())
    host = Host(dut)
    await host.exchange()
    await host.send([2, 0x37, 0x6A])
    await host.poll_for(800000, poll=40000)
    await host.exchange()
    task.cancel()
    await clear_contexts(dut)
    packets, cur = [], []
    for w in host.got:
        if w >> 12:
            packets.append((cur, w))
            cur = []
        else:
            cur.append(w)
    assert ([0x37, 0x6A, 0x37 ^ 0x6A], 0xA000) in packets, packets
    assert all(s == 0xA000 for _, s in packets) and len(packets) >= 2, packets
    assert all(p in ([0xFF, 0x00, 0xFF], [0x37, 0x6A, 0x5D]) for p, _ in packets), packets


# ------------------------------------------------------------------ the tests: ARINC 429
async def arinc_tx_capture(dut, words, half=250):
    await run_program(dut, "trial2_arinc_tx.s", HALF=half)
    log = LineLog(dut, [0, 1], idle=0)
    host = Host(dut)
    await host.exchange()
    feed = sum((list(arinc_host_words(b)) for b in words), [])
    await host.send(feed)
    await wait_cycles(len(words) * 40 * 2 * half + 4000)
    log.close()
    await clear_contexts(dut)
    return arinc_decode_tx(log.edges[0], log.edges[1], 2 * half)


@cocotb.test()
async def test_arinc_tx_words_both_rates(dut):
    """Words at 100 and 12.5 kbit/s: label MSB first, data LSB first, odd parity put right even when the host
    gives it wrong, half-bit pulses, never HI and LO together, and 4 bit times between words."""
    await reset(dut)
    words = [arinc_bits(0o203, 0x2A5A5A), arinc_bits(0o377, 0), arinc_bits(0o001, 0x7FFFFF)]
    host_words = [arinc_bits(0o203, 0x2A5A5A, parity_ok=False)] + words[1:]
    for half in (250, 2000):
        got = await arinc_tx_capture(dut, host_words, half)
        assert got == words, f"half {half}: {got}"


async def arinc_receive(dut, edges, bit=500):
    await run_program(dut, "trial2_arinc_rx.s", BIT=bit)
    host = Host(dut)
    await host.exchange()
    player = cocotb.start_soon(play(dut, edges, [4, 5], base=0xCF))
    await host.poll_for(edges[-1][0] + 6 * bit, poll=4000)
    await player
    await host.exchange()
    await clear_contexts(dut)
    got = host.got
    return [tuple(got[k:k + 3]) for k in range(0, len(got), 3)]


@cocotb.test()
async def test_arinc_rx_rates_and_jitter(dut):
    """100 kbit/s at +-1% with jitter, and the low-speed range from 12 to 14.5 kbit/s on the 12.5k build:
    self-clocking, so the receiver follows the sender."""
    await reset(dut)
    import random
    rng = random.Random(429)
    words = [arinc_bits(rng.getrandbits(8), rng.getrandbits(23)) for _ in range(4)]
    want = [(0xA000,) + arinc_host_words(b) for b in words]
    for bit, build in ((500, 500), (495, 500), (505, 500), (4167, 4000), (3448, 4000)):
        edges = arinc_edges(words, bit=bit, start=3 * build, jitter=lambda k: rng.randint(-8, 8))
        got = await arinc_receive(dut, edges, build)  # the receiver wants 2 bit times of NULL before a word
        assert got == want, f"bit {bit}: {[[hex(x) for x in w] for w in got]}"


@cocotb.test()
async def test_arinc_rx_errors(dut):
    """Bad parity, HI and LO together, a word that stops after 20 bits, and a word straight after it."""
    await reset(dut)
    good = arinc_bits(0o310, 0x123456)
    bad_parity = arinc_bits(0o310, 0x123456, parity_ok=False)
    edges = arinc_edges([bad_parity])
    both = arinc_edges([good], start=edges[-1][0] + 3000)
    both[2 * 9] = (both[2 * 9][0], (1, 1))  # bit 10's pulse on both lines
    short = arinc_edges([good[:20]], start=both[-1][0] + 3000)
    after = arinc_edges([good], start=short[-1][0] + 3000)
    got = await arinc_receive(dut, edges + both + short + after)
    assert [w[0] for w in got] == [0xE001, 0xE002, 0xE003, 0xA000], [[hex(x) for x in w] for w in got]
    assert got[-1][1:] == arinc_host_words(good)


@cocotb.test()
async def test_arinc_loopback_two_contexts(dut):
    """Transmitter and receiver in two contexts, the driver's lines 0 and 1 wired to the receiver's 4 and 5."""
    await reset(dut)
    prog = combined("trial2_arinc_tx.s", "trial2_arinc_rx.s")
    dut.uio_in.value = 0xCF  # the receiver's lines at NULL
    await stop(dut)
    await load_program(dut, prog.words)
    await set_two(dut, prog)
    await start(dut)

    async def wire():
        while True:
            await First(Edge(dut.uio_out), Edge(dut.uio_oe))
            out, oe = int(dut.uio_out.value), int(dut.uio_oe.value)
            v = pad(out, oe, 0, 0) << 4 | pad(out, oe, 1, 0) << 5
            dut.uio_in.value = int(dut.uio_in.value) & ~0x30 | v

    task = cocotb.start_soon(wire())
    host = Host(dut)
    await host.exchange()
    words = [arinc_bits(0o270, 0x5A5A5A), arinc_bits(0o007, 0x1)]
    await host.send(sum((list(arinc_host_words(b)) for b in words), []))
    await host.poll_for(3 * 40 * 500, poll=4000)
    await host.exchange()
    task.cancel()
    await clear_contexts(dut)
    got = [tuple(host.got[k:k + 3]) for k in range(0, len(host.got), 3)]
    assert got == [(0xA000,) + arinc_host_words(b) for b in words], got
