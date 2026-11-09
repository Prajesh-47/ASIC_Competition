# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# SD end to end with the XFER host link (explore/isa-lanes-int): a whole CMD17 read and CMD24 write, timed
# from the host's first XFER to the moment the host holds the final status word. Main's sd_host.s against the
# LANES firmware, over a sweep of SPI clocks. These log numbers for lab/explore/isa-lanes-int.md; each run
# also checks the data, so a number is only logged for a transfer that worked.
#
#   make COCOTB_TEST_MODULES=test_sd_xfer
#   make COCOTB_TEST_MODULES=test_sd_xfer COCOTB_TEST_FILTER=measure_sd_xfer_in_spec

import cocotb
from cocotb.triggers import FallingEdge

import test as host
from test import reset
from test_hostlink import xfer
from test_trial import (SdCard, block_nibbles, cmd_bytes, crc_nibbles, line_crcs, sd_block_from_words,
                        sd_clock_periods, sd_start)
from splice.model import Splice

PROGRAMS = {"main": "sd_host.s", "lanes": "../../lab/explore/fw/sd_host_lanes.s"}
IN_SPEC = [4, 5, 6, 8, 10, 16]   # SCK = core / (2 * half); the host interface allows core/8 at most
OUT_OF_SPEC = [3, 2]             # faster than docs/architecture.md section 8 allows: only to see the trend


def script_words(script):
    feed = []
    for typ, cmd, then, data in script:
        feed += [typ, cmd[0] << 8 | cmd[1], cmd[2] << 8 | cmd[3], cmd[4] << 8]
        if then is not None:
            feed.append(then)
        feed += data
    return feed


async def sd_run_xfer(dut, card, script, until, half, limit=400_000):
    """The trial's sd_run with an XFER host. Returns (words, cycles until `until(words)` held, or None,
    cycle the command's status word reached the host)."""
    feed = script_words(script)
    saved, host.HALF = host.HALF, half
    words, state = [], {"t": 0, "done": None, "cmd": None}

    async def run():
        free = Splice.FIFO_DEPTH  # START leaves the host-to-engine FIFO empty
        while state["done"] is None and state["t"] < limit:
            sent, got, free = await xfer(dut, feed, free)
            del feed[:sent]
            words.extend(got)
            if state["cmd"] is None and 0xA000 in words:
                state["cmd"] = state["t"]
            if until(words):
                state["done"] = state["t"]

    try:
        task = cocotb.start_soon(run())
        t = 0
        while state["done"] is None and t < limit:
            await FallingEdge(dut.clk)
            dut.uio_in.value = card.step(t, int(dut.uio_out.value), int(dut.uio_oe.value))
            t += 1
            state["t"] = t
        await task
    finally:
        host.HALF = saved
    return words, state["done"], state["cmd"]


def write_data():
    block = [(i * 13 + 5) & 0xFF for i in range(512)]
    tail = crc_nibbles(line_crcs(block_nibbles(block)))
    data = [block[i] << 8 | block[i + 1] for i in range(0, 512, 2)]
    data += [tail[i] << 12 | tail[i + 1] << 8 | tail[i + 2] << 4 | tail[i + 3] for i in range(0, 16, 4)]
    return block, data


async def read_once(dut, program, half):
    await sd_start(dut, program=program)
    card = SdCard()
    words, done, cmd = await sd_run_xfer(dut, card, [(1, cmd_bytes(17, 0), 1, [])], lambda w: len(w) >= 265, half)
    if done is None:
        return None
    nibbles, tail = sd_block_from_words(words[4:264])
    ok = (words[3] == 0xA000 and nibbles == block_nibbles(card.block) and tail == crc_nibbles(line_crcs(nibbles))
          and words[264] == 0xA500)
    return ok, done, cmd, 50 / min(sd_clock_periods(card.clock_edges))


async def write_once(dut, program, half):
    await sd_start(dut, program=program)
    card = SdCard()
    block, data = write_data()
    words, done, cmd = await sd_run_xfer(dut, card, [(1, cmd_bytes(24, 0), 2, data)], lambda w: len(w) >= 5, half)
    if done is None:
        return None
    ok = card.written == block and card.write_status == 0b010 and words[4] == 0xA502
    return ok, done, cmd, 50 / min(sd_clock_periods(card.clock_edges))


async def sweep(dut, halves, strict):
    for half in halves:
        for name, program in PROGRAMS.items():
            for kind, fn in (("read", read_once), ("write", write_once)):
                result = await fn(dut, program, half)
                if result is None or not result[0]:
                    dut._log.info(f"MEASURE sd xfer {kind} {name} SCK=core/{2 * half}: FAILED {result}")
                    assert not strict, f"{kind} {name} at core/{2 * half}"
                    continue
                _, done, cmd, fastest = result
                data = done - cmd
                dut._log.info(f"MEASURE sd xfer {kind} {name} SCK=core/{2 * half}: total {done} cycles "
                              f"({512 * 50e6 / done / 1e3:.0f} kB/s), command {cmd}, after the command {data} "
                              f"({data / 260:.1f} per word, {512 * 50e6 / data / 1e3:.0f} kB/s), "
                              f"fastest SD clock {fastest:.2f} MHz")


@cocotb.test()
async def measure_sd_xfer_in_spec(dut):
    await reset(dut)
    await sweep(dut, IN_SPEC, True)


@cocotb.test()
async def measure_sd_xfer_out_of_spec(dut):
    await reset(dut)
    await sweep(dut, OUT_OF_SPEC, False)
