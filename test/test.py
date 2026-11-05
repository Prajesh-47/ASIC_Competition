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
        # heartbeat (uo[3]) takes 2^23 cycles to rise; everything else is tied low
        assert dut.uo_out.value == 0
