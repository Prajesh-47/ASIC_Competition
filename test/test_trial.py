# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# Blind adaptability trial (docs/adaptability.md). An outside model picked protocols, and each is
# implemented from its specification with no RTL or ISA change. Kept out of the default test run because
# slow protocols take minutes. Run with: make COCOTB_TEST_MODULES=test_trial
#
# Lines change event by event, not cycle by cycle, so DALI's millisecond frames finish in reasonable time.

import cocotb
from cocotb.triggers import Edge, First, Timer
from cocotb.utils import get_sim_time

from test import firmware, load_program, reset, start, stop, write_reg, fifo_read, fifo_write, spi_xfer  # noqa: F401

CYCLE_NS = 20


def cycles_now():
    return get_sim_time("ns") // CYCLE_NS


async def wait_cycles(n):
    if n > 0:
        await Timer(int(n) * CYCLE_NS, "ns")


class Bus:
    """An open-drain line: low if the chip or the test drives it low. Keeps a list of every level change."""

    def __init__(self, dut, line):
        self.dut, self.line, self.ext, self.history = dut, line, 1, []
        self.level = 1
        self.task = cocotb.start_soon(self.watch())

    def own(self):
        oe, out = int(self.dut.uio_oe.value), int(self.dut.uio_out.value)
        return (out >> self.line) & 1 if (oe >> self.line) & 1 else 1

    def update(self):
        level = self.own() & self.ext
        if level != self.level or not self.history:
            self.level = level
            self.history.append((cycles_now(), level))
        self.dut.uio_in.value = 0xFF & ~(1 << self.line) | (level << self.line)

    async def watch(self):
        while True:
            self.update()
            await First(Edge(self.dut.uio_oe), Edge(self.dut.uio_out))

    def drive(self, level):
        self.ext = level
        self.update()

    def close(self):
        self.task.cancel()

    def sample(self, t):
        value = 1
        for when, level in self.history:
            if when > t:
                break
            value = level
        return value

    def falls_after(self, t):
        was, out = 1, []
        for when, level in self.history:
            if when >= t and was == 1 and level == 0:
                out.append(when)
            was = level
        return out


async def fifo_all(dut, limit=64):
    words = []
    for _ in range(limit):
        if (await spi_xfer(dut, [0x12, 0]))[1] & 0x10:
            break
        words.extend(await fifo_read(dut, 1))
    return words


# ------------------------------------------------------------------ DALI
TE = 20833


def dali_phases(bits):
    """Half-bit levels of a Manchester frame, start bit first. A 1 is low then high, and a 0 is high then low."""
    out = []
    for b in [1] + bits:
        out += [0, 1] if b else [1, 0]
    return out


def byte_bits(value, count=8):
    return [(value >> k) & 1 for k in range(count - 1, -1, -1)]


async def dali_send(bus, bits, te=TE):
    for level_ in dali_phases(bits):
        bus.drive(level_)
        await wait_cycles(te)
    bus.drive(1)


def dali_read_forward(bus, first, count, te=TE):
    """Sampled levels at the middle of each half bit."""
    return [bus.sample(round(first + te * (k + 0.5))) for k in range(count)]


async def dali_start(dut, program, **defines):
    await stop(dut)
    await load_program(dut, firmware(program, **defines).words)
    await write_reg(dut, 1, 0x10)
    await start(dut)
    return Bus(dut, 4)


async def controller_exchange(dut, frame, reply_bits=None, te=TE, gap=7):
    """One forward frame. Replies `gap` Te after the frame ends. Returns the bus and when the frame began."""
    bus = await dali_start(dut, "trial/dali_controller.s")
    began = cycles_now()
    await fifo_write(dut, [frame])
    while not bus.falls_after(began):
        await wait_cycles(1000)
    first = bus.falls_after(began)[0]
    await wait_cycles(first + 38 * TE + gap * TE - cycles_now())
    if reply_bits is not None:
        await dali_send(bus, reply_bits, te)
    return bus, first


@cocotb.test()
async def test_dali_forward_frame_and_yes(dut):
    """Exact waveform, 38 Te long, MSB first, then a YES that arrives after the shortest allowed gap."""
    await reset(dut)
    frame = 0xA591
    bus, first = await controller_exchange(dut, frame, byte_bits(0xFF))
    wanted = dali_phases(byte_bits(frame, 16))
    assert dali_read_forward(bus, first, len(wanted)) == wanted, "forward frame waveform is wrong"
    end_of_data = first + len(wanted) * TE
    changes = [t for t, _ in bus.history if first <= t <= first + 38 * TE]
    assert changes[-1] <= end_of_data + 8, "bus should idle through the two stop bits"
    assert abs(wanted.count(0) + wanted.count(1) - 34) == 0
    await wait_cycles(30 * TE)
    assert await fifo_all(dut) == [0x01FF]
    bus.close()


@cocotb.test()
async def test_dali_no_reply_means_no(dut):
    await reset(dut)
    bus, _ = await controller_exchange(dut, 0xFF91, None)
    await wait_cycles(28 * TE)
    assert await fifo_all(dut) == [0x0200]
    bus.close()


@cocotb.test()
async def test_dali_backward_at_rate_limits(dut):
    """The note allows 1200 bit/s +-10%, at the latest allowed gap too."""
    await reset(dut)
    for te, gap, value in ((round(TE * 0.9), 7, 0xA5), (round(TE * 1.1), 7, 0x5A), (TE, 20, 0x00)):
        bus, _ = await controller_exchange(dut, 0x0102, byte_bits(value), te=te, gap=gap)
        await wait_cycles(30 * TE)
        got = await fifo_all(dut)
        assert got == [0x0100 | value], f"te {te} gap {gap}: {[hex(w) for w in got]}"
        bus.close()


@cocotb.test()
async def test_dali_malformed_backward_rejected(dut):
    await reset(dut)
    for bits in (byte_bits(0x55)[:7], byte_bits(0x55) + [1]):
        bus, _ = await controller_exchange(dut, 0x0102, bits)
        await wait_cycles(30 * TE)
        got = await fifo_all(dut)
        assert got == [0x0300], f"{len(bits)} bits: {[hex(w) for w in got]}"
        bus.close()


@cocotb.test()
async def test_dali_collision_detected_and_retried(dut):
    """Another controller pulls the bus low while we've released it. We must stop, wait, and resend intact."""
    await reset(dut)
    bus = await dali_start(dut, "trial/dali_controller.s")
    began = cycles_now()
    frame = 0x0F0F
    await fifo_write(dut, [frame])
    while not bus.falls_after(began):
        await wait_cycles(1000)
    first = bus.falls_after(began)[0]
    # Bit 0 of the frame is a 0, whose first half is high. Pull it low there, as a 1 from a competitor would.
    await wait_cycles(first + 2 * TE + TE // 8 - cycles_now())
    bus.drive(0)
    await wait_cycles(TE)
    bus.drive(1)
    await wait_cycles(5 * TE)
    assert bus.own() == 1, "should have released the bus after a collision"
    await wait_cycles(24 * TE)
    retry = [t for t in bus.falls_after(first + 6 * TE)]
    assert retry, "never retried"
    gap = retry[0] - (first + 3 * TE)
    assert gap >= 22 * TE - 8, f"retried after only {gap / TE:.1f} Te"
    wanted = dali_phases(byte_bits(frame, 16))
    await wait_cycles(retry[0] + 66 * TE - cycles_now())  # frame, then the longest reply wait
    assert dali_read_forward(bus, retry[0], len(wanted)) == wanted, "retried frame is wrong"
    assert await fifo_all(dut) == [0x0400, 0x0200]
    bus.close()


@cocotb.test()
async def test_dali_gear_addressing_and_reply(dut):
    """Short address, group, broadcast and someone else's address, at the rate limits too."""
    await reset(dut)
    for byte, addressed, te in ((5 << 1 | 1, True, TE), (0x80 | 3 << 1 | 1, True, round(TE * 1.1)), (0xFF, True, round(TE * 0.9)),
                                (6 << 1 | 1, False, TE), (0x80 | 4 << 1 | 1, False, TE)):
        bus = await dali_start(dut, "trial/dali_gear.s")
        await wait_cycles(1000)
        frame = byte << 8 | 0x91
        began = cycles_now()
        await dali_send(bus, byte_bits(frame, 16), te)
        await wait_cycles(4 * te)
        ended = cycles_now()
        await wait_cycles(40 * TE)
        got = await fifo_all(dut)
        falls = bus.falls_after(ended)
        assert got[:1] == [frame], f"{frame:#06x}: {[hex(w) for w in got]}"
        assert (0x0100 in got) == addressed
        if addressed:
            assert falls, f"{frame:#06x} got no reply"
            gap = (falls[0] - ended) / TE
            assert 7 <= gap <= 22, f"reply after {gap:.1f} Te"
            assert dali_read_forward(bus, falls[0], 18) == dali_phases(byte_bits(0xFF)), "reply is wrong"
        else:
            assert not falls, f"{frame:#06x} shouldn't get a reply"
        assert began < ended
        bus.close()


# ------------------------------------------------------------------ HDLC (RFC 1662, bit-synchronous)
HDLC_BIT = 50
HDLC_TX, HDLC_RX, HDLC_CLK, HDLC_VALID, HDLC_ERR = 4, 0, 1, 6, 7
FLAG = [0, 1, 1, 1, 1, 1, 1, 0]


def fcs16(data):
    fcs = 0xFFFF
    for byte in data:
        fcs ^= byte
        for _ in range(8):
            fcs = (fcs >> 1) ^ 0x8408 if fcs & 1 else fcs >> 1
    return fcs ^ 0xFFFF


def hdlc_with_fcs(data):
    fcs = fcs16(data)
    return list(data) + [fcs & 0xFF, fcs >> 8]


def lsb_bits(data):
    return [(byte >> k) & 1 for byte in data for k in range(8)]


def hdlc_stuff(bits):
    out, run = [], 0
    for b in bits:
        out.append(b)
        run = run + 1 if b else 0
        if run == 5:
            out.append(0)
            run = 0
    return out


def hdlc_unstuff_frames(bits):
    """Frames between flags, from the raw stream. Written from section 5.2, separately from the firmware."""
    frames, data, run, inside = [], [], 0, False
    for b in bits:
        if b:
            run += 1
            data.append(1)
            continue
        if run == 6:
            body = data[:-7] if len(data) >= 7 else []
            if inside and body:
                frames.append(body)
            data, inside = [], True
        elif run == 5:
            pass  # inserted
        else:
            data.append(0)
        run = 0
    return frames


def bits_to_bytes(bits):
    assert len(bits) % 8 == 0, f"{len(bits)} bits isn't whole bytes"
    return [sum(bits[i + k] << k for k in range(8)) for i in range(0, len(bits), 8)]


async def hdlc_start(dut):
    await stop(dut)
    prog = firmware("trial/hdlc.s")
    await load_program(dut, prog.words)
    await write_reg(dut, 0, 1)
    await write_reg(dut, 4, prog.ctx1 >> 8)
    await write_reg(dut, 5, prog.ctx1 & 0xFF)
    await start(dut)


def hdlc_rx_levels(frame_bits, tail=40):
    """(data, clock) per cycle. The clock rises mid-bit, as a synchronous line would."""
    wave = []
    for b in frame_bits + [1] * tail:
        wave += [(b, 0)] * (HDLC_BIT // 2) + [(b, 1)] * (HDLC_BIT - HDLC_BIT // 2)
    return wave


def hdlc_tx_words(frame):
    words = [len(frame) << 8 | frame[0]]
    rest = frame[1:]
    for i in range(0, len(rest), 2):
        words.append(rest[i] | (rest[i + 1] << 8 if i + 1 < len(rest) else 0))
    return words


async def hdlc_exchange(dut, tx_frames=(), rx_bits=(), extra=400):
    """Sends and receives at once, with a host feeding and draining the FIFOs. Returns TX line, TX enable, words."""
    from test import capture
    words, feed = [], [w for frame in tx_frames for w in hdlc_tx_words(frame)]
    wave = hdlc_rx_levels(list(rx_bits))
    total = max(len(wave), (sum(len(f) for f in tx_frames) * 10 + 60) * HDLC_BIT) + extra

    def bus(t, out, oe):
        data, clk = wave[t] if t < len(wave) else (1, 0)
        return 0xFC | data << HDLC_RX | clk << HDLC_CLK

    done = []

    async def host():
        while not done or feed:
            status = (await spi_xfer(dut, [0x12, 0]))[1]
            if feed and not status & 0x08:
                await fifo_write(dut, [feed.pop(0)])
            if not status & 0x10:
                words.extend(w for w in await fifo_read(dut, 2) if w)  # empty reads as 0, and no word is 0
            if done and status & 0x10 and not feed:
                return

    task = cocotb.start_soon(host())
    trace = await capture(dut, total, bus)
    done.append(True)
    await task
    return trace, words


def hdlc_tx_stream(trace):
    levels = [(t[0] >> HDLC_TX) & 1 for t in trace]
    first = next(i for i in range(1, len(levels)) if levels[i] != levels[i - 1])
    start_at = first % HDLC_BIT
    return [levels[i] for i in range(start_at + HDLC_BIT // 2, len(levels), HDLC_BIT)], levels


def hdlc_rx_decode(words):
    """Split FIFO words into frames: low byte 0x80 means data, and otherwise the word is the status."""
    out, data = [], []
    for w in words:
        if w & 0xFF == 0x80:
            data.append(w >> 8)
        else:
            out.append((data, w))
            data = []
    return out


PPP = [0xFF, 0x03, 0x00, 0x21]


@cocotb.test()
async def test_hdlc_known_answer_transmit(dut):
    """The plan's vector: FCS of ASCII 123456789 is 0x906E, sent 6E 90. Then the whole frame on the wire."""
    await reset(dut)
    await hdlc_start(dut)
    assert fcs16(b"123456789") == 0x906E
    trace, _ = await hdlc_exchange(dut, [list(b"123456789")])
    stream, levels = hdlc_tx_stream(trace)
    frames = hdlc_unstuff_frames(stream)
    assert len(frames) == 1, f"{len(frames)} frames"
    assert bits_to_bytes(frames[0]) == list(b"123456789") + [0x6E, 0x90]
    edges = [i for i in range(1, len(levels)) if levels[i] != levels[i - 1]]
    assert all(abs(((e - edges[0]) + 1) % HDLC_BIT - 1) <= 1 for e in edges), "an edge is off the 1 us grid"
    await stop(dut)
    await write_reg(dut, 0, 0)


@cocotb.test()
async def test_hdlc_transmit_stuffing_and_shared_flag(dut):
    """At least 20 ones in a row, a payload holding the flag pattern, and two frames sharing one flag."""
    await reset(dut)
    await hdlc_start(dut)
    first, second = PPP + [0xFF, 0xFF, 0xFF, 0x7E, 0x7E], PPP + [0x7E, 0x00, 0xFF]
    trace, _ = await hdlc_exchange(dut, [first, second])
    stream, _ = hdlc_tx_stream(trace)
    frames = [bits_to_bytes(f) for f in hdlc_unstuff_frames(stream)]
    assert frames == [hdlc_with_fcs(first), hdlc_with_fcs(second)], f"{frames}"
    text = "".join(map(str, stream))
    closing = "".join(map(str, hdlc_stuff(lsb_bits(hdlc_with_fcs(first))))) + "01111110" + "".join(map(str, hdlc_stuff(lsb_bits(hdlc_with_fcs(second)))))
    assert closing in text, "the frames should share exactly one flag"
    await stop(dut)
    await write_reg(dut, 0, 0)


@cocotb.test()
async def test_hdlc_receive(dut):
    """Good frames with long runs of ones and flag patterns, and shared flags. One word per byte, then status."""
    await reset(dut)
    await hdlc_start(dut)
    one, two = PPP + [0xFF, 0xFF, 0xFF, 0x7E, 0x00], list(b"123456789")
    bits = FLAG * 2 + hdlc_stuff(lsb_bits(hdlc_with_fcs(one))) + FLAG + hdlc_stuff(lsb_bits(hdlc_with_fcs(two))) + FLAG + FLAG
    trace, words = await hdlc_exchange(dut, rx_bits=bits)
    got = hdlc_rx_decode(words)
    assert got == [(hdlc_with_fcs(one), 1), (hdlc_with_fcs(two), 1)], f"{got}"
    assert (trace[-1][0] >> HDLC_VALID) & 1 == 1 and (trace[-1][0] >> HDLC_ERR) & 1 == 0
    await stop(dut)
    await write_reg(dut, 0, 0)


@cocotb.test()
async def test_hdlc_receive_rejects_bad_frames(dut):
    """Each is followed by a good frame, so losing alignment after an error would show up too."""
    await reset(dut)
    good = PPP + [0x55]
    good_bits = hdlc_stuff(lsb_bits(hdlc_with_fcs(good))) + FLAG
    corrupt = hdlc_with_fcs(good)
    corrupt[-1] ^= 0x10
    cases = {
        "wrong FCS": (hdlc_stuff(lsb_bits(corrupt)) + FLAG, 2),
        "under 4 octets": (hdlc_stuff(lsb_bits(hdlc_with_fcs([0x12]))) + FLAG, 3),
        "not whole bytes": (hdlc_stuff(lsb_bits(hdlc_with_fcs(good))[:-3]) + FLAG, 2),
        "more than six ones": (hdlc_stuff(lsb_bits(PPP)) + [1] * 9 + [0] + FLAG, 4),
    }
    for name, (bad, status) in cases.items():
        await hdlc_start(dut)
        trace, words = await hdlc_exchange(dut, rx_bits=FLAG + bad + good_bits + FLAG)
        got = hdlc_rx_decode(words)
        assert [s for _, s in got] == [status, 1], f"{name}: {got}"
        assert got[1][0] == hdlc_with_fcs(good), f"{name}: next frame damaged, {got}"
    await stop(dut)
    await write_reg(dut, 0, 0)


@cocotb.test()
async def test_hdlc_full_duplex(dut):
    """Both directions at the same time, the case that needs two contexts."""
    await reset(dut)
    await hdlc_start(dut)
    out_frame, in_frame = PPP + [0xFF, 0xAA, 0x7E], PPP + [0x01, 0xFF, 0xFF]
    bits = FLAG + hdlc_stuff(lsb_bits(hdlc_with_fcs(in_frame))) + FLAG + FLAG
    trace, words = await hdlc_exchange(dut, [out_frame], bits)
    stream, _ = hdlc_tx_stream(trace)
    assert [bits_to_bytes(f) for f in hdlc_unstuff_frames(stream)] == [hdlc_with_fcs(out_frame)]
    assert hdlc_rx_decode(words) == [(hdlc_with_fcs(in_frame), 1)], f"{[hex(w) for w in words]}"
    await stop(dut)
    await write_reg(dut, 0, 0)


async def hdlc_sustained(dut, half, send_out, send_in):
    """Long frames, past what the FIFOs can buffer, so the SPI host has to keep up throughout."""
    import test as host
    saved, host.HALF = host.HALF, half
    try:
        await hdlc_start(dut)
        out_frame = PPP + [(i * 37) & 0xFF for i in range(60)]
        in_frame = PPP + [(i * 91) & 0xFF for i in range(60)]
        bits = FLAG + hdlc_stuff(lsb_bits(hdlc_with_fcs(in_frame))) + FLAG + FLAG
        trace, words = await hdlc_exchange(dut, [out_frame] if send_out else [], bits if send_in else [])
        stream, _ = hdlc_tx_stream(trace) if send_out else ([], [])
        sent = [bits_to_bytes(f) for f in hdlc_unstuff_frames(stream) if len(f) % 8 == 0]
        got = hdlc_rx_decode(words)
        ok_out = not send_out or sent == [hdlc_with_fcs(out_frame)]
        ok_in = not send_in or got == [(hdlc_with_fcs(in_frame), 1)]
        dut._log.info(f"sustained SCK=core/{2 * half} out={send_out} in={send_in}: tx_ok={ok_out} rx_ok={ok_in}")
        await stop(dut)
        await write_reg(dut, 0, 0)
        return ok_out and ok_in
    finally:
        host.HALF = saved


@cocotb.test()
async def test_hdlc_sustained_one_direction(dut):
    await reset(dut)
    assert await hdlc_sustained(dut, 5, True, False), "transmit alone at core/10"
    assert await hdlc_sustained(dut, 5, False, True), "receive alone at core/10"


@cocotb.test()
async def test_hdlc_sustained_full_duplex(dut):
    """The fastest SPI the spec allows. Slower hosts are measured for the report, not required."""
    await reset(dut)
    at_10 = await hdlc_sustained(dut, 5, True, True)
    dut._log.info(f"full duplex at core/10: {'keeps up' if at_10 else 'falls behind'}")
    assert await hdlc_sustained(dut, 4, True, True), "full duplex at core/8"
