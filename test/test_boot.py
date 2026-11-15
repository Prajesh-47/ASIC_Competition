# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# The boot store (host register 23 bit 1): the first 64 program words also live in flops, so the chip
# still runs short programs if the program SRAM macro doesn't work on silicon.

import cocotb
from cocotb.handle import Force, Release

from test import (GATE_LEVEL, assemble, capture, fifo_write, firmware, load_program, read_reg, reset, spi_xfer,
                  start, stop, uart_frames, write_reg)

BOOT_WORDS = 64


async def read_program(dut, base, count):
    data = await spi_xfer(dut, [0x02, base >> 8, base & 0xFF] + [0] * (2 * count))
    return [data[3 + 2 * i] << 8 | data[4 + 2 * i] for i in range(count)]


@cocotb.test()
async def test_boot_store_runs_uart_with_the_sram_dead(dut):
    """UART transmit from the boot store. On RTL the SRAM's output is forced to ones for the whole test,
    load included, so nothing can come from the macro."""
    await reset(dut)
    if not GATE_LEVEL:
        dut.user_project.sram_dout.value = Force(0xFFFF)
    try:
        await write_reg(dut, 23, 2)
        assert await read_reg(dut, 23) == 2
        bit = 40
        prog = firmware("uart_tx.s", BIT=bit)
        await load_program(dut, prog.words)
        assert await read_program(dut, 0, len(prog.words)) == prog.words, "the host can't read the boot store back"
        data = [0x55, 0xA3, 0x00, 0xFF]
        await fifo_write(dut, data)
        await start(dut)
        trace = await capture(dut, 13 * bit * len(data))
        levels = [(t[0] >> 4) & 1 if (t[1] >> 4) & 1 else 1 for t in trace]
        assert [f[1] for f in uart_frames(levels, bit)] == data
        await stop(dut)
    finally:
        if not GATE_LEVEL:
            dut.user_project.sram_dout.value = Release()
        await write_reg(dut, 23, 0)


@cocotb.test()
async def test_boot_store_mirrors_low_words_and_wraps(dut):
    """Every write below word 64 lands in both memories. In boot mode addresses wrap at 64; out of it the
    SRAM still holds everything that was written."""
    await reset(dut)
    await stop(dut)
    words = [(i * 0x9E37 + 0x4321) & 0xFFFF for i in range(100)]
    await load_program(dut, words)
    assert await read_program(dut, 0, 100) == words
    await write_reg(dut, 23, 2)
    got = await read_program(dut, 0, 100)
    assert got == [words[i % BOOT_WORDS] for i in range(100)], "boot store doesn't mirror words 0-63, or doesn't wrap"
    await load_program(dut, [0x1234], base=70)  # a write above the boot store must not land in it
    assert (await read_program(dut, 6, 1)) == [words[6]]
    await write_reg(dut, 23, 0)
    assert await read_program(dut, 0, 100) == words[:70] + [0x1234] + words[71:]


@cocotb.test()
async def test_boot_store_program_counter_wraps_at_64_words(dut):
    """A 64-word program that runs off its end starts again at word 0 in boot mode, so the line toggles
    every 64 cycles. From the SRAM the same program runs on through 448 words of NOP first."""
    await reset(dut)
    prog = assemble("mode 4, eng\nset 4, 1\nset 4, 0\n" + "nop\n" * (BOOT_WORDS - 3))
    assert len(prog.words) == BOOT_WORDS
    await load_program(dut, prog.words + assemble("nop").words * (512 - BOOT_WORDS))
    periods = {}
    for boot in (1, 0):
        await write_reg(dut, 23, boot << 1)
        await start(dut)
        trace = await capture(dut, 1200)
        await stop(dut)
        rises = [t for t in range(1, len(trace)) if (trace[t][0] >> 4) & 1 and not (trace[t - 1][0] >> 4) & 1]
        periods[boot] = {b - a for a, b in zip(rises, rises[1:])}
    assert periods[1] == {BOOT_WORDS}, periods
    assert periods[0] == {512}, periods
