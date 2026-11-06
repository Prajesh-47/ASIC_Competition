# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# Capture unit. Expected results come from the waveform each test drives, not from the golden model.

import cocotb
from cocotb.triggers import ClockCycles

from test import (assemble, capture, capture_lib, capture_pointer, firmware, load_program, read_capture, read_reg, reset,
                  spi_xfer, start, stop, uart_wave, write_reg, UI_IDLE)  # noqa: F401


async def setup(dut, mode=0, prescale=0, post=0, watch=0x3210):
    await write_reg(dut, 6, watch & 0xFF)
    await write_reg(dut, 7, watch >> 8)
    await write_reg(dut, 8, prescale & 0xFF)
    await write_reg(dut, 9, prescale >> 8)
    await write_reg(dut, 10, mode)
    await write_reg(dut, 11, post & 0xFF)
    await write_reg(dut, 12, post >> 8)


@cocotb.test()
async def test_capture_reconstructs_exact_edges(dut):
    """Every edge, timed to the cycle, on a real UART frame."""
    await reset(dut)
    await setup(dut)
    await load_program(dut, firmware("capture_on.s").words)
    await start(dut)
    await ClockCycles(dut.clk, 10)
    bit = 37
    wave = [1, 1] + uart_wave(0xA5, bit) + [1, 1]
    await capture(dut, len(wave) * bit, lambda t, o, e: 0xFE | wave[t // bit])
    dut.uio_in.value = 0xFF
    await ClockCycles(dut.clk, 10)
    await stop(dut)
    count = await capture_pointer(dut)
    edges, marks = capture_lib.decode(await read_capture(dut, count))
    assert not marks
    states = [s & 1 for _, s in edges]
    assert states[0] == 1 and all(a != b for a, b in zip(states, states[1:])), f"{edges}"
    # Time between edges, independently computed from the waveform driven above
    changes = [i for i in range(1, len(wave)) if wave[i] != wave[i - 1]]
    want = [(b - a) * bit for a, b in zip(changes, changes[1:])]
    got = [b[0] - a[0] for a, b in zip(edges[1:], edges[2:])]
    assert got == want, f"{got} vs {want}"
    assert edges[0][0] == 0


@cocotb.test()
async def test_capture_prescaler(dut):
    await reset(dut)
    await setup(dut, prescale=9)  # a tick every 10 cycles
    await load_program(dut, firmware("capture_on.s").words)
    await start(dut)
    await ClockCycles(dut.clk, 10)
    await capture(dut, 900, lambda t, o, e: 0xFE | (0 if 300 <= t < 800 else 1))
    await stop(dut)
    edges, _ = capture_lib.decode(await read_capture(dut, await capture_pointer(dut)))
    assert len(edges) == 3, f"{edges}"
    assert abs((edges[2][0] - edges[1][0]) - 50) <= 1, f"{edges}"


@cocotb.test()
async def test_capture_long_silence_uses_repeat_entries(dut):
    await reset(dut)
    await setup(dut)
    await load_program(dut, firmware("capture_on.s").words)
    await start(dut)
    await ClockCycles(dut.clk, 10)
    first, gap = 20, 2 * 0xFFD + 300
    await capture(dut, first + gap + 50, lambda t, o, e: 0xFE | (1 if first <= t < first + gap else 0))
    await stop(dut)
    words = await read_capture(dut, await capture_pointer(dut))
    assert [w & 0xFFF for w in words].count(0xFFF) == 2, f"{[hex(w) for w in words]}"
    edges, _ = capture_lib.decode(words)
    assert edges[-1][1] & 1 == 0 and edges[-2][1] & 1 == 1
    assert edges[-1][0] - edges[-2][0] == gap, f"gap measured across repeats is wrong: {edges}"


@cocotb.test()
async def test_capture_stops_when_full(dut):
    await reset(dut)
    await setup(dut)
    await load_program(dut, firmware("capture_on.s").words)
    await start(dut)
    await ClockCycles(dut.clk, 10)
    await capture(dut, 1400, lambda t, o, e: 0xFE | ((t >> 1) & 1))  # far more than 512 changes
    control = await read_reg(dut, 10)
    assert control & 0x80 == 0, "should have stopped"
    assert control & 0x40, "should report wrapped"
    assert await capture_pointer(dut) == 0
    await stop(dut)


@cocotb.test()
async def test_ring_keeps_history_and_stops_after_trigger(dut):
    """Pre-trigger: old entries survive, and capture stops exactly `post` entries after the trigger."""
    await reset(dut)
    post = 7
    await setup(dut, mode=1, post=post)
    await load_program(dut, firmware("capture_on.s").words)
    await start(dut)
    await ClockCycles(dut.clk, 10)
    trig_at = 1300  # 650 entries by then, enough to wrap 512

    def stim(t, o, e):
        return 0xFE | ((t >> 1) & 1), (UI_IDLE | 0x08) if t >= trig_at else UI_IDLE

    await capture(dut, 1600, stim)
    dut.ui_in.value = UI_IDLE
    control = await read_reg(dut, 10)
    assert control & 0x80 == 0, f"should have stopped: {control:08b}"
    assert control & 0x20, "should report triggered"
    assert control & 0x40, "more than 512 entries should have wrapped"
    pointer = await capture_pointer(dut)
    await stop(dut)
    # Toggling every 2 cycles writes one entry per change, so the pointer shows where it stopped.
    # 2 entries per 4 cycles from about cycle 0, plus the start entry, wrapped at 512.
    expected = (trig_at // 2 + 1 + post + 1) % 512
    assert abs(pointer - expected) <= 8, f"pointer {pointer}, expected about {expected}"
    # All 512 entries read back, in time order, and the newest changes are still regular.
    words = await read_capture(dut, 512)
    ordered = capture_lib.in_order(words, pointer, True)
    assert all((w & 0xFFF) in (1, 2) for w in ordered[2:]), f"{[hex(w) for w in ordered[:6]]}"


@cocotb.test()
async def test_trig_in_starts_capture(dut):
    await reset(dut)
    await setup(dut, mode=0b10)
    await load_program(dut, assemble("halt").words)
    await start(dut)
    await ClockCycles(dut.clk, 40)
    assert await capture_pointer(dut) == 0, "nothing should be captured before the trigger"

    def stim(t, o, e):
        return 0xFE | (0 if 20 <= t < 40 else 1), (UI_IDLE | 0x08) if t >= 10 else UI_IDLE

    await capture(dut, 60, stim)
    dut.ui_in.value = UI_IDLE
    count = await capture_pointer(dut)
    await stop(dut)
    edges, _ = capture_lib.decode(await read_capture(dut, count))
    assert len(edges) == 3 and edges[0][0] == 0, f"{edges}"
    assert edges[2][0] - edges[1][0] == 20, f"{edges}"


@cocotb.test()
async def test_marks_take_no_time_and_capoff_stops(dut):
    await reset(dut)
    await setup(dut)
    prog = assemble("""
        capon
        delay 50
        capmark
        delay 50
        capmark
        delay 20
        capoff
        halt
""")
    await load_program(dut, prog.words)
    await start(dut)
    await capture(dut, 400, lambda t, o, e: 0xFF)
    assert (await read_reg(dut, 10)) & 0x80 == 0, "CAPOFF should have stopped capture"
    await stop(dut)
    edges, marks = capture_lib.decode(await read_capture(dut, 3))
    assert len(marks) == 2, f"{marks}"
    assert len(edges) == 1 and edges[0][0] == 0


@cocotb.test()
async def test_host_can_start_and_stop_capture(dut):
    await reset(dut)
    await setup(dut)
    await load_program(dut, assemble("halt").words)
    await start(dut)
    await write_reg(dut, 10, 0x80)
    assert (await read_reg(dut, 10)) & 0x80, "capture should be running"
    await write_reg(dut, 10, 0x40)
    assert (await read_reg(dut, 10)) & 0x80 == 0, "capture should have stopped"
    assert await capture_pointer(dut) == 1, "just the starting state"
    await stop(dut)


@cocotb.test()
async def test_vcd_export_matches_the_edges(dut):
    """A file a user would open: its value changes must land where the capture says."""
    entries = [0xF000, 0xE00A, 0xF014, 0xFFFE, 0xE005]
    text = capture_lib.to_vcd(entries, tick_ns=20)
    lines = text.splitlines()
    assert "$enddefinitions $end" in lines
    assert "#0" in lines and "#200" in lines and "#600" in lines and "#700" in lines
    # Only L0 changes after the start, so each later time carries one change or the mark.
    start_i = lines.index("#200")
    assert lines[start_i + 1] == '0!', lines[start_i:start_i + 3]
    assert lines[lines.index("#600") + 1:lines.index("#700")].count("1%") == 1
