# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# Week-1 smoke test for the SPLICE stub. Runs on both RTL and the
# gate-level netlist (GATES=yes), so it only touches top-level ports.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles


async def reset(dut):
    clock = Clock(dut.clk, 20, unit="ns")  # 50 MHz, matches info.yaml
    cocotb.start_soon(clock.start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 1)


@cocotb.test()
async def test_reset_state(dut):
    """After reset every output is low and every bus line is hi-Z."""
    await reset(dut)
    assert dut.uo_out.value == 0, f"uo_out={dut.uo_out.value}"
    assert dut.uio_oe.value == 0, "lines must be hi-Z out of reset"
    assert dut.uio_out.value == 0


@cocotb.test()
async def test_lines_stay_hiz(dut):
    """Bus traffic on uio/ui must not make SPLICE drive anything (unconfigured)."""
    await reset(dut)
    for v in (0x00, 0xFF, 0xA5, 0x5A):
        dut.ui_in.value = v
        dut.uio_in.value = v
        await ClockCycles(dut.clk, 4)
        assert dut.uio_oe.value == 0
        # heartbeat (uo[3]) takes 2^23 cycles to rise; the SRAM self-test takes
        # thousands of cycles to raise uo[1]/uo[2]; everything else is tied low
        assert dut.uo_out.value == 0


# SRAM self-test results on branch exp/two-sram-harden.
DONE = 0x02  # uo[1]
PASS = 0x04  # uo[2]
# 4 passes of 2N accesses over 256 and 512 words is 3072 cycles, plus overhead.
SELFTEST_MIN_CYCLES = 3072
SELFTEST_MAX_CYCLES = 3200


async def run_selftest(dut):
    """Wait for the self-test, checking the lines stay hi-Z. Returns cycles taken."""
    for cycle in range(SELFTEST_MAX_CYCLES):
        await ClockCycles(dut.clk, 1)
        assert dut.uio_oe.value == 0, f"line driven during self-test, cycle {cycle}"
        if int(dut.uo_out.value) & DONE:
            return cycle
    assert False, f"self-test not done after {SELFTEST_MAX_CYCLES} cycles"


@cocotb.test()
async def test_sram_selftest(dut):
    """Every word of both macros reads back correctly, in both data polarities."""
    await reset(dut)
    cycles = await run_selftest(dut)
    assert cycles >= SELFTEST_MIN_CYCLES, f"finished too early: {cycles} cycles"
    await ClockCycles(dut.clk, 2)
    assert int(dut.uo_out.value) & PASS, f"self-test failed: uo_out={dut.uo_out.value}"


@cocotb.test()
async def test_sram_selftest_after_reset(dut):
    """Resetting partway through restarts the self-test, and it still passes."""
    await reset(dut)
    await ClockCycles(dut.clk, 500)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 2)
    assert dut.uo_out.value == 0
    dut.rst_n.value = 1
    cycles = await run_selftest(dut)
    assert cycles >= SELFTEST_MIN_CYCLES, f"finished too early: {cycles} cycles"
    await ClockCycles(dut.clk, 2)
    assert int(dut.uo_out.value) & PASS, f"self-test failed: uo_out={dut.uo_out.value}"
