# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# Quad host mode (host register 23, v2): every host command with 4 bits each way per SCK edge, on IO0 =
# MOSI/MISO and IO1-3 = AUX_IN/AUX_OUT 0-2. Each test enters quad mode with a single-lane register write and
# leaves it with a quad one, so the next test starts in single-lane mode.

import cocotb
from cocotb.triggers import ClockCycles

import test as host
from test import (assemble, fifo_read, fifo_write, load_program, read_reg, reset, spi_xfer, start, stop,
                  write_reg)

ECHO = assemble("loop: pullw 16\npush\njmp loop\n")


async def enter_quad(dut):
    host.QUAD = False
    await write_reg(dut, 23, 1)
    host.QUAD = True


async def leave_quad(dut):
    await write_reg(dut, 23, 0)
    host.QUAD = False


async def read_program(dut, base, count):
    data = await spi_xfer(dut, [0x02, base >> 8, base & 0xFF] + [0] * (2 * count))
    return [data[3 + 2 * i] << 8 | data[4 + 2 * i] for i in range(count)]


@cocotb.test()
async def test_quad_registers_and_program_memory(dut):
    """Registers and all 512 program words round trip in quad mode, and single-lane mode works after it."""
    await reset(dut)
    await enter_quad(dut)
    try:
        assert await read_reg(dut, 23) == 1, "register 23 doesn't read back as quad"
        await write_reg(dut, 3, 0xA5)
        assert await read_reg(dut, 3) == 0xA5
        words = [(i * 0x9E37 + 0x1234) & 0xFFFF for i in range(512)]
        await stop(dut)
        await load_program(dut, words)
        got = await read_program(dut, 0, 512)
        bad = [i for i in range(512) if got[i] != words[i]]
        assert not bad, f"{len(bad)} words differ, first at {bad[0]}: {got[bad[0]]:04x} vs {words[bad[0]]:04x}"
    finally:
        await leave_quad(dut)
    assert await read_reg(dut, 23) == 0, "quad mode didn't end"
    assert await read_reg(dut, 3) == 0xA5, "single-lane mode broken after quad"


@cocotb.test()
async def test_quad_fifo_status_and_xfer(dut):
    """A running echo program: FIFO_WR and FIFO_RD, STATUS levels, and full-duplex XFER, all in quad mode."""
    from test_hostlink import xfer
    await reset(dut)
    await stop(dut)
    await load_program(dut, ECHO.words)
    await start(dut)
    await enter_quad(dut)
    try:
        await fifo_write(dut, [0x1111, 0x2222, 0x3333])
        await ClockCycles(dut.clk, 50)
        status = await spi_xfer(dut, [0x12, 0, 0, 0])
        assert status[2] == 3, f"STATUS level {status[2]}, expected 3"
        assert await fifo_read(dut, 3) == [0x1111, 0x2222, 0x3333]
        sent, got, feed = 0, [], [0x4000 + i for i in range(40)]
        free = 8
        for _ in range(200):
            n, words, free = await xfer(dut, feed[sent:], free)
            sent += n
            got += words
            if len(got) == len(feed):
                break
        assert got == feed, f"XFER echoed {len(got)} of {len(feed)}: {[hex(w) for w in got[:6]]}"
    finally:
        await leave_quad(dut)
        await stop(dut)


@cocotb.test()
async def test_quad_pins(dut):
    """In quad mode AUX_OUT0-2 belong to the host, and stay low with CS high. AUX_OUT3 stays the engine's."""
    await reset(dut)
    await stop(dut)
    await load_program(dut, assemble("set 11, 1\nset 8, 1\nset 9, 1\nset 10, 1\nhalt\n").words)
    await start(dut)
    await ClockCycles(dut.clk, 20)
    assert (int(dut.uo_out.value) >> 4) == 0xF, "engine doesn't drive AUX_OUT in single-lane mode"
    await enter_quad(dut)
    try:
        await ClockCycles(dut.clk, 10)
        assert (int(dut.uo_out.value) >> 4) == 0x8, f"quad idle: AUX_OUT is {int(dut.uo_out.value) >> 4:x}, not 8"
    finally:
        await leave_quad(dut)
    await ClockCycles(dut.clk, 10)
    assert (int(dut.uo_out.value) >> 4) == 0xF, "engine didn't get AUX_OUT back"
    await stop(dut)
