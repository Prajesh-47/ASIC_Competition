# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# Host link measurements: how slow an SPI host can be and still keep up, for each FIFO depth and host
# strategy. These log numbers for docs/adaptability.md. The pass/fail checks live in other test files.

import cocotb
from cocotb.triggers import ClockCycles

import test as host
from test import capture, reset, stop, write_reg
from test_trial import (FLAG, PPP, hdlc_rx_decode, hdlc_rx_levels, hdlc_start, hdlc_stuff, hdlc_sustained,
                        hdlc_tx_stream, hdlc_tx_words, hdlc_unstuff_frames, hdlc_with_fcs, bits_to_bytes,
                        lsb_bits, HDLC_BIT, HDLC_CLK, HDLC_RX)

HALVES = [4, 5, 6, 8, 10, 12, 16, 20, 24, 32]  # SCK = core / (2 * half)


class Spi:
    """One SPI transaction a byte at a time, so the host can act on what it has read, as a real master can."""

    def __init__(self, dut, ui_high=0xF0):
        self.dut, self.ui = dut, ui_high

    async def begin(self):
        self.dut.ui_in.value = self.ui
        await ClockCycles(self.dut.clk, host.HALF)

    async def byte(self, out):
        value = 0
        for k in range(7, -1, -1):
            bit = (out >> k) & 1
            self.dut.ui_in.value = self.ui | (bit << 1)
            await ClockCycles(self.dut.clk, host.HALF)
            value = (value << 1) | int(str(self.dut.uo_out.value)[-1])
            self.dut.ui_in.value = self.ui | (bit << 1) | 1
            await ClockCycles(self.dut.clk, host.HALF)
        self.dut.ui_in.value = self.ui | (out & 1) << 1
        return value

    async def end(self):
        await ClockCycles(self.dut.clk, host.HALF)
        self.dut.ui_in.value = self.ui | 0x04
        await ClockCycles(self.dut.clk, 2 * host.HALF)


async def xfer(dut, out_words, free_known):
    """One XFER: sends what fits in the room the last XFER reported, and reads what the level says is there."""
    spi = Spi(dut)
    nw = min(len(out_words), free_known, 255)
    await spi.begin()
    await spi.byte(0x22)
    level = await spi.byte(nw)
    free = await spi.byte(0)
    got = []
    for i in range(max(nw, level)):
        w = out_words[i] if i < nw else 0
        hi = await spi.byte(w >> 8)
        lo = await spi.byte(w & 0xFF)
        if i < level:
            got.append(hi << 8 | lo)
    await spi.end()
    return nw, got, max(free - nw, 0)


async def exchange_xfer(dut, tx_frames=(), rx_bits=(), extra=400):
    """hdlc_exchange from test_trial.py, with an XFER host in place of the STATUS, write, read loop."""
    words, feed = [], [w for frame in tx_frames for w in hdlc_tx_words(frame)]
    wave = hdlc_rx_levels(list(rx_bits))
    total = max(len(wave), (sum(len(f) for f in tx_frames) * 10 + 60) * HDLC_BIT) + extra
    done = []

    def bus(t, out, oe):
        data, clk = wave[t] if t < len(wave) else (1, 0)
        return 0xFC | data << HDLC_RX | clk << HDLC_CLK

    async def run():
        free = 0
        while True:
            sent, got, free = await xfer(dut, feed, free)
            del feed[:sent]
            words.extend(got)
            if done and not feed and not got:
                return

    task = cocotb.start_soon(run())
    trace = await capture(dut, total, bus)
    done.append(True)
    await task
    return trace, words


async def hdlc_sustained_xfer(dut, half):
    saved, host.HALF = host.HALF, half
    try:
        await hdlc_start(dut)
        out_frame = PPP + [(i * 37) & 0xFF for i in range(60)]
        in_frame = PPP + [(i * 91) & 0xFF for i in range(60)]
        bits = FLAG + hdlc_stuff(lsb_bits(hdlc_with_fcs(in_frame))) + FLAG + FLAG
        trace, words = await exchange_xfer(dut, [out_frame], bits)
        stream, _ = hdlc_tx_stream(trace)
        sent = [bits_to_bytes(f) for f in hdlc_unstuff_frames(stream) if len(f) % 8 == 0]
        ok = sent == [hdlc_with_fcs(out_frame)] and hdlc_rx_decode(words) == [(hdlc_with_fcs(in_frame), 1)]
        await stop(dut)
        await write_reg(dut, 0, 0)
        return ok
    finally:
        host.HALF = saved


async def sweep(dut, name, fn):
    for half in HALVES:
        ok = await fn(dut, half)
        dut._log.info(f"MEASURE hdlc full duplex, {name}: SCK=core/{2 * half} {'keeps up' if ok else 'falls behind'}")
        if not ok:
            break


@cocotb.test()
async def measure_hdlc_full_duplex_trial_host(dut):
    """The trial's host loop, unchanged: STATUS, then one word out and up to two in."""
    await reset(dut)
    await sweep(dut, "trial host", lambda d, h: hdlc_sustained(d, h, True, True))


@cocotb.test()
async def measure_hdlc_full_duplex_xfer_host(dut):
    """One XFER per loop, sized from the levels the last one reported."""
    await reset(dut)
    await sweep(dut, "XFER host", hdlc_sustained_xfer)
