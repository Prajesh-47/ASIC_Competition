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


# The trial firmware, and versions using the 16-bit timer, added after the trial. Both must pass.
DALI_CONTROLLERS = ("trial/dali_controller.s", "dali_controller.s")
DALI_GEARS = ("trial/dali_gear.s", "dali_gear.s")


async def controller_exchange(dut, frame, reply_bits=None, te=TE, gap=7, program="trial/dali_controller.s"):
    """One forward frame. Replies `gap` Te after the frame ends. Returns the bus and when the frame began."""
    bus = await dali_start(dut, program)
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
    for program in DALI_CONTROLLERS:
        frame = 0xA591
        bus, first = await controller_exchange(dut, frame, byte_bits(0xFF), program=program)
        wanted = dali_phases(byte_bits(frame, 16))
        assert dali_read_forward(bus, first, len(wanted)) == wanted, f"{program}: forward frame waveform is wrong"
        end_of_data = first + len(wanted) * TE
        changes = [t for t, _ in bus.history if first <= t <= first + 38 * TE]
        assert changes[-1] <= end_of_data + 8, f"{program}: bus should idle through the two stop bits"
        assert abs(wanted.count(0) + wanted.count(1) - 34) == 0
        if program == "dali_controller.s":  # one tick per half bit: every edge exactly on a whole Te
            assert all((t - first) % TE == 0 for t in changes), f"edges off the Te grid: {[(t - first) % TE for t in changes]}"
        await wait_cycles(30 * TE)
        assert await fifo_all(dut) == [0x01FF], program
        bus.close()


@cocotb.test()
async def test_dali_no_reply_means_no(dut):
    await reset(dut)
    for program in DALI_CONTROLLERS:
        bus, _ = await controller_exchange(dut, 0xFF91, None, program=program)
        await wait_cycles(28 * TE)
        assert await fifo_all(dut) == [0x0200], program
        bus.close()


@cocotb.test()
async def test_dali_backward_at_rate_limits(dut):
    """The note allows 1200 bit/s +-10%, at the latest allowed gap too."""
    await reset(dut)
    for program in DALI_CONTROLLERS:
        for te, gap, value in ((round(TE * 0.9), 7, 0xA5), (round(TE * 1.1), 7, 0x5A), (TE, 20, 0x00)):
            bus, _ = await controller_exchange(dut, 0x0102, byte_bits(value), te=te, gap=gap, program=program)
            await wait_cycles(30 * TE)
            got = await fifo_all(dut)
            assert got == [0x0100 | value], f"{program} te {te} gap {gap}: {[hex(w) for w in got]}"
            bus.close()


@cocotb.test()
async def test_dali_malformed_backward_rejected(dut):
    await reset(dut)
    for program in DALI_CONTROLLERS:
        for bits in (byte_bits(0x55)[:7], byte_bits(0x55) + [1]):
            bus, _ = await controller_exchange(dut, 0x0102, bits, program=program)
            await wait_cycles(30 * TE)
            got = await fifo_all(dut)
            assert got == [0x0300], f"{program} {len(bits)} bits: {[hex(w) for w in got]}"
            bus.close()


@cocotb.test()
async def test_dali_collision_detected_and_retried(dut):
    """Another controller pulls the bus low while we've released it. We must stop, wait, and resend intact."""
    await reset(dut)
    for program in DALI_CONTROLLERS:
        bus = await dali_start(dut, program)
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
        assert retry, f"{program}: never retried"
        gap = retry[0] - (first + 3 * TE)
        assert gap >= 22 * TE - 8, f"{program}: retried after only {gap / TE:.1f} Te"
        wanted = dali_phases(byte_bits(frame, 16))
        await wait_cycles(retry[0] + 66 * TE - cycles_now())  # frame, then the longest reply wait
        assert dali_read_forward(bus, retry[0], len(wanted)) == wanted, f"{program}: retried frame is wrong"
        assert await fifo_all(dut) == [0x0400, 0x0200], program
        bus.close()


@cocotb.test()
async def test_dali_gear_addressing_and_reply(dut):
    """Short address, group, broadcast and someone else's address, at the rate limits too."""
    await reset(dut)
    for program in DALI_GEARS:
        for byte, addressed, te in ((5 << 1 | 1, True, TE), (0x80 | 3 << 1 | 1, True, round(TE * 1.1)), (0xFF, True, round(TE * 0.9)),
                                    (6 << 1 | 1, False, TE), (0x80 | 4 << 1 | 1, False, TE)):
            bus = await dali_start(dut, program)
            await wait_cycles(1000)
            frame = byte << 8 | 0x91
            began = cycles_now()
            await dali_send(bus, byte_bits(frame, 16), te)
            await wait_cycles(4 * te)
            ended = cycles_now()
            await wait_cycles(40 * TE)
            got = await fifo_all(dut)
            falls = bus.falls_after(ended)
            assert got[:1] == [frame], f"{program} {frame:#06x}: {[hex(w) for w in got]}"
            assert (0x0100 in got) == addressed
            if addressed:
                assert falls, f"{program} {frame:#06x} got no reply"
                gap = (falls[0] - ended) / TE
                assert 7 <= gap <= 22, f"{program}: reply after {gap:.1f} Te"
                assert dali_read_forward(bus, falls[0], 18) == dali_phases(byte_bits(0xFF)), f"{program}: reply is wrong"
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


# ------------------------------------------------------------------ S/PDIF (IEC 60958, 48 kHz stereo)
# Preamble states after a 0, from the plan and checked against the AES3 table.
PRE = {"M": [1, 1, 1, 0, 0, 0, 1, 0], "W": [1, 1, 1, 0, 0, 1, 0, 0], "B": [1, 1, 1, 0, 1, 0, 0, 0]}
STATES_PER_SECOND = 6_144_000
FRAME_CYCLES = 50_000_000 / 48_000


def spdif_payload(n, audio=None):
    """Slots 4-31 as bits, oldest first: 24 audio, V, U, C, even parity. The parity covers slots 4-30."""
    sample = (n * 0x1B5A7 + 0x35) & 0xFFFFFF if audio is None else audio
    bits = [(sample >> k) & 1 for k in range(24)] + [n % 3 == 0, n % 5 == 0, n % 7 == 0]
    bits = [int(b) for b in bits]
    return bits + [sum(bits) & 1]


def spdif_states(subframes, first=0, invert=False):
    """Biphase-mark states for subframes of (preamble name, 28 bits). Written from the coding rules."""
    out, level = [], 0
    for name, bits in subframes:
        pre = PRE[name] if level == 0 else [1 - s for s in PRE[name]]
        out += pre
        level = out[-1]
        for b in bits:
            level = 1 - level
            out.append(level)
            if b:
                level = 1 - level
            out.append(level)
    return [1 - s for s in out] if invert else out


def spdif_stream(count, start=0):
    """count subframes starting at index start in a block of 384: B, W, M, W, ..."""
    out = []
    for i in range(start, start + count):
        name = "B" if i % 384 == 0 else ("W" if i % 2 else "M")
        out.append((name, spdif_payload(i)))
    return out


def spdif_wave(states, rate=1.0, lead=120):
    """Level per clock cycle, with the state timing rounded to the 20 ns grid."""
    per_state = 50_000_000 / STATES_PER_SECOND / rate
    total = round(len(states) * per_state)
    wave, index = [states[0] ^ 1] * lead, 0
    for cycle in range(total):
        while (index + 1) * per_state <= cycle and index + 1 < len(states):
            index += 1
        wave.append(states[index])
    return wave


def spdif_decode_words(words):
    """FIFO words to (preamble, 28 bits): a bit is 1 when its sample equals the one before."""
    out = []
    for i in range(0, len(words) - 1, 2):
        first, second = words[i], words[i + 1]
        states = [(first >> k) & 1 for k in range(15, 11, -1)]
        samples = [(first >> k) & 1 for k in range(11, -1, -1)] + [(second >> k) & 1 for k in range(15, -1, -1)]
        code = (states[0] << 3 | states[1] << 2 | states[2] << 1 | states[3])
        name = {0b0010: "M", 0b1101: "M", 0b0100: "W", 0b1011: "W", 0b1000: "B", 0b0111: "B"}.get(code, "?")
        prev, bits = states[3], []
        for s in samples:
            bits.append(int(s == prev))
            prev = s
        out.append((name, bits))
    return out


async def spdif_receive(dut, wave, half=4, rate=1.0):
    """Plays a stream into RX with a host at the fastest SPI allowed. Returns words and the trace.
    Pairs come every 520 cycles, and reading one takes 320. So after the first pair the host reads on a timer,
    as a real host synced to the stream would. Polling as well would not fit."""
    from test import capture
    import test as host
    saved, host.HALF = host.HALF, half
    words, done = [], []
    period = FRAME_CYCLES / 2 / rate

    async def reader():
        while not done:
            if not (await spi_xfer(dut, [0x12, 0]))[1] & 0x10:
                break
        await wait_cycles(280)
        began, pairs = cycles_now(), 0
        while not done:
            words.extend(await fifo_read(dut, 2))
            pairs += 1
            await wait_cycles(began + pairs * period - cycles_now())
        # What's left once the stream has stopped
        for _ in range(6):
            if (await spi_xfer(dut, [0x12, 0]))[1] & 0x10:
                break
            words.extend(await fifo_read(dut, 1))

    task = cocotb.start_soon(reader())
    trace = await capture(dut, len(wave) + 200, lambda t, o, e: 0xFE | (wave[t] if t < len(wave) else wave[-1]))
    done.append(True)
    await task
    host.HALF = saved
    return words, trace


async def spdif_rx_start(dut):
    await stop(dut)
    await load_program(dut, firmware("trial/spdif_rx.s").words)
    await start(dut)


async def spdif_rx_run(dut, subframes, rate=1.0, invert=False):
    await spdif_rx_start(dut)
    wave = spdif_wave(spdif_states(subframes, invert=invert), rate)
    words, trace = await spdif_receive(dut, wave, rate=rate)
    await stop(dut)
    return words, trace


def spdif_locked_tail(decoded, sent):
    """The receiver may join late, so find where its output lines up with what was sent."""
    if not decoded:
        return None
    for skip in range(0, min(12, len(sent))):
        if decoded[0] == sent[skip]:
            return skip
    return None


@cocotb.test()
async def test_spdif_receive_decodes_two_frames_worth(dut):
    """Decode of every field, including at the block start."""
    await reset(dut)
    sent = spdif_stream(24)
    words, _ = await spdif_rx_run(dut, sent)
    got = spdif_decode_words(words)
    skip = spdif_locked_tail(got, sent)
    assert skip is not None, f"never lined up: {got[:2]}"
    n = min(len(got), len(sent) - skip)
    assert n >= 16, f"only {n} subframes"
    assert got[:n] == sent[skip:skip + n], f"{sum(a != b for a, b in zip(got, sent[skip:]))} subframes wrong of {n}"


@cocotb.test()
async def test_spdif_receive_block_of_192_frames(dut):
    """More than a whole block: B must appear exactly every 384 subframes, and no data may be lost."""
    await reset(dut)
    sent = spdif_stream(384 + 40)
    words, trace = await spdif_rx_run(dut, sent)
    got = spdif_decode_words(words)
    skip = spdif_locked_tail(got, sent)
    assert skip is not None
    n = min(len(got), len(sent) - skip)
    wrong = sum(a != b for a, b in zip(got[:n], sent[skip:skip + n]))
    dut._log.info(f"block: {n} subframes decoded, {wrong} wrong, joined after {skip}")
    assert n >= 384 and wrong == 0, f"{n} subframes, {wrong} wrong"
    clean = trace[3000:-800]  # the receiver rightly loses sync once the stream stops at the end
    assert not any((t[0] >> 5) & 1 for t in clean), "ERR set on a clean stream"
    assert not any((t[0] >> 6) & 1 for t in clean), "PERR set on a clean stream"
    assert any((t[0] >> 7) & 1 for t in trace), "SYNC never showed the block start"


@cocotb.test()
async def test_spdif_receive_polarity_inverted(dut):
    await reset(dut)
    sent = spdif_stream(24)
    words, _ = await spdif_rx_run(dut, sent, invert=True)
    got = spdif_decode_words(words)
    skip = spdif_locked_tail(got, sent)
    assert skip is not None, "no lock on an inverted stream"
    n = min(len(got), len(sent) - skip)
    assert n >= 16 and got[:n] == sent[skip:skip + n]


@cocotb.test()
async def test_spdif_receive_clock_drift(dut):
    """The sender's clock off by each amount. Records the widest that holds, and needs IEC's 0.1% at least."""
    await reset(dut)
    results = {}
    for offset in (-0.03, -0.02, -0.01, -0.001, 0.001, 0.01, 0.02, 0.03):
        sent = spdif_stream(40)
        words, _ = await spdif_rx_run(dut, sent, rate=1 + offset)
        got = spdif_decode_words(words)
        skip = spdif_locked_tail(got, sent)
        n = 0 if skip is None else min(len(got), len(sent) - skip)
        results[offset] = skip is not None and n >= 30 and got[:n] == sent[skip:skip + n]
    dut._log.info(f"drift tolerance: {results}")
    assert results[-0.001] and results[0.001], f"{results}"


@cocotb.test()
async def test_spdif_receive_flags_parity_and_preamble_errors(dut):
    """One audio bit flipped with parity left alone, then one state of an M preamble changed."""
    await reset(dut)
    sent = spdif_stream(24)
    name, bits = sent[12]
    sent[12] = (name, [bits[0] ^ 1] + bits[1:])
    _, trace = await spdif_rx_run(dut, sent)
    wave_at = [i for i, t in enumerate(trace) if (t[0] >> 6) & 1]
    assert wave_at, "parity error wasn't flagged"
    frame = len(wave_at) / (FRAME_CYCLES / 2)
    assert frame < 2, f"PERR stayed high for {frame:.1f} subframes"

    sent = spdif_stream(24)
    states = spdif_states(sent)
    m_at = 32 * 2 * 8  # the 9th subframe is an M
    assert sent[8][0] == "M"
    states[m_at + 5] ^= 1
    wave = spdif_wave(states)
    await spdif_rx_start(dut)
    words, trace = await spdif_receive(dut, wave)
    await stop(dut)
    assert any((t[0] >> 5) & 1 for t in trace), "a damaged M preamble wasn't noticed"


def spdif_read_line(levels):
    """States from a recorded line, using edges only, as an independent receiver would."""
    edges = [i for i in range(1, len(levels)) if levels[i] != levels[i - 1]]
    runs = [b - a for a, b in zip(edges, edges[1:])]
    states = []
    level = levels[edges[0]]
    for run in runs:
        count = 1 if run <= 12 else 2 if run <= 20 else 3
        states += [level] * count
        level = 1 - level
    return states, edges, runs


def spdif_split(states):
    """Finds preambles by their run of three, and returns (name, 28 bits, last state before)."""
    out, i = [], 0
    while i + 3 <= len(states) and not (states[i] == states[i + 1] == states[i + 2]):
        i += 1
    while i + 64 <= len(states):
        pre = states[i:i + 8]
        norm = pre if pre[0] == 1 else [1 - s for s in pre]
        name = next((k for k, v in PRE.items() if v == norm), "?")
        bits = []
        for slot in range(28):
            a, b = states[i + 8 + 2 * slot], states[i + 9 + 2 * slot]
            bits.append(int(a != b))
        out.append((name, bits))
        i += 64
    return out


async def spdif_transmit(dut, subframes_count):
    from test import capture
    import test as host
    saved, host.HALF = host.HALF, 4
    feed = []
    payloads = []
    for i in range(subframes_count):
        sent = spdif_payload(i)
        payloads.append(sent)
        word1 = sum(b << k for k, b in enumerate(sent[:12]))
        word2 = sum(b << k for k, b in enumerate(sent[12:27]))
        feed += [word1, word2]
    await stop(dut)
    await load_program(dut, firmware("trial/spdif_tx.s").words)
    await fifo_write(dut, feed[:4])
    fed = 4
    done = []

    async def writer():
        # Polling and writing one word takes 320 cycles, and only 260 are available. So write pairs in one
        # transaction, on a timer, as a timed host would.
        nonlocal fed
        began, pairs = cycles_now(), 0
        while fed < len(feed) and not done:
            pairs += 1
            await wait_cycles(began + pairs * FRAME_CYCLES / 2 - cycles_now())
            await fifo_write(dut, feed[fed:fed + 2])
            fed += 2

    await start(dut)
    task = cocotb.start_soon(writer())
    trace = await capture(dut, round(subframes_count * FRAME_CYCLES / 2), None)
    done.append(True)
    await task
    host.HALF = saved
    await stop(dut)
    return payloads, trace


@cocotb.test()
async def test_spdif_transmit_known_preambles_and_block(dut):
    """A whole block, read back by an edge-only decoder: preambles, data, parity, and the rate."""
    await reset(dut)
    count = 384 + 20
    payloads, trace = await spdif_transmit(dut, count)
    levels = [(t[0] >> 4) & 1 for t in trace]
    assert not any((t[0] >> 5) & 1 for t in trace[:-200]), "the host fell behind"
    states, edges, runs = spdif_read_line(levels)
    khz = (len(states) / 128) / ((edges[-1] - edges[0]) / 50e6) / 1000
    dut._log.info(f"frame rate {khz:.3f} kHz over {len(states) / 128:.1f} frames. Gaps seen, in cycles: {sorted(set(runs))}")
    got = spdif_split(states)
    assert got[0][0] == "B", f"first preamble is {got[0][0]}"
    names = [g[0] for g in got]
    wanted = ["B" if i % 384 == 0 else ("W" if i % 2 else "M") for i in range(len(names))]
    assert names == wanted, f"preamble order breaks at {next(i for i, (a, b) in enumerate(zip(names, wanted)) if a != b)}"
    n = min(len(got), len(payloads)) - 1
    wrong = [i for i in range(n) if got[i][1] != payloads[i]]
    assert not wrong, f"{len(wrong)} of {n} subframes wrong, first {wrong[0]}: {got[wrong[0]][1]} vs {payloads[wrong[0]]}"

    assert abs(khz - 48) / 48 < 0.001, f"{khz:.3f} kHz is outside IEC's 0.1% tolerance"


# ------------------------------------------------------------------ SD, native bus mode
def crc7(data):
    crc = 0
    for byte in data:
        for k in range(7, -1, -1):
            top = ((crc >> 6) & 1) ^ ((byte >> k) & 1)
            crc = (crc << 1) & 0x7F
            if top:
                crc ^= 0x09
    return crc


def crc16_bits(bits):
    """CRC-16 of one data line: x^16 + x^12 + x^5 + 1, from zero."""
    crc = 0
    for b in bits:
        top = ((crc >> 15) & 1) ^ b
        crc = (crc << 1) & 0xFFFF
        if top:
            crc ^= 0x1021
    return crc


def block_nibbles(data):
    return [n for byte in data for n in (byte >> 4, byte & 15)]


def line_crcs(nibbles):
    """One CRC per line, indexed by DAT number."""
    return [crc16_bits([(n >> line) & 1 for n in nibbles]) for line in range(4)]


def crc_nibbles(crcs):
    return [sum(((crcs[line] >> k) & 1) << line for line in range(4)) for k in range(15, -1, -1)]


class SdCard:
    """Behaviour from the Physical Layer Simplified Specification: commands sampled on the rising edge,
    response and read data changing on the falling edge."""

    def __init__(self, block=None):
        self.block = block or [(i * 7 + 3) & 0xFF for i in range(512)]
        self.commands, self.raw, self.written, self.write_status = [], [], None, None
        self.shift, self.response, self.dat_queue = [], [], []
        self.cmd, self.dat = 1, 0xF
        self.sck, self.wait = 0, 0
        self.silent = set()
        self.bad_crc7 = False
        self.flip_read = None
        self.stuck_low = None
        self.collect, self.after_response = None, None
        self.clock_edges = []
        self.acmd = False

    def reply(self, index, payload, crc=True):
        bytes_ = [index & 0x3F] + payload
        code = crc7(bytes_) if crc else 0x7F
        if self.bad_crc7:
            code ^= 1
        whole = bytes_ + [code << 1 | 1]
        return [(b >> k) & 1 for b in whole for k in range(7, -1, -1)]

    def handle(self, frame):
        data = [sum(frame[i + k] << (7 - k) for k in range(8)) for i in range(0, 48, 8)]
        self.raw.append(data)
        index = data[0] & 0x3F
        assert data[0] & 0xC0 == 0x40, f"bad start or direction bits: {data}"
        assert frame[-1] == 1, "no end bit"
        assert crc7(data[:5]) == data[5] >> 1, f"host sent a wrong CRC-7: {[hex(b) for b in data]}"
        self.commands.append(("A" if self.acmd else "") + str(index))
        was_app, self.acmd = self.acmd, index == 55
        argument = data[1:5]
        if index in self.silent or index == 0:
            return
        status = [0, 0, 1, 0x20 if self.acmd else 0]
        if index == 8:
            self.response = self.reply(8, argument)
        elif index == 55:
            self.response = self.reply(55, status)
        elif index == 41 and was_app:
            self.response = self.reply(0x3F, [0xC0, 0xFF, 0x80, 0x00], crc=False)
        elif index == 2:
            cid = [(i * 11 + 1) & 0xFF for i in range(15)]
            whole = [0x3F] + cid + [crc7(cid) << 1 | 1]
            self.response = [(b >> k) & 1 for b in whole for k in range(7, -1, -1)]
        elif index == 3:
            self.response = self.reply(3, [0x12, 0x34, 0x05, 0x00])
        elif index == 7:
            self.response = self.reply(7, status)
        elif index == 17:
            self.response = self.reply(17, status)
            self.after_response = "read"
        elif index == 24:
            self.response = self.reply(24, status)
            self.after_response = "write"
        self.response = [1, 1] + self.response  # NCR is at least 2 clocks

    def read_sequence(self):
        nibbles = block_nibbles(self.block)
        tail = crc_nibbles(line_crcs(nibbles))
        out = [0xF] * 3 + [0] + nibbles + tail + [0xF]
        if self.flip_read is not None:
            out[4 + self.flip_read] ^= 1
        return out

    def step(self, t, out, oe):
        sck = (out >> 4) & 1 if (oe >> 4) & 1 else 0
        host_cmd = (out >> 5) & 1 if (oe >> 5) & 1 else 1
        host_dat = 0
        for line in range(4):
            host_dat |= ((out >> line) & 1 if (oe >> line) & 1 else 1) << line
        hosts_dat = oe & 0xF
        if self.stuck_low is not None:
            host_dat &= ~(1 << self.stuck_low)

        if sck and not self.sck:
            self.clock_edges.append(t)
            if self.collect is not None:
                self.collect.append(host_dat)
                self.check_write()
            elif not self.response and (oe >> 5) & 1:
                if self.shift or host_cmd == 0:
                    self.shift.append(host_cmd)
                    if len(self.shift) == 48:
                        frame, self.shift = self.shift, []
                        self.handle(frame)
        if not sck and self.sck:
            if self.response:
                self.cmd = self.response.pop(0)
                if not self.response:
                    self.finish_response = True
            else:
                self.cmd = 1
                if self.after_response == "read":
                    self.dat_queue = self.read_sequence()
                    self.after_response = None
                elif self.after_response == "write":
                    self.collect = []
                    self.after_response = None
            self.dat = self.dat_queue.pop(0) if self.dat_queue else 0xF
        self.sck = sck

        dat = (self.dat & 0xF & ~hosts_dat) | (host_dat & hosts_dat)
        if self.stuck_low is not None:
            dat &= ~(1 << self.stuck_low)
        cmd = host_cmd if (oe >> 5) & 1 else self.cmd
        return 0xD0 | dat | cmd << 5 | (sck << 4)

    def check_write(self):
        seen = self.collect
        started = next((i for i, v in enumerate(seen) if v == 0), None)
        if started is None:
            if len(seen) > 40:
                self.collect = None
            return
        body = seen[started + 1:]
        if len(body) < 1024 + 16 + 1:
            return
        nibbles = body[:1024]
        tail = body[1024:1040]
        self.written = [nibbles[i] << 4 | nibbles[i + 1] for i in range(0, 1024, 2)]
        good = crc_nibbles(line_crcs(nibbles)) == tail and body[1040] == 0xF
        self.write_status = 0b010 if good else 0b101
        token = [0, (self.write_status >> 2) & 1, (self.write_status >> 1) & 1, self.write_status & 1, 1]
        self.dat_queue = [0xF, 0xF] + [0xE | b for b in token] + [0xE] * 8
        self.collect = None


async def sd_run(dut, card, script, cycles=120_000, half=4):
    """Runs (op, command bytes, then-word, data words) entries. Returns everything the FIFO gave."""
    from test import capture
    import test as host
    saved, host.HALF = host.HALF, half
    words, done = [], []
    feed = []
    for typ, cmd, then, data in script:
        feed += [typ, cmd[0] << 8 | cmd[1], cmd[2] << 8 | cmd[3], cmd[4] << 8]
        if then is not None:
            feed.append(then)
        feed += data

    async def driver():
        while feed or not done:
            status = (await spi_xfer(dut, [0x12, 0]))[1]
            if feed and not status & 0x08:
                await fifo_write(dut, [feed.pop(0)])
            if not status & 0x10:
                words.extend(await fifo_read(dut, 1))
            if done:
                break

    task = cocotb.start_soon(driver())
    await capture(dut, cycles, card.step)
    done.append(True)
    await task
    for _ in range(8):
        if (await spi_xfer(dut, [0x12, 0]))[1] & 0x10:
            break
        words.extend(await fifo_read(dut, 1))
    host.HALF = saved
    return words


async def sd_start(dut, half=20):
    await stop(dut)
    await load_program(dut, firmware("trial/sd_host.s", HALF=half).words)
    await start(dut)


def cmd_bytes(index, arg=0):
    return [0x40 | index, (arg >> 24) & 255, (arg >> 16) & 255, (arg >> 8) & 255, arg & 255]


@cocotb.test()
async def test_sd_initialization_and_responses(dut):
    """The plan's vector, CMD0 = 40 00 00 00 00 95 on the wire, and then R7, R1, R3, R2 and R6."""
    await reset(dut)
    await sd_start(dut)
    card = SdCard()
    words = await sd_run(dut, card, [
        (0, cmd_bytes(0), None, []),
        (1, cmd_bytes(8, 0x1AA), 0, []),
        (1, cmd_bytes(55), 0, []),
        (2, cmd_bytes(41, 0x40FF8000), 0, []),
        (3, cmd_bytes(2), 0, []),
        (1, cmd_bytes(3), 0, []),
    ], cycles=60_000)
    assert card.raw[0] == [0x40, 0, 0, 0, 0, 0x95], f"CMD0 on the wire: {[hex(b) for b in card.raw[0]]}"
    assert card.commands == ["0", "8", "55", "A41", "2", "3"], f"{card.commands}"
    # R7, R1, R3, then R2, then R6: 3 words and a status each, and 9 words and a status for R2
    assert len(words) == 4 + 4 + 4 + 10 + 4, f"{[hex(w) for w in words]}"
    assert words[3] == words[7] == words[11] == words[21] == words[25] == 0xA000, f"{[hex(w) for w in words]}"
    assert (words[0] >> 8) & 0x3F == 8 and words[1] == 0x0000 | 0x0001 and words[2] >> 8 == 0xAA, f"R7 {[hex(w) for w in words[:3]]}"
    assert (words[8] >> 8) == 0x3F and (words[8] & 0xFF) == 0xC0, f"R3 {[hex(w) for w in words[8:11]]}"
    assert words[12] >> 8 == 0x3F, f"R2 {hex(words[12])}"


def sd_block_from_words(words):
    """256 data words and 4 CRC words, as sampled: DAT3..DAT0 per nibble, oldest highest."""
    nibbles = [(w >> k) & 15 for w in words[:256] for k in (12, 8, 4, 0)]
    tail = [(w >> k) & 15 for w in words[256:260] for k in (12, 8, 4, 0)]
    return nibbles, tail


def sd_clock_periods(edges):
    return [b - a for a, b in zip(edges, edges[1:])]


@cocotb.test()
async def test_sd_read_block_four_bits(dut):
    """CMD17 and a whole 512-byte block on all four lines, with CRC-16 per line. Measures the real clock."""
    await reset(dut)
    await sd_start(dut)
    card = SdCard()
    words = await sd_run(dut, card, [(1, cmd_bytes(17, 0), 1, [])], cycles=150_000)
    assert words[3] == 0xA000, f"{[hex(w) for w in words[:5]]}"
    data = words[4:264]
    nibbles, tail = sd_block_from_words(data)
    wanted = block_nibbles(card.block)
    assert nibbles == wanted, f"first difference at nibble {next(i for i, (a, b) in enumerate(zip(nibbles, wanted)) if a != b)}"
    assert tail == crc_nibbles(line_crcs(nibbles)), "CRC-16 doesn't match"
    assert words[264] == 0xA500, f"{hex(words[264])}"
    periods = sd_clock_periods(card.clock_edges)
    fastest = min(periods)
    dut._log.info(f"SD read: fastest clock {50 / fastest:.2f} MHz, required 25. Whole block took {card.clock_edges[-1] - card.clock_edges[0]} cycles")


@cocotb.test()
async def test_sd_write_block_four_bits(dut):
    """CMD24 and a whole block. The card model checks each line's CRC-16 and sends its status token."""
    await reset(dut)
    await sd_start(dut)
    card = SdCard()
    block = [(i * 13 + 5) & 0xFF for i in range(512)]
    nibbles = block_nibbles(block)
    tail = crc_nibbles(line_crcs(nibbles))
    data = [block[i] << 8 | block[i + 1] for i in range(0, 512, 2)]
    data += [tail[i] << 12 | tail[i + 1] << 8 | tail[i + 2] << 4 | tail[i + 3] for i in range(0, 16, 4)]
    words = await sd_run(dut, card, [(1, cmd_bytes(24, 0), 2, data)], cycles=180_000)
    assert card.written == block, "the card received different data"
    assert card.write_status == 0b010
    assert 0xA502 in words, f"{[hex(w) for w in words]}"
    periods = sd_clock_periods(card.clock_edges)
    dut._log.info(f"SD write: fastest clock {50 / min(periods):.2f} MHz, required 25")


@cocotb.test()
async def test_sd_detects_corruption_and_recovers_from_silence(dut):
    await reset(dut)
    await sd_start(dut)
    card = SdCard()
    card.bad_crc7 = True
    words = await sd_run(dut, card, [(1, cmd_bytes(8, 0x1AA), 0, [])], cycles=12_000)
    assert 0xC700 in words, f"a bad response CRC wasn't noticed: {[hex(w) for w in words]}"

    await sd_start(dut)
    card = SdCard()
    card.silent = {8}
    words = await sd_run(dut, card, [(1, cmd_bytes(8, 0x1AA), None, []), (1, cmd_bytes(55), 0, [])], cycles=30_000)
    assert words[0] == 0xEE00, f"no timeout: {[hex(w) for w in words]}"
    assert 0xA000 in words, f"didn't recover after the timeout: {[hex(w) for w in words]}"

    await sd_start(dut)
    card = SdCard()
    card.flip_read = 100
    words = await sd_run(dut, card, [(1, cmd_bytes(17, 0), 1, [])], cycles=150_000)
    nibbles, tail = sd_block_from_words(words[4:264])
    assert tail != crc_nibbles(line_crcs(nibbles)), "CRC-16 should catch a flipped data bit"


@cocotb.test()
async def test_sd_dat_line_fault(dut):
    """DAT2 stuck low during a transfer, in each direction."""
    await reset(dut)
    await sd_start(dut)
    card = SdCard()
    card.stuck_low = 2
    words = await sd_run(dut, card, [(1, cmd_bytes(17, 0), 1, [])], cycles=150_000)
    nibbles, tail = sd_block_from_words(words[4:264])
    assert tail != crc_nibbles(line_crcs(nibbles)) or nibbles != block_nibbles(card.block), "the fault went unnoticed"

    await sd_start(dut)
    card = SdCard()
    card.stuck_low = 2
    block = [0xFF] * 512
    nibbles = block_nibbles(block)
    tail = crc_nibbles(line_crcs(nibbles))
    data = [0xFFFF] * 256 + [tail[i] << 12 | tail[i + 1] << 8 | tail[i + 2] << 4 | tail[i + 3] for i in range(0, 16, 4)]
    words = await sd_run(dut, card, [(1, cmd_bytes(24, 0), 2, data)], cycles=180_000)
    assert card.write_status == 0b101, f"card status {card.write_status}"
    assert 0xA505 in words, f"{[hex(w) for w in words]}"


# ------------------------------------------------------------------ 100BASE-TX, the expected limit
CODE_4B5B = {0: 0x1E, 1: 0x09, 2: 0x14, 3: 0x15, 4: 0x0A, 5: 0x0B, 6: 0x0E, 7: 0x0F,
             8: 0x12, 9: 0x13, 10: 0x16, 11: 0x17, 12: 0x1A, 13: 0x1B, 14: 0x1C, 15: 0x1D}
J, K, T, R, IDLE = 0x18, 0x11, 0x0D, 0x07, 0x1F


def group_bits(group):
    return [(group >> k) & 1 for k in range(4, -1, -1)]


async def eth_tx_run(dut, nibbles, bit):
    """Returns descrambled line bits, or None if the line can't be read as MLT-3."""
    from test import capture
    await stop(dut)
    await load_program(dut, firmware("trial/eth_tx.s", BIT=bit).words)
    await start(dut)
    await wait_cycles(30 * bit)
    words = [nibbles[i] << 12 | nibbles[i + 1] << 8 | nibbles[i + 2] << 4 for i in range(0, len(nibbles), 3)] + [1]
    feed = list(words)
    done = []

    async def writer():
        while feed and not done:
            if not (await spi_xfer(dut, [0x12, 0]))[1] & 0x08:
                await fifo_write(dut, [feed.pop(0)])

    task = cocotb.start_soon(writer())
    trace = await capture(dut, (len(nibbles) * 5 + 200) * bit, None)
    done.append(True)
    await task
    await stop(dut)
    return trace


def mlt3_levels(trace):
    return [((t[0] >> 4) & 1) - ((t[0] >> 5) & 1) for t in trace]


def mlt3_decode(levels, bit):
    """Symbols sampled at each bit time. A change of level is a 1. Also checks that levels step 0,+1,0,-1."""
    edges = [i for i in range(1, len(levels)) if levels[i] != levels[i - 1]]
    assert edges, "the line never changed"
    start_at = edges[0] % bit
    samples = [levels[i] for i in range(start_at + bit // 2, len(levels), bit)]
    order = {(0, 1): 1, (1, 0): 1, (0, -1): 1, (-1, 0): 1}
    bits = []
    for a, b in zip(samples, samples[1:]):
        if a != b:
            assert (a, b) in order, f"MLT-3 jumped from {a} to {b}"
        bits.append(int(a != b))
    return bits


def descramble(line):
    """Idle is all ones, so the scrambler can be read from it, and then it follows s[n] = s[n-11] ^ s[n-9]."""
    stream = [1 - b for b in line[:11]]
    for i in range(11, len(line)):
        stream.append(stream[i - 11] ^ stream[i - 9])
    return [a ^ b for a, b in zip(line, stream)]


def eth_groups_after_jk(bits):
    text = "".join(map(str, bits))
    at = text.find("1100010001")
    assert at >= 0, "no J/K found"
    body = bits[at + 10:]
    return [sum(b << (4 - k) for k, b in enumerate(body[i:i + 5])) for i in range(0, len(body) - 4, 5)], bits[:at]


@cocotb.test()
async def test_eth_transmit_whole_chain_and_fastest_rate(dut):
    """Everything below 125 MBd: 4B/5B, J/K, T/R, idle, the scrambler over a long run, MLT-3. Finds the top rate."""
    await reset(dut)
    nibbles = (list(range(16)) * 4)[:63]
    fastest = None
    for bit in (60, 40, 30, 26, 24, 22, 20, 18, 16, 14):
        trace = await eth_tx_run(dut, nibbles, bit)
        try:
            bits = descramble(mlt3_decode(mlt3_levels(trace), bit))
            groups, before = eth_groups_after_jk(bits)
            wanted = [CODE_4B5B[n] for n in nibbles] + [T, R]
            ok = groups[:len(wanted)] == wanted and all(before[20:]) and all(g == IDLE for g in groups[len(wanted):-1])
        except AssertionError as err:
            dut._log.info(f"100BASE-TX chain at {bit} cycles a symbol: {err}")
            ok = False
        dut._log.info(f"100BASE-TX chain at {bit} cycles a symbol ({50 / bit:.2f} MBd): {'good' if ok else 'wrong'}")
        if ok:
            fastest = bit
        else:
            break
    assert fastest is not None, "the chain never worked, even slowly"
    dut._log.info(f"100BASE-TX whole chain tops out at {50 / fastest:.2f} MBd, needs 125: {125 / (50 / fastest):.0f} times too slow")


@cocotb.test()
async def test_eth_absolute_pin_limit(dut):
    """The fastest a pin can move at all, with no encoding: one bit per cycle from SR. Below 125 MBd regardless."""
    from test import assemble, capture
    await reset(dut)
    prog = assemble("""
        msb   1
        set   4, 0
        mode  4, eng
        sri   0x55, 8
loop:   bci   8
b:      outb  4, b
        sri   0x55, 8
        jmp   loop
""")
    await load_program(dut, prog.words)
    await start(dut)
    await wait_cycles(20)
    trace = await capture(dut, 400, None)
    await stop(dut)
    levels = [(t[0] >> 4) & 1 for t in trace]
    changes = [i for i in range(1, len(levels)) if levels[i] != levels[i - 1]]
    shortest = min(b - a for a, b in zip(changes, changes[1:]))
    assert shortest == 1, "OUTB should move one bit per cycle"
    dut._log.info(f"Fastest pin change: every {shortest} cycle, so at most 50 MBd against 125")


@cocotb.test()
async def test_eth_receive_at_125_mbd_cannot_work(dut):
    """Plan test 6: feed a real 125 MBd stream to the fastest possible sampling, and count what survives."""
    from test import assemble
    await reset(dut)
    prog = assemble("""
        msb   1
top:  bci   16
b:    inb   0, b
      push
      jmp   top
""".replace("      ", "        "))
    await load_program(dut, prog.words)
    rng_bits = [(i * i + (i >> 2)) & 1 for i in range(400)]

    async def play():
        for b in rng_bits:
            dut.uio_in.value = 0xFE | b
            await Timer(8, "ns")

    await start(dut)
    cocotb.start_soon(play())
    await wait_cycles(10)
    words = []
    for _ in range(8):
        await wait_cycles(16)
    words = await fifo_all(dut, 4)
    await stop(dut)
    captured = len(words) * 16
    per_symbol = 8 / CYCLE_NS
    dut._log.info(f"At 50 MHz, one sample per {CYCLE_NS} ns against one symbol per 8 ns: {per_symbol:.2f} samples a symbol, "
                  f"so at least {100 * (1 - per_symbol):.0f}% of symbols are never seen. Captured {captured} bits")
    assert per_symbol < 1, "this test assumes sampling slower than the symbols"


async def eth_rx_run(dut, groups, half):
    from test import capture
    await stop(dut)
    await load_program(dut, firmware("trial/eth_rx.s").words)
    await start(dut)
    bits = [1] * 20 + [b for g in groups for b in group_bits(g)] + [1] * 20
    words = []
    wave = []
    for b in bits:
        wave += [(b, 0)] * half + [(b, 1)] * half
    done = []

    async def reader():
        while not done:
            if not (await spi_xfer(dut, [0x12, 0]))[1] & 0x10:
                words.extend(await fifo_read(dut, 1))

    task = cocotb.start_soon(reader())

    def bus(t, out, oe):
        data, clk = wave[t] if t < len(wave) else (1, 0)
        return 0xFC | data | clk << 1

    trace = await capture(dut, len(wave) + 400, bus)
    done.append(True)
    await task
    words += await fifo_all(dut, 8)
    await stop(dut)
    return words, trace


@cocotb.test()
async def test_eth_receive_code_groups(dut):
    """Decoding, delimiters and an invalid group. Finds the fastest clock that decodes correctly."""
    await reset(dut)
    nibbles = list(range(16))
    good = [J, K] + [CODE_4B5B[n] for n in nibbles] + [T, R]
    fastest = None
    for half in (60, 40, 30, 26, 22, 20, 18, 16, 14, 12):
        words, _ = await eth_rx_run(dut, good, half)
        ok = words == nibbles + [0x0200]
        dut._log.info(f"100BASE-X receive at {2 * half} cycles a bit ({25 / half:.2f} MBd): {'good' if ok else [hex(w) for w in words][:20]}")
        if ok:
            fastest = half
        else:
            break
    assert fastest is not None
    dut._log.info(f"100BASE-X receive tops out at {25 / fastest:.2f} MBd, needs 125")
    words, trace = await eth_rx_run(dut, [J, K, CODE_4B5B[3], 0x00, CODE_4B5B[4]], 60)
    assert words[:2] == [3, 0x0100], f"invalid group: {[hex(w) for w in words]}"
    assert any((t[0] >> 7) & 1 for t in trace), "RX_ERROR never rose"


# ------------------------------------------------------------------ My own picks (weaker evidence: I chose them)
# ---- MDIO, clause 22
class Phy:
    """A PHY register model: samples MDIO on the rising edge of MDC, and drives its data just after that edge."""

    def __init__(self, phyad=3):
        self.phyad, self.regs, self.sck, self.bits = phyad, {2: 0x1234, 9: 0xBEEF}, 0, []
        self.drive = None
        self.queue, self.count, self.ones = [], 0, 0

    def step(self, t, out, oe):
        sck = (out >> 4) & 1
        host = (out >> 5) & 1 if (oe >> 5) & 1 else None
        line = host if host is not None else (self.drive if self.drive is not None else 1)
        if sck and not self.sck:
            if self.queue:
                self.drive = self.queue.pop(0)
                if not self.queue:
                    self.after = True
            elif self.drive is not None and host is None:
                self.drive = None
            else:
                self.bits.append(line)
                bits = self.bits
                if len(bits) >= 46 and bits[-46:-14] == [1] * 32 and bits[-14:-12] == [0, 1]:
                    word = sum(b << (13 - i) for i, b in enumerate(bits[-14:]))
                    op, ad, reg = (word >> 10) & 3, (word >> 5) & 31, word & 31
                    if op == 2 and ad == self.phyad:
                        value = self.regs.get(reg, 0)
                        self.queue = [0] + [(value >> k) & 1 for k in range(15, -1, -1)]
                    self.op = (op, ad, reg)
                    self.write_left = 18 if op == 1 and ad == self.phyad else 0
                    self.bits = []
                elif getattr(self, "write_left", 0):
                    self.write_left -= 1
                    if self.write_left == 0:
                        value = sum(b << (15 - i) for i, b in enumerate(bits[-16:]))
                        self.regs[self.op[2]] = value
                        self.bits = []
        self.sck = sck
        return 0xDF | line << 5


def mdio_word(op, phy, reg, ta):
    return 0b01 << 14 | op << 12 | phy << 7 | reg << 2 | ta


async def mdio_run(dut, phy, words, cycles):
    from test import capture
    task = cocotb.start_soon(fifo_write(dut, words))  # the program starts clocking as soon as a word arrives
    trace = await capture(dut, cycles, phy.step)
    await task
    return await fifo_all(dut, 8), trace


@cocotb.test()
async def test_mdio_read_write_and_absent_phy(dut):
    await reset(dut)
    await load_program(dut, firmware("trial/mdio.s").words)
    await start(dut)
    phy = Phy()
    words, trace = await mdio_run(dut, phy, [mdio_word(2, 3, 9, 0)], 1800)
    assert words == [0xBEEF, 0xA000], f"read: {[hex(w) for w in words]}"
    edges = [i for i in range(1, len(trace)) if (trace[i][0] >> 4) & 1 and not (trace[i - 1][0] >> 4) & 1]
    period = edges[1] - edges[0]
    dut._log.info(f"MDC period {period} cycles: {50 / period:.2f} MHz, and the standard's limit is 2.5")
    assert period >= 20
    words, _ = await mdio_run(dut, phy, [mdio_word(1, 3, 5, 0b10), 0xCAFE], 1800)
    assert words == [0xA000], f"write: {[hex(w) for w in words]}"
    assert phy.regs.get(5) == 0xCAFE, f"{phy.regs}"
    words, _ = await mdio_run(dut, phy, [mdio_word(2, 3, 5, 0)], 1800)
    assert words == [0xCAFE, 0xA000], f"read back: {[hex(w) for w in words]}"
    words, _ = await mdio_run(dut, phy, [mdio_word(2, 7, 9, 0)], 1800)
    assert words == [0xEE00], f"no PHY at that address: {[hex(w) for w in words]}"
    await stop(dut)


# ---- LIN
LIN_BIT = 2604


def lin_pid(identifier):
    b = [(identifier >> k) & 1 for k in range(6)]
    p0 = b[0] ^ b[1] ^ b[2] ^ b[4]
    p1 = 1 - (b[1] ^ b[3] ^ b[4] ^ b[5])
    return identifier | p0 << 6 | p1 << 7


def lin_checksum(data):
    total = 0
    for byte in data:
        total += byte
        if total > 255:
            total -= 255
    return (~total) & 0xFF


async def uart_send(bus, byte, bit):
    bus.drive(0)
    await wait_cycles(bit)
    for k in range(8):
        bus.drive((byte >> k) & 1)
        await wait_cycles(bit)
    bus.drive(1)
    await wait_cycles(bit)


async def lin_header(bus, pid, bit, break_bits=13):
    bus.drive(0)
    await wait_cycles(break_bits * bit)
    bus.drive(1)
    await wait_cycles(bit)
    await uart_send(bus, 0x55, bit)
    await wait_cycles(bit)
    await uart_send(bus, pid, bit)


def uart_decode(bus, began, count, bit):
    """Bytes sampled mid-bit on falling edges after a time, from the recorded line."""
    out, t = [], began
    for _ in range(count):
        falls = bus.falls_after(t)
        if not falls:
            break
        first = falls[0]
        out.append(sum(bus.sample(round(first + bit * (k + 1.5))) << k for k in range(8)))
        t = first + bit * 9
    return out


# The trial firmware, and the version using ADDR and TSETR, added after the trial. Both must pass.
LIN_PROGRAMS = ("trial/lin.s", "lin.s")


async def lin_start(dut, program="trial/lin.s", **defines):
    await stop(dut)
    await load_program(dut, firmware(program, **defines).words)
    await write_reg(dut, 1, 0x10)
    await start(dut)
    bus = Bus(dut, 4)
    await wait_cycles(200)
    return bus


@cocotb.test()
async def test_lin_receive_with_auto_baud_and_checksum(dut):
    """Frames at nominal and off by 10% each way, measured from the sync byte. Then a bad checksum and bad parity."""
    await reset(dut)
    for program in LIN_PROGRAMS:
        for scale, data in ((1.0, [0x12, 0xFE]), (0.9, [0xFF, 0xFF]), (1.1, [0x00, 0x80])):
            bit = round(LIN_BIT * scale)
            bus = await lin_start(dut, program)
            pid = lin_pid(0x10)
            await lin_header(bus, pid, bit)
            for b in data + [lin_checksum([pid] + data)]:
                await uart_send(bus, b, bit)
            await wait_cycles(200)
            got = await fifo_all(dut)
            assert got == [pid, 0x100 | data[0], 0x100 | data[1], 0xA000], f"{program} at {scale}: {[hex(w) for w in got]}"
            bus.close()
        bus = await lin_start(dut, program)
        pid = lin_pid(0x10)
        await lin_header(bus, pid, LIN_BIT)
        for b in [1, 2, lin_checksum([pid, 1, 2]) ^ 4]:
            await uart_send(bus, b, LIN_BIT)
        await wait_cycles(200)
        assert (await fifo_all(dut))[-1] == 0xC500, f"{program}: a bad checksum wasn't noticed"
        bus.close()
        bus = await lin_start(dut, program)
        await lin_header(bus, lin_pid(0x10) ^ 0x40, LIN_BIT)
        await wait_cycles(200)
        assert (await fifo_all(dut)) == [0xBAD0], f"{program}: bad parity wasn't noticed"
        bus.close()


@cocotb.test()
async def test_lin_responds_and_ignores_a_short_break(dut):
    await reset(dut)
    for program in LIN_PROGRAMS:
        bus = await lin_start(dut, program)
        await fifo_write(dut, [0xA5, 0x3C])
        pid = 0x3C
        await lin_header(bus, pid, LIN_BIT)
        began = cycles_now()
        await wait_cycles(45 * LIN_BIT)
        got = uart_decode(bus, began, 3, LIN_BIT)
        assert got == [0xA5, 0x3C, lin_checksum([pid, 0xA5, 0x3C])], f"{program}: {got}"
        assert (await fifo_all(dut)) == [pid, 0xA000], program
        bus.close()
        bus = await lin_start(dut, program)
        bus.drive(0)
        await wait_cycles(5 * LIN_BIT)
        bus.drive(1)
        await wait_cycles(40 * LIN_BIT)
        assert (await fifo_all(dut)) == [], f"{program}: treated a short low pulse as a break"
        bus.close()



@cocotb.test()
async def test_lin_new_firmware_far_above_lin_rates(dut):
    """With ADDR and TSETR, a node set up for 104 cycles a bit (480 kbaud) still receives a worst-case frame.

    The trial version stops near 30 kbaud on 0xFF bytes, where counting up the checksum runs out of time.
    """
    await reset(dut)
    bit, data = 104, [0xFF, 0xFF]
    bus = await lin_start(dut, "lin.s", NOMINAL=bit)
    pid = lin_pid(0x10)
    await lin_header(bus, pid, bit)
    for b in data + [lin_checksum([pid] + data)]:
        await uart_send(bus, b, bit)
    await wait_cycles(200)
    got = await fifo_all(dut)
    assert got == [pid, 0x1FF, 0x1FF, 0xA000], [hex(w) for w in got]
    bus.close()

# ---- SENT
def sent_crc(nibbles):
    crc = 5
    for n in nibbles + [0]:
        for k in range(3, -1, -1):
            top = ((crc >> 3) & 1) ^ ((n >> k) & 1)
            crc = (crc << 1) & 0xF
            if top:
                crc ^= 0xD
    return crc


@cocotb.test()
async def test_sent_transmit_frame(dut):
    """Falling-edge intervals give every nibble, in ticks, and CRC-4 comes from a separate calculation."""
    await reset(dut)
    await load_program(dut, firmware("trial/sent_tx.s").words)
    await start(dut)
    bus = Bus(dut, 4)
    status, data = 0x9, [0x1, 0x2, 0xA, 0xF, 0x0, 0x7]
    await fifo_write(dut, [status << 12 | data[0] << 8 | data[1] << 4 | data[2], data[3] << 12 | data[4] << 8 | data[5] << 4])
    await wait_cycles(150 * (56 + 12 * 9 + 15 * 9 + 20))
    falls = bus.falls_after(0)
    ticks = [(b - a) / 150 for a, b in zip(falls, falls[1:])]
    assert len(ticks) >= 9, f"{ticks}"
    assert all(abs(t - round(t)) < 0.05 for t in ticks[:9]), f"timing isn't on the tick grid: {ticks[:9]}"
    ticks = [round(t) for t in ticks]
    assert ticks[0] == 56, f"sync is {ticks[0]} ticks"
    values = [t - 12 for t in ticks[1:9]]
    assert values[0] == status and values[1:7] == data, f"{values}"
    assert values[7] == sent_crc(data), f"CRC {values[7]}, expected {sent_crc(data)}"
    bus.close()
    await stop(dut)


# ---- HDMI-CEC
CEC_TICK = 5000


def cec_bits(blocks):
    out = []
    for i, block in enumerate(blocks):
        out += [(block >> k) & 1 for k in range(7, -1, -1)] + [int(i == len(blocks) - 1), 1]
    return out


async def cec_send_bit(bus, bit):
    low = 1.5 if bit == 0 else 0.6
    bus.drive(0)
    await wait_cycles(low * 50000)
    bus.drive(1)
    await wait_cycles((2.4 - low) * 50000)


def cec_read_frame(bus, first):
    """From the recorded line: the start bit's width, then each bit's low time, as a device would measure."""
    falls = bus.falls_after(first)
    rises = [t for t, level in bus.history if level == 1 and t >= first]
    widths = []
    for f in falls:
        r = next((x for x in rises if x > f), None)
        widths.append(((r - f) / 50000) if r else None)
    return falls, widths


async def cec_start_prog(dut, name):
    await stop(dut)
    await load_program(dut, firmware(name).words)
    await write_reg(dut, 1, 0x10)
    await start(dut)
    bus = Bus(dut, 4)
    await wait_cycles(100)
    return bus


@cocotb.test()
async def test_cec_initiator_frame_and_ack(dut):
    """Exact bit widths, with a follower model that ACKs. Then the same with no follower."""
    await reset(dut)
    bus = await cec_start_prog(dut, "trial/cec_initiator.s")
    began = cycles_now()
    await fifo_write(dut, [0x40 << 8, 0x36 << 8 | 1])

    async def follower():
        """ACK: hold low from the falling edge of each ACK bit to 1.5 ms."""
        seen = 0
        while seen < 21:
            falls = bus.falls_after(began)
            if len(falls) > seen:
                index, seen = seen, seen + 1
                if index in (10, 20):
                    bus.drive(0)
                    await wait_cycles(1.5 * 50000 - 500)
                    bus.drive(1)
            await wait_cycles(200)

    task = cocotb.start_soon(follower())
    await wait_cycles(70 * CEC_TICK + 22 * 2.4 * 50000 + 4.5 * 50000 + 40000)
    task.cancel()
    falls, widths = cec_read_frame(bus, began)
    assert 3.6 <= widths[0] <= 3.8, f"start bit low for {widths[0]:.2f} ms"
    assert 4.3 <= (falls[1] - falls[0]) / 50000 <= 4.7, "start bit period"
    wanted = cec_bits([0x40, 0x36])
    for i, b in enumerate(wanted[:-1]):
        if i in (9, 19):
            continue
        low = widths[i + 1]
        assert abs(low - (1.5 if not b else 0.6)) < 0.11, f"bit {i} was low for {low:.2f} ms, wanted {b}"
        period = (falls[i + 2] - falls[i + 1]) / 50000
        assert abs(period - 2.4) < 0.11, f"bit {i} took {period:.2f} ms"
    assert await fifo_all(dut) == [0xA000], "the frame should have finished acknowledged"
    bus.close()

    bus = await cec_start_prog(dut, "trial/cec_initiator.s")
    await fifo_write(dut, [0x40 << 8 | 1])
    await wait_cycles(70 * CEC_TICK + 12 * 2.4 * 50000 + 4.5 * 50000)
    assert await fifo_all(dut) == [0x0E00], "no ACK should be reported"
    bus.close()


@cocotb.test()
async def test_cec_arbitration_lost(dut):
    """Another initiator sends a 0 where we send a 1: we must stop driving and report it."""
    await reset(dut)
    bus = await cec_start_prog(dut, "trial/cec_initiator.s")
    began = cycles_now()
    await fifo_write(dut, [0xF0 << 8 | 1])
    while not bus.falls_after(began):
        await wait_cycles(2000)
    first = bus.falls_after(began)[0]
    await wait_cycles(first + 4.5 * 50000 - cycles_now())
    bus.drive(0)  # our first data bit is a 1. Theirs is a 0
    await wait_cycles(1.5 * 50000)
    bus.drive(1)
    await wait_cycles(0.9 * 50000 + 2 * 50000)
    assert bus.own() == 1, "still driving after losing"
    assert await fifo_all(dut) == [0xAB00]
    bus.close()


@cocotb.test()
async def test_cec_follower_receives_and_acks(dut):
    await reset(dut)
    bus = await cec_start_prog(dut, "trial/cec_follower.s")

    async def frame(blocks):
        bus.drive(0)
        await wait_cycles(3.7 * 50000)
        bus.drive(1)
        await wait_cycles(0.8 * 50000)
        acks = []
        for i, b in enumerate(cec_bits(blocks)):
            if (i + 1) % 10 == 0:  # ACK bit: send a 1, and read it at 1.05 ms
                bus.drive(0)
                await wait_cycles(0.6 * 50000)
                bus.drive(1)
                await wait_cycles(0.45 * 50000)
                acks.append(bus.level == 0)
                await wait_cycles(1.35 * 50000)
            else:
                await cec_send_bit(bus, b)
        return acks

    assert await frame([0x34, 0x82]) == [True, True], "a block addressed to us must be acknowledged"
    await wait_cycles(10000)
    got = await fifo_all(dut)
    assert got == [0x34, 0x100 | 0x82], f"{[hex(w) for w in got]}"
    assert await frame([0x35]) == [False], "someone else's block must not be acknowledged"
    await wait_cycles(10000)
    assert await fifo_all(dut) == [0x100 | 0x35]
    bus.drive(0)
    await wait_cycles(1 * 50000)
    bus.drive(1)
    await wait_cycles(4 * 50000)
    assert await fifo_all(dut) == [0xBAD0], "a short low pulse isn't a start bit"
    bus.close()
