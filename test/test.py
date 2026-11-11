# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# SPLICE tests. They run on RTL and on the gate-level netlist (GATES=yes), so they only touch
# top-level ports. Programs reach the chip the way a real host sends them: over the SPI port.

import os
import random
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "model"))

from splice.asm import assemble  # noqa: E402
from splice.isa import COND, PHASE, SRC, TABLE, WAITC, WANT, encode  # noqa: E402
from splice.model import Splice  # noqa: E402

sys.path.insert(0, os.path.join(HERE, "..", "host"))
from splice_host import capture as capture_lib  # noqa: E402

GATE_LEVEL = os.environ.get("GATES") == "yes"
HALF = 5  # SPI half period in core clocks: SCK is core/10
UI_IDLE = 0xF4  # AUX_IN high, CS high, SCK and MOSI low


def firmware(name, **defines):
    with open(os.path.join(HERE, "..", "model", "firmware", name), encoding="utf-8") as source:
        return assemble(source.read(), defines)


QUAD = False  # host register 23: 4 bits each way per SCK edge, on IO0 = MOSI/MISO and IO1-3 = AUX 0-2


def quad_ui(ui_high, nib):
    """ui_in for one quad nibble: IO0 on ui[1], IO1-3 on ui[4..6], ui[7] and CS from ui_high."""
    return (ui_high & 0x80) | ((nib >> 1) & 7) << 4 | (nib & 1) << 1


def quad_uo(dut):
    value = str(dut.uo_out.value)
    assert all(c in "01" for c in value), f"quad read sees uo_out={value}"
    uo = int(value, 2)
    return ((uo >> 4) & 7) << 1 | (uo & 1)


async def quad_byte(dut, byte, ui_high):
    value = 0
    for nib in (byte >> 4, byte & 15):
        dut.ui_in.value = quad_ui(ui_high, nib)
        await ClockCycles(dut.clk, HALF)
        value = value << 4 | quad_uo(dut)
        dut.ui_in.value = quad_ui(ui_high, nib) | 1
        await ClockCycles(dut.clk, HALF)
    dut.ui_in.value = quad_ui(ui_high, byte & 15)
    return value


async def spi_xfer(dut, data, ui_high=0xF0):
    """One SPI transaction, mode 0, MSB first. Returns the bytes clocked back. Quad when QUAD is set."""
    out = []
    dut.ui_in.value = ui_high  # CS low
    await ClockCycles(dut.clk, HALF)
    for byte in data:
        if QUAD:
            out.append(await quad_byte(dut, byte, ui_high))
            continue
        value = 0
        for k in range(7, -1, -1):
            bit = (byte >> k) & 1
            dut.ui_in.value = ui_high | (bit << 1)
            await ClockCycles(dut.clk, HALF)
            miso = str(dut.uo_out.value)[-1]  # uo[0] only: the other pins aren't part of this
            assert miso in "01", f"MISO is {miso} during a host read, uo_out={dut.uo_out.value}"
            value = (value << 1) | int(miso)
            dut.ui_in.value = ui_high | (bit << 1) | 1
            await ClockCycles(dut.clk, HALF)
        dut.ui_in.value = ui_high | (byte & 1) << 1
        out.append(value)
    await ClockCycles(dut.clk, HALF)
    dut.ui_in.value = ui_high | 0x04  # CS high
    await ClockCycles(dut.clk, 2 * HALF)
    return out


def word_bytes(words):
    return [b for w in words for b in (w >> 8, w & 0xFF)]


async def load_program(dut, words, base=0):
    await spi_xfer(dut, [0x01, base >> 8, base & 0xFF] + word_bytes(words))


async def write_reg(dut, reg, value):
    await spi_xfer(dut, [0x05, reg, value])


async def read_reg(dut, reg):
    return (await spi_xfer(dut, [0x06, reg, 0]))[2]


async def read_capture(dut, count, base=0):
    data = await spi_xfer(dut, [0x04, base >> 8, base & 0xFF] + [0] * (2 * count))
    return [data[3 + 2 * i] << 8 | data[4 + 2 * i] for i in range(count)]


async def capture_pointer(dut):
    return (await read_reg(dut, 14)) << 8 | await read_reg(dut, 13)


async def fifo_write(dut, words):
    await spi_xfer(dut, [0x20] + word_bytes(words))


async def fifo_read(dut, count):
    data = await spi_xfer(dut, [0x21] + [0] * (2 * count))
    return [data[1 + 2 * i] << 8 | data[2 + 2 * i] for i in range(count)]


async def start(dut):
    await spi_xfer(dut, [0x10])


async def stop(dut):
    await spi_xfer(dut, [0x11])


async def status(dut):
    return (await spi_xfer(dut, [0x12, 0]))[1]


async def levels(dut):
    """STATUS with its two extra bytes: (status, engine-to-host words waiting, host-to-engine room)."""
    data = await spi_xfer(dut, [0x12, 0, 0, 0])
    return data[1], data[2], data[3]


async def fifo_xfer(dut, words=(), slots=None):
    """XFER: pushes words while popping. Returns (level, free, words read), keeping only the words the
    level said were there."""
    nw = len(words)
    slots = nw if slots is None else max(slots, nw)
    data = await spi_xfer(dut, [0x22, nw, 0] + word_bytes(list(words) + [0] * (slots - nw)))
    level, free = data[1], data[2]
    got = [data[3 + 2 * i] << 8 | data[4 + 2 * i] for i in range(slots)]
    return level, free, got[:min(level, slots)], got


async def reset(dut):
    clock = Clock(dut.clk, 20, unit="ns")  # 50 MHz, matches info.yaml
    cocotb.start_soon(clock.start())
    dut.ena.value = 1
    dut.ui_in.value = UI_IDLE
    dut.uio_in.value = 0xFF
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    # The FIFO depth is a build choice. Right after reset the room reported is the whole FIFO, and the
    # golden model is told the same, so the two can't silently differ.
    Splice.FIFO_DEPTH = (await spi_xfer(dut, [0x12, 0, 0, 0]))[3]


async def capture(dut, cycles, stim=None):
    """Sample every clock. stim(t, uio_out, uio_oe) returns uio_in, and optionally ui_in."""
    trace = []
    for t in range(cycles):
        await FallingEdge(dut.clk)
        out, oe = int(dut.uio_out.value), int(dut.uio_oe.value)
        if stim:
            levels = stim(t, out, oe)
            if isinstance(levels, tuple):
                dut.uio_in.value, dut.ui_in.value = levels
            else:
                dut.uio_in.value = levels
        trace.append((out, oe, int(dut.uo_out.value)))
    return trace


def uart_frames(levels, bit):
    """Decode by sampling mid-bit, as a real receiver does. Returns (start cycle, byte, stop)."""
    frames, t = [], 0
    while t + 10 * bit < len(levels):
        if levels[t] or levels[t + bit // 2]:  # a real receiver checks the middle of the start bit
            t += 1
            continue
        value = 0
        for k in range(8):
            value |= levels[t + bit // 2 + (k + 1) * bit] << k
        frames.append((t, value, levels[t + bit // 2 + 9 * bit]))
        t += bit // 2 + 9 * bit
    return frames


def uart_wave(byte, bit):
    return [0] + [(byte >> k) & 1 for k in range(8)] + [1]


# ------------------------------------------------------------------ Reset and safety
@cocotb.test()
async def test_reset_state(dut):
    """After reset every line is released and nothing is running."""
    await reset(dut)
    assert dut.uio_oe.value == 0, "lines must be hi-Z out of reset"
    assert dut.uio_out.value == 0
    assert int(dut.uo_out.value) == 0
    # Not running, nothing halted, no IRQ, and both FIFOs in their empty state
    assert await status(dut) == 0b00010000


@cocotb.test()
async def test_lines_stay_hiz_until_configured(dut):
    """Bus traffic must not make an unconfigured SPLICE drive anything."""
    await reset(dut)
    for value in (0x00, 0xFF, 0xA5, 0x5A):
        dut.uio_in.value = value
        await ClockCycles(dut.clk, 4)
        assert dut.uio_oe.value == 0


@cocotb.test()
async def test_lines_hiz_during_reset_and_after_stop(dut):
    """Hi-Z is a permanent requirement, so check it during reset too, not only afterwards."""
    await reset(dut)
    await load_program(dut, assemble("mode 0, f1\nmode 4, eng\nset 4, 1\nhalt").words)
    await start(dut)
    await ClockCycles(dut.clk, 10)
    assert int(dut.uio_oe.value) == 0x11, "the program should be driving lines 0 and 4"
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 2)  # one edge to register reset, one to settle
    for _ in range(8):
        await FallingEdge(dut.clk)
        assert int(dut.uio_oe.value) == 0, f"line driven during reset: {dut.uio_oe.value}"
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 3)
    assert int(dut.uio_oe.value) == 0
    await load_program(dut, assemble("mode 0, f1\nhalt").words)
    await start(dut)
    await ClockCycles(dut.clk, 10)
    assert int(dut.uio_oe.value) == 0x01
    await stop(dut)
    assert int(dut.uio_oe.value) == 0, "STOP must release every line"


# ------------------------------------------------------------------ Instruction results the pins can't show
@cocotb.test()
async def test_rand_sequence_and_seed(dut):
    """Fuzzing depends on a repeatable sequence, so check it against an independent LFSR."""
    await reset(dut)
    prog = assemble("""
        rand r0
        mov  sr, r0
        push
        rand r0
        mov  sr, r0
        push
        ldi  r1, 0x1234
        mov  seed, r1
        rand r0
        mov  sr, r0
        push
        halt
""")
    await load_program(dut, prog.words)
    await start(dut)
    await ClockCycles(dut.clk, 30)

    def step(state):
        return (state >> 1) ^ (0xB400 if state & 1 else 0)
    first = step(0xACE1)
    assert await fifo_read(dut, 3) == [first, step(first), step(0x1234)]


@cocotb.test()
async def test_missed_tick_sets_m_not_f(dut):
    """A timer nobody consumes used to set F, which also means a full FIFO and a timeout. It sets M now,
    which JMP M reads and clears."""
    await reset(dut)
    prog = assemble("""
        tset 5            ; ticks every 5 cycles, and nothing consumes them
        dly  40
        sri  0x22
        push              ; room in the FIFO, so F must be clear
        jmp  f, bad
        tset 0            ; stop the timer, or the next tick would set M again
        jmp  m, seen
        jmp  bad
seen:   jmp  m, bad       ; the read cleared it
        set  8, 1
        halt
bad:    set  9, 1
        halt
""")
    await load_program(dut, prog.words)
    await start(dut)
    await ClockCycles(dut.clk, 120)
    aux = int(dut.uo_out.value) >> 4
    assert aux == 0b0001, f"AUX_OUT={aux:04b}"


@cocotb.test()
async def test_push_to_full_fifo_sets_f(dut):
    """Data lost to a full FIFO has to be visible to firmware."""
    await reset(dut)
    depth = (await levels(dut))[2]  # all room, right after reset
    prog = assemble("""
        sri  0x11
""" + "        push\n" * depth + """
        jmp  f, early     ; a full FIFO's worth fits
        push
        jmp  f, good      ; one more doesn't
        halt
good:   set  8, 1
        halt
early:  set  9, 1
        halt
""")
    await load_program(dut, prog.words)
    await start(dut)
    await ClockCycles(dut.clk, 30 + depth)
    aux = int(dut.uo_out.value) >> 4
    assert aux == 0b0001, f"AUX_OUT={aux:04b}"
    assert await fifo_read(dut, depth) == [0x11] * depth


# ------------------------------------------------------------------ Memories through the host port
@cocotb.test()
async def test_program_memory_roundtrip(dut):
    await reset(dut)
    words = [0x1234, 0xFFFF, 0x0000, 0xA55A, 0x8001, 0x7FFE]
    await load_program(dut, words, base=250)  # crosses nothing, ends at the last word
    data = await spi_xfer(dut, [0x02, 0, 250] + [0] * (2 * len(words)))
    got = [data[3 + 2 * i] << 8 | data[4 + 2 * i] for i in range(len(words))]
    assert got == words, f"{got}"


@cocotb.test()
async def test_capture_memory_roundtrip(dut):
    await reset(dut)
    words = [0xBEEF, 0x0001, 0x8000]
    await spi_xfer(dut, [0x03, 1, 0xFE] + word_bytes(words[:2]))  # the top of 512 words
    await spi_xfer(dut, [0x03, 0, 0x00] + word_bytes(words[2:]))
    data = await spi_xfer(dut, [0x04, 1, 0xFE] + [0] * 4)
    assert [data[3] << 8 | data[4], data[5] << 8 | data[6]] == words[:2]
    data = await spi_xfer(dut, [0x04, 0, 0x00] + [0] * 2)
    assert data[3] << 8 | data[4] == words[2]


@cocotb.test()
async def test_empty_fifo_reads_as_zero(dut):
    await reset(dut)
    assert await fifo_read(dut, 2) == [0, 0]


@cocotb.test()
async def test_host_registers(dut):
    await reset(dut)
    await write_reg(dut, 1, 0x30)
    await write_reg(dut, 3, 0x2A)
    await write_reg(dut, 9, 0x5B)
    assert await read_reg(dut, 1) == 0x30
    assert await read_reg(dut, 3) == 0x2A
    assert await read_reg(dut, 9) == 0x5B


# ------------------------------------------------------------------ Reprogrammed protocols
@cocotb.test()
async def test_fifo_read_consumes_exactly_what_it_reads(dut):
    """Ending a read must leave the rest queued. Status tells empty apart from a stored 0."""
    await reset(dut)
    prog = assemble("""
        pullw 16
        push
        pullw 16
        push
        pullw 16
        push
        halt
""")
    await load_program(dut, prog.words)
    await start(dut)
    await fifo_write(dut, [0x1111, 0x0000, 0x3333])
    await ClockCycles(dut.clk, 100)
    for expected in (0x1111, 0x0000, 0x3333):
        assert not (await status(dut)) & 0x10, f"{expected:#x} should still be queued"
        await spi_xfer(dut, [0x21])  # the command alone reads nothing, so it consumes nothing
        assert (await fifo_read(dut, 1)) == [expected]
    assert (await status(dut)) & 0x10, "FIFO should now be empty"


ECHO_AFTER_FALL = """
        ldi     r1, 0
        mov     to, r1        ; no timeout
        wait    0, fall       ; the test releases the echo by pulling line 0 low
loop:   pullw   16
        push
        jmp     loop
"""


@cocotb.test()
async def test_status_reports_fifo_levels(dut):
    """Old hosts read one STATUS byte. Two more give the level each way, exactly."""
    await reset(dut)
    st, level, depth = await levels(dut)
    assert (st, level) == (0x10, 0) and depth >= 4 and depth & (depth - 1) == 0, f"{st:#x} {level} {depth}"
    prog = assemble("        sri 0x11\n" + "        push\n" * 3 + "        halt\n")
    await load_program(dut, prog.words)
    await start(dut)
    await ClockCycles(dut.clk, 30)
    await fifo_write(dut, [1, 2])  # halted, so nothing pulls them
    _, level, free = await levels(dut)
    assert (level, free) == (3, depth - 2), f"level {level}, free {free}"
    assert await status(dut) & 0x10 == 0, "the one-byte STATUS is unchanged"


@cocotb.test()
async def test_xfer_pushes_and_pops_in_one_transaction(dut):
    """Words go both ways at once. A stored 0 counts as data, and a word that arrives during a burst
    waits for the next one instead of being lost."""
    await reset(dut)
    depth = (await levels(dut))[2]
    await load_program(dut, assemble(ECHO_AFTER_FALL).words)
    await start(dut)
    level, free, got, _ = await fifo_xfer(dut, [0x1111, 0x0000, 0x3333], slots=4)
    assert (level, free, got) == (0, depth, []), f"{level} {free} {got}"
    dut.uio_in.value = 0xFE
    await ClockCycles(dut.clk, 50)
    assert (await levels(dut))[1:] == (3, depth)
    level, free, got, raw = await fifo_xfer(dut, [0x4444], slots=5)
    assert (level, got) == (3, [0x1111, 0x0000, 0x3333]), f"{level} {[hex(w) for w in got]}"
    assert raw[3:] == [0, 0], "slots past the level read as 0"
    await ClockCycles(dut.clk, 50)
    level, free, got, _ = await fifo_xfer(dut, slots=2)
    assert (level, got) == (1, [0x4444]), f"pushed in the same XFER as the reads: {level} {got}"
    assert (await levels(dut))[1:] == (0, depth)


@cocotb.test()
async def test_xfer_pops_only_what_it_clocks(dut):
    """A burst shorter than the level leaves the rest queued, in order."""
    await reset(dut)
    await load_program(dut, assemble(ECHO_AFTER_FALL).words)
    await start(dut)
    dut.uio_in.value = 0xFE
    await fifo_write(dut, [0xA001])
    await ClockCycles(dut.clk, 20)
    await fifo_write(dut, [0xA002, 0xA003])
    await ClockCycles(dut.clk, 50)
    level, _, got, _ = await fifo_xfer(dut, slots=1)
    assert (level, got) == (3, [0xA001]), f"{level} {got}"
    level, _, got, _ = await fifo_xfer(dut, slots=3)
    assert (level, got) == (2, [0xA002, 0xA003]), f"{level} {got}"


@cocotb.test()
async def test_xfer_into_full_fifo_keeps_the_first_words(dut):
    """Words past the room are dropped, and the free space shows it. The ones that fit arrive intact."""
    await reset(dut)
    depth = (await levels(dut))[2]
    await load_program(dut, assemble(ECHO_AFTER_FALL).words)
    await start(dut)
    sent = [0xB000 + i for i in range(depth + 2)]
    await fifo_xfer(dut, sent)
    assert (await levels(dut))[2] == 0
    dut.uio_in.value = 0xFE
    out = []
    for _ in range(6):
        await ClockCycles(dut.clk, 40 * depth)
        out += (await fifo_xfer(dut, slots=depth))[2]
    assert out == sent[:depth], f"{[hex(w) for w in out]}"


@cocotb.test()
async def test_xfer_ended_early(dut):
    """Ending after the header moves nothing. Ending inside a slot pushes nothing, and spends that slot's
    engine word, as FIFO_RD does: its first bit is already on MISO."""
    await reset(dut)
    depth = (await levels(dut))[2]
    await load_program(dut, assemble(ECHO_AFTER_FALL).words)
    await start(dut)
    dut.uio_in.value = 0xFE
    await fifo_write(dut, [0xC001, 0xC002, 0xC003])
    await ClockCycles(dut.clk, 80)
    await spi_xfer(dut, [0x22, 1, 0])
    assert (await levels(dut))[1:] == (3, depth), "header only"
    await spi_xfer(dut, [0x22, 1, 0, 0xD0])  # one slot, cut after the high byte
    await ClockCycles(dut.clk, 60)
    level, _, got, _ = await fifo_xfer(dut, slots=3)
    assert (level, got) == (2, [0xC002, 0xC003]), f"{level} {[hex(w) for w in got]}"


@cocotb.test()
async def test_uart_tx_loaded_over_spi(dut):
    """The first end-to-end milestone: a program loaded after reset sends exact UART frames."""
    await reset(dut)
    bit = 40
    prog = firmware("uart_tx.s", BIT=bit)
    await load_program(dut, prog.words)
    data = [0x55, 0xA3, 0x00, 0xFF]
    await fifo_write(dut, data)
    await start(dut)
    trace = await capture(dut, 13 * bit * len(data))
    levels = [(t[0] >> 4) & 1 if (t[1] >> 4) & 1 else 1 for t in trace]
    frames = uart_frames(levels, bit)
    assert [f[1] for f in frames] == data, f"{[hex(f[1]) for f in frames]}"
    assert all(f[2] == 1 for f in frames), "bad stop bit"
    for frame_start, _, _ in frames:
        window = levels[frame_start: frame_start + 10 * bit]
        edges = [i for i in range(1, len(window)) if window[i] != window[i - 1]]
        assert all(e % bit == 0 for e in edges), f"edge off the bit grid: {edges}"


@cocotb.test()
async def test_pass_through_is_identity_with_exact_latency(dut):
    """Random traffic, pulses of one cycle included, through all four pairs in both directions.
    Latency comes from the spec (section 4.2), not from the model: 3 cycles, or 2 on a fast line."""
    await reset(dut)
    prog = assemble("""
        mode 4, pass
        mode 5, inv
        mode 2, pass
        mode 3, inv
top:    jmp  top
""")
    partner = {4: (0, 0), 5: (1, 1), 2: (6, 0), 3: (7, 1)}  # output line: its input line, inverted or not
    rng = random.Random(7)
    for fast in (0x00, 0xFF, 0x14):
        await stop(dut)
        await load_program(dut, prog.words)
        await write_reg(dut, 2, fast)
        await start(dut)
        await ClockCycles(dut.clk, 12)
        wave, value = [], 0xFF
        for _ in range(3000):
            value ^= rng.getrandbits(8) & rng.getrandbits(8)  # about a quarter of the lines toggle each cycle
            wave.append(value)
        wave += [wave[-1]] * 6
        trace = await capture(dut, len(wave), lambda t, out, oe: wave[t])
        toggles = {line: 0 for line in partner}
        for line, (source, inverted) in partner.items():
            latency = 2 if (fast >> line) & 1 else 3
            for t in range(10, len(wave)):
                out, oe, _ = trace[t]
                assert (oe >> line) & 1, f"line {line} released at {t}"
                expected = ((wave[t - latency] >> source) & 1) ^ inverted
                assert (out >> line) & 1 == expected, f"line {line}, fast {fast:#x}, cycle {t}"
                toggles[line] += ((wave[t - latency] ^ wave[t - latency - 1]) >> source) & 1
        assert all(count > 300 for count in toggles.values()), f"too little traffic: {toggles}"
    await stop(dut)
    await write_reg(dut, 2, 0)


@cocotb.test()
async def test_contexts_keep_their_own_return_address(dut):
    """Both contexts inside a CALL at the same time. Each must return to its own call site."""
    await reset(dut)
    prog = assemble("""
        set   4, 0
        mode  4, eng
        call  slow
        set   4, 1
        halt
slow:   delay 100
        ret
other:  delay 10
        set   5, 0
        mode  5, eng
        call  quick
        set   5, 1
        halt
quick:  delay 10
        ret
.ctx1 other
""")
    await load_program(dut, prog.words)
    await write_reg(dut, 0, 1)
    await write_reg(dut, 4, prog.ctx1 >> 8)
    await write_reg(dut, 5, prog.ctx1 & 0xFF)
    await start(dut)
    trace = await capture(dut, 80)
    assert not (trace[-1][0] >> 4) & 1, "context 0 returned early, so it took context 1's return address"
    assert (trace[-1][0] >> 5) & 1, "context 1 didn't reach its own call site"
    trace = await capture(dut, 300)
    assert (trace[-1][0] >> 4) & 1, "context 0 didn't reach its own call site"
    await stop(dut)
    await write_reg(dut, 0, 0)


@cocotb.test()
async def test_same_netlist_runs_other_protocols(dut):
    """No reset and no RTL change: load a different program each time, on the same running chip."""
    await reset(dut)

    # UART transmit
    bit = 40
    await load_program(dut, firmware("uart_tx.s", BIT=bit).words)
    await fifo_write(dut, [0x5A])
    await start(dut)
    trace = await capture(dut, 12 * bit)
    levels = [(t[0] >> 4) & 1 if (t[1] >> 4) & 1 else 1 for t in trace]
    assert [f[1] for f in uart_frames(levels, bit)] == [0x5A]
    await stop(dut)

    # SPI controller, full duplex, against a target model
    half = 10
    await load_program(dut, firmware("spi_master.s", HALF=half).words)
    sent, reply = [0x96, 0x3C], [0xC3, 0x5A]
    await fifo_write(dut, [b << 8 for b in sent])
    await start(dut)
    st = {"bits": 0, "psck": 0, "mosi": []}

    def spi_target(t, out, oe):
        sck = (out >> 4) & 1 if (oe >> 4) & 1 else 0
        if sck and not st["psck"]:
            st["mosi"].append((out >> 5) & 1)
        if st["psck"] and not sck:
            st["bits"] += 1
        st["psck"] = sck
        i = st["bits"]
        miso = (reply[i // 8] >> (7 - i % 8)) & 1 if i // 8 < len(reply) else 1
        return (miso << 7) | 0x7F

    await capture(dut, 2 * half * 16 + 100, spi_target)
    got = await fifo_read(dut, 2)
    assert [w & 0xFF for w in got] == reply, f"{[hex(w) for w in got]}"
    sampled = [int("".join(map(str, st["mosi"][k:k + 8])), 2) for k in (0, 8)]
    assert sampled == sent, f"{sampled}"
    await stop(dut)
    dut.uio_in.value = 0xFF

    # UART receive
    bit = 40
    await load_program(dut, firmware("uart_rx.s", BIT=bit).words)
    await start(dut)
    data = [0xA7, 0x18]
    wave = [v for byte in data for v in uart_wave(byte, bit) + [1]]
    await capture(dut, len(wave) * bit + 50, lambda t, o, e: 0xFE | (wave[t // bit] if t // bit < len(wave) else 1))
    assert [w >> 8 for w in await fifo_read(dut, 2)] == data
    await stop(dut)

    # I2C controller on open-drain lines, against a target model that ACKs
    await write_reg(dut, 1, 0x30)
    q = 12
    await load_program(dut, firmware("i2c_master.s", Q=q).words)
    await fifo_write(dut, [0xA2 << 8, 0x5C << 8])
    await start(dut)
    i2c = {"pscl": 1, "psda": 1, "bits": [], "bytes": [], "ack": False, "go": False, "ev": []}

    def i2c_target(t, out, oe):
        scl = 0 if (oe >> 4) & 1 else 1
        sda_m = 0 if (oe >> 5) & 1 else 1
        if scl and i2c["pscl"] and i2c["psda"] and not sda_m:
            i2c.update(go=True, bits=[], ack=False)
            i2c["ev"].append("S")
        if scl and i2c["pscl"] and not i2c["psda"] and sda_m:
            i2c["ev"].append("P")
        if i2c["go"]:
            if scl and not i2c["pscl"] and not i2c["ack"]:
                i2c["bits"].append(sda_m)
                if len(i2c["bits"]) == 8:
                    i2c["bytes"].append(int("".join(map(str, i2c["bits"])), 2))
            if i2c["pscl"] and not scl:
                if i2c["ack"]:
                    i2c.update(ack=False, bits=[])
                elif len(i2c["bits"]) == 8:
                    i2c["ack"] = True
        sda = sda_m & (0 if i2c["ack"] else 1)
        i2c["pscl"], i2c["psda"] = scl, sda_m
        return 0xCF | (sda << 5) | (scl << 4)

    await capture(dut, q * 100, i2c_target)
    assert i2c["bytes"] == [0xA2, 0x5C], f"{i2c['bytes']}"
    assert i2c["ev"] == ["S", "P"], f"{i2c['ev']}"
    await stop(dut)
    await write_reg(dut, 1, 0)


@cocotb.test()
async def test_uart_bit_flip_inline(dut):
    """Interposing: pass traffic from line 0 to line 4, and flip one chosen bit of every frame."""
    await reset(dut)
    bit, k = 40, 3
    await load_program(dut, firmware("uart_flip.s", BIT=bit, K=k).words)
    await start(dut)
    data = [0x55, 0xA3, 0xFF]
    wave = [v for byte in data for v in uart_wave(byte, bit) + [1, 1]]
    trace = await capture(dut, len(wave) * bit + 20,
                          lambda t, o, e: 0xFE | (wave[t // bit] if t // bit < len(wave) else 1))
    levels = [(t[0] >> 4) & 1 if (t[1] >> 4) & 1 else 1 for t in trace]
    got = [f[1] for f in uart_frames(levels, bit)]
    assert got == [b ^ (1 << k) for b in data], f"{[hex(g) for g in got]}"


@cocotb.test()
async def test_two_contexts_run_independently(dut):
    """Two programs at half rate: one sends UART while the other flips bits on another line pair."""
    await reset(dut)
    bit, k = 40, 2
    source = f"""
.equ BIT, {bit}
        set   4, 1
        mode  4, eng
        tset  BIT
tx:     pullw 8
        set.t 4, 0
b:      outb.t 4, b
        set.t 4, 1
        jmp   tx
ctx1:   mode  5, pass
        tset  BIT
        ldi   r1, ({k} + 1) * BIT - 1
top:    wait  1, fall
        tphase r1
        mode.t 5, inv
        mode.t 5, pass
        delay  (19 * BIT / 2 - ({k} + 2) * BIT) / 2
        jmp   top
.ctx1 ctx1
"""
    prog = assemble(source)
    await load_program(dut, prog.words)
    await write_reg(dut, 0, 1)
    await write_reg(dut, 4, prog.ctx1 >> 8)
    await write_reg(dut, 5, prog.ctx1 & 0xFF)
    await fifo_write(dut, [0x5A, 0xC3])
    await start(dut)
    inbound = [0xA6]
    wave = [v for byte in inbound for v in uart_wave(byte, bit) + [1]]
    trace = await capture(dut, 26 * bit, lambda t, o, e: 0xFD | ((wave[t // bit] if t // bit < len(wave) else 1) << 1))
    tx = [(t[0] >> 4) & 1 if (t[1] >> 4) & 1 else 1 for t in trace]
    flipped = [(t[0] >> 5) & 1 if (t[1] >> 5) & 1 else 1 for t in trace]
    assert [f[1] for f in uart_frames(tx, bit)] == [0x5A, 0xC3]
    assert [f[1] for f in uart_frames(flipped, bit)] == [0xA6 ^ (1 << k)]
    await stop(dut)
    await write_reg(dut, 0, 0)


@cocotb.test()
async def test_register_timer_period_past_13_bits(dut):
    """ADDR builds a period of 20,833 cycles (DALI's half bit) and TSETR loads it. TSET stops at 8,191.

    Random programs run 800 cycles, so they never see a period this long tick.
    """
    await reset(dut)
    period = 20833
    prog = assemble(f"ldi r1, 10000\nldi r2, {period - 10000}\nadd r1, r2\ntset r1\n"
                    "top: set.t 8, 1\n set.t 8, 0\n jmp top")
    await stop(dut)
    await load_program(dut, prog.words)
    await write_reg(dut, 0, 0)
    await start(dut)
    rises, was = [], 0
    for t in range(5 * period):
        await FallingEdge(dut.clk)
        now = (int(dut.uo_out.value) >> 4) & 1
        if now and not was:
            rises.append(t)
        was = now
    await stop(dut)
    gaps = [b - a for a, b in zip(rises, rises[1:])]
    assert len(gaps) >= 1 and all(g == 2 * period for g in gaps), f"rising edges at {rises}"


def _aux_edges(levels):
    """Times of AUX0 rising, AUX0 falling and AUX1 falling, from uo_out samples."""
    events, prev = {}, None
    for t, uo in enumerate(levels):
        if prev is not None:
            for name, bit, rise in (("aux0_rise", 4, 1), ("aux0_fall", 4, 0), ("aux1_fall", 5, 0)):
                if name not in events and ((uo >> bit) & 1) == rise and ((prev >> bit) & 1) != rise:
                    events[name] = t
        prev = uo
    return events


@cocotb.test()
async def test_timer_loads_clear_pending_and_use_all_bits(dut):
    """TSETR drops a tick already pending, and TPHASE from R1 takes a value over 14 bits.

    Both survived as mutants: random programs rarely reach them. Timing is checked against the model.
    """
    await reset(dut)
    source = ("set 9, 1\n tset 2\n dly 20\n"      # a tick is now pending
              "ldi r1, 20833\n tset r1\n"            # must drop it
              "set 9, 0\n set.t 8, 1\n"              # so this waits a full period
              "ldi r1, 30000\n tphase r1\n"          # over 14 bits
              "set.t 8, 0\n halt")
    words = assemble(source).words
    cycles = 55000

    model = Splice()
    model.load(words)
    model.start()
    want = _aux_edges([model.step(UI_IDLE, 0xFF)["uo"] for _ in range(cycles)])

    await stop(dut)
    await load_program(dut, words)
    await write_reg(dut, 0, 0)
    await start(dut)
    levels = []
    for _ in range(cycles):
        await FallingEdge(dut.clk)
        levels.append(int(dut.uo_out.value))
    await stop(dut)
    got = _aux_edges(levels)

    assert len(want) == 3 and len(got) == 3, f"model {want}, RTL {got}"
    assert abs(want["aux0_fall"] - want["aux0_rise"] - 30000) < 8, f"model itself is off: {want}"
    rel = lambda e: (e["aux0_rise"] - e["aux1_fall"], e["aux0_fall"] - e["aux0_rise"])  # noqa: E731
    assert rel(got) == rel(want), f"RTL {rel(got)}, model {rel(want)}"


@cocotb.test()
async def test_contexts_keep_their_own_crc_polynomial(dut):
    """Both contexts CRC the same byte with different polynomials. A shared polynomial survived as a mutant."""
    await reset(dut)

    def half(poly):
        return (f"ldi r1, {poly}\n poly r1\n ldi r0, 0\n msb 1\n sri 0xA5, 8\n bci 8\n"
                "lp{p}: out 15\n crc r0, msb\n jmp bcn, lp{p}\n mov sr, r0\n push\n halt\n").replace("{p}", str(poly))
    prog = assemble(half(0x1021) + ".ctx1 second\nsecond: " + half(0x8005))
    model = Splice()
    model.load(prog.words)
    model.two, model.ctx1_pc = True, prog.ctx1
    model.start()
    for _ in range(400):
        model.step(UI_IDLE, 0xFF)
    want = sorted(model.e2h)
    assert len(want) == 2 and want[0] != want[1], f"model gave {want}"

    await stop(dut)
    await load_program(dut, prog.words)
    await write_reg(dut, 0, 1)  # two contexts
    await write_reg(dut, 5, prog.ctx1)
    await start(dut)
    await ClockCycles(dut.clk, 400)
    got = sorted(await fifo_read(dut, 2))
    await stop(dut)
    await write_reg(dut, 0, 0)
    assert got == want, f"RTL {[hex(w) for w in got]}, model {[hex(w) for w in want]}"


# ------------------------------------------------------------------ RTL against the golden model
MODEL_ONLY = set()  # explore/isa-lanes-table: instructions in the model but not yet in RTL
PARK_LINE = 11  # AUX_IN3: every random program waits for this line before doing anything else
MASK_UO = 0xFE  # uo[0] is the host's MISO, which the model doesn't include
COVERAGE = {}
STATE_CHECK_EVERY = 16  # reading every register through the simulator is slow, and errors persist


def random_program(rng, length, ctx1):
    """Valid random programs that stay in bounds, so no read is undefined. ctx1 is one start address,
    or a list of them for contexts 1 up, and each start parks too."""
    starts = set(ctx1) if isinstance(ctx1, (list, tuple)) else {ctx1}
    park = encode("WAIT", line=PARK_LINE, c=0, stamp=0)
    words = [park, encode("TSET", p=rng.randrange(1, 30))]
    words += [encode("LDI", r=0, hl=0, v=rng.randrange(1, 40)), encode("MOV", d=6, s=0)]  # set a timeout
    if rng.random() < 0.7:
        words.append(encode("CAPON"))
    # Straight-line code that always runs, so the variant bins don't depend on random code reaching them.
    # Without it, each new opcode shuffled which bins went empty. Over 80 programs, missing one of these
    # variants has odds of about one in a million.
    words += [encode("MODE", line=rng.randrange(8), src=rng.randrange(7), tick=0, dly=0),
              encode("TPHASE", mode=rng.randrange(4)),
              encode("TSRC", line=rng.randrange(12), want=rng.randrange(4))]
    # CRCW at each width: random code reached it at 4 lanes zero times in 80 programs once 4 contexts came in
    words += [encode("LANES", w=rng.randrange(4)),
              encode("CRC", r=rng.randrange(4), dir=rng.randrange(2), word=1, lane=rng.randrange(8))]
    choices = [op for op in TABLE if op not in MODEL_ONLY]
    while len(words) < length - 1:
        pc = len(words)
        if pc in starts:
            words.append(park)
            continue
        if rng.random() < 0.02:  # reserved encodings must behave as NOP
            words.append(rng.choice([0xC400, 0xC800, 0xCC00, 0xA800, 0xB000, 0xE5C0, 0xF000, 0xF7FF, 0xFFFF]))
            continue
        op = rng.choice(choices)
        chance = rng.random()
        if chance < 0.03:  # uniform choice left two coverage bins empty: TSRC on falling edges, JMP on BC != 0
            op = "TSRC"
        elif chance < 0.08:
            op = "BCI"
        elif chance < 0.16:
            # Jumps own 15 of the coverage bins but were drawn like any other opcode, so each new opcode
            # thinned them until some went empty. A fixed share keeps them independent of the opcode count.
            op = "JMP"
        if op == "HALT" and rng.random() < 0.9:
            op = "NOP"
        if op == "PULLW" and rng.random() < 0.7:
            op = "PULL"  # an empty FIFO would stall PULLW for the rest of the run
        if op == "DLYR" and rng.random() < 0.7:
            op = "NOP"  # a huge register value would too
        if op == "TSETR" and rng.random() < 0.7:
            op = "TSET"  # a register period is usually huge, which starved the !F and BC=0 jump bins
        fields = {}
        for name, width in TABLE[op][1]:
            value = rng.getrandbits(width)
            if name == "to":
                value = rng.randrange(1, length - 1)
            elif name == "c" and op == "JMP" and rng.random() < 0.4:
                value = rng.choice([COND["BCN"], COND["BCN"], COND["BC0"]])
            elif name == "rel":
                value = rng.randrange(0, min(pc, 16))
            elif name == "n" and op == "DLY":
                value = rng.randrange(0, 40)
            elif name == "p":
                value = rng.randrange(1, 40)
            elif name == "dly":
                value = rng.randrange(0, min(1 << width, 6))
            fields[name] = value
        if op == "JMP" and fields["c"] == COND["BC0"] and pc + 1 not in starts and pc + 1 < length - 1 \
                and rng.random() < 0.5:
            # Few BC=0 jumps run at all, so set BC up for both outcomes rather than leave it to chance
            words.append(encode("BCI", n=0 if rng.random() < 0.5 else rng.randrange(1, 32)))
        if op == "JMP" and fields["c"] in (COND["F"], COND["NF"]) and rng.random() < 0.5 \
                and not starts & {pc + 1, pc + 2} and pc + 2 < length - 1:
            # Missed ticks set M now, not F, so F is rarely 1 and JMP NF was seldom seen not taken. Shifting
            # a register holding 1 or 2 right sets F to its low bit, for both outcomes
            r = rng.randrange(4)
            words += [encode("LDI", r=r, hl=0, v=rng.choice([1, 2])), encode("SHR", r=r)]
        if op in ("OUT", "IN", "OUTB", "INB", "CRC") and rng.random() < 0.5 and pc + 1 not in starts \
                and pc + 1 < length - 1:
            # LANES is one opcode in sixty and its width lasts, so left to chance some widths never shifted
            words.append(encode("LANES", w=rng.randrange(4)))
        if op in ("LDP", "JMPR"):
            # Both take an address from a register, so load one inside the program first: past it the memory
            # holds whatever an earlier test left there, which the model doesn't know
            r = fields["s"] & 3
            fields["s"] = r
            if pc + 1 in starts or pc + 1 >= length - 1:
                op, fields = "NOP", {"tick": 0, "side": 0}
            else:
                words.append(encode("LDI", r=r, hl=0, v=rng.randrange(1, length - 1)))
        words.append(encode(op, **fields))
    words.append(encode("JMP", c=COND["AL"], to=1))
    return words


async def clear_program_memory(dut):
    """LDP and JMPR can reach any address, and the model's memory past the program is zeros, so make the
    chip's match. Earlier tests leave their programs behind. The program memory has 512 words."""
    await stop(dut)
    await load_program(dut, [0] * 512)


def state_differences(dut, model):
    """Every architectural register of both contexts, model against RTL."""
    engine = dut.user_project.engine
    diffs = {}
    for k, c in enumerate(model.ctx):
        pairs = {
            "pc": (c.pc, engine.pc[k]), "SR": (c.SR, engine.sr[k]), "BC": (c.BC, engine.bc[k]),
            "TO": (c.TO, engine.to[k]), "Z": (c.Z, engine.z[k]), "F": (c.F, engine.f[k]),
            "stall": (c.stall, engine.stall[k]), "pending": (int(c.pending), engine.pending[k]),
            "tcnt": (c.tcnt, engine.tcnt[k]), "tp": (c.tp, engine.tp[k]), "lr": (c.lr, engine.lr[k]),
            "msb": (int(c.msb), engine.msb[k]), "lfsr": (c.lfsr, engine.lfsr[k]),
            "side": (c.side_line, engine.side_ln[k]), "tsl": (c.tsrc_line, engine.ts_line[k]),
            "tsw": (c.tsrc_want, engine.ts_want[k]), "poly": (c.poly, engine.poly[k]),
            "lastb": (c.lastb, engine.lastb[k]), "M": (c.M, engine.m[k]),
            "since": (c.since, engine.since[k]), "tint": (c.tint, engine.tint[k]),
            "lanes": (c.lanes.bit_length() - 1, engine.lw[k]),
            "ldp": (int(c.ldp is not None), engine.ldp_pend[k]),
        }
        for r in range(4):
            pairs[f"R{r}"] = (c.R[r], engine.rr[k * 4 + r])
        for name, (want, handle) in pairs.items():
            value = int(handle.value)
            if value != want:
                diffs[f"ctx{k}.{name}"] = (want, value)
    cap = dut.user_project.capture
    for name, want, handle in (("cap.active", int(model.cap_active), cap.active), ("cap.ptr", model.cap_ptr, cap.ptr),
                               ("cap.dt", model.cap_dt, cap.dt), ("cap.pre", model.cap_pre, cap.pre),
                               ("cap.triggered", int(model.cap_triggered), cap.triggered)):
        value = int(handle.value)
        if value != want:
            diffs[name] = (want, value)
    return diffs


async def differential_once(dut, seed, cycles, two):
    """One random program on both, comparing every output pin on every cycle. `two` is the number of
    contexts, or a bool for one or two. With more than one, PULL and PUSH are routed at random to the host
    or to the links between contexts."""
    rng = random.Random(seed)
    nctx = two if not isinstance(two, bool) else (2 if two else 1)
    length = 48 if nctx <= 2 else 64
    starts = [length * k // nctx for k in range(1, nctx)] + [0] * (3 - (nctx - 1))
    words = random_program(rng, length, starts[:nctx - 1] if nctx > 1 else -1)
    odrain, fast = rng.getrandbits(8), rng.getrandbits(8)
    holdoff = rng.choice([0, 1, 2, 5, 20, 64])
    preload = [rng.getrandbits(16) for _ in range(rng.randrange(0, 5))]
    pull_sel = push_sel = 0
    if nctx > 2:
        pull_sel, push_sel = rng.getrandbits(2 * nctx), rng.getrandbits(2 * nctx)
    two = nctx > 1

    await stop(dut)
    await load_program(dut, words)
    await write_reg(dut, 0, nctx - 1)
    await write_reg(dut, 1, odrain)
    await write_reg(dut, 2, fast)
    await write_reg(dut, 4, 0)
    await write_reg(dut, 5, starts[0])
    await write_reg(dut, 17, starts[1])
    await write_reg(dut, 19, starts[2])
    await write_reg(dut, 20, pull_sel)
    await write_reg(dut, 21, push_sel)
    await write_reg(dut, 3, holdoff)
    watch = rng.getrandbits(16)
    prescale, cap_mode, post = rng.randrange(0, 6), rng.getrandbits(3) & 0b101, rng.randrange(0, 6)
    await write_reg(dut, 6, watch & 0xFF)
    await write_reg(dut, 7, watch >> 8)
    await write_reg(dut, 8, prescale)
    await write_reg(dut, 9, 0)
    await write_reg(dut, 10, cap_mode)
    await write_reg(dut, 11, post)
    await write_reg(dut, 12, 0)
    if preload:
        await fifo_write(dut, preload)
    dut.uio_in.value = 0xFF
    await spi_xfer(dut, [0x10])
    assert int(dut.uo_out.value) & 0x08, "engine isn't running"

    # Both sides are now parked. With two contexts the slot order isn't known yet, so keep a model
    # for each order and drop whichever disagrees. That's the only freedom the comparison gets.
    models = []
    for parity in range(nctx):
        model = Splice()
        model.load(words)
        model.nctx, model.odrain, model.fast, model.ctx_pc = nctx, odrain, fast, list(starts)
        model.pull_sel = [(pull_sel >> (2 * k)) & 3 for k in range(4)]
        model.push_sel = [(push_sel >> (2 * k)) & 3 for k in range(4)]
        model.holdoff = holdoff
        model.cap_watch, model.cap_prescale, model.cap_mode, model.cap_post = watch, prescale, cap_mode, post
        for word in preload:
            model.host_push(word)
        model.start()
        for _ in range(4):
            model.step(UI_IDLE, 0xFF)
        model.cur = parity
        models.append(model)
    expected = [m.pins() for m in models]

    ui_bits, uio_bits = UI_IDLE & 0xF0, 0xFF
    for t in range(cycles):
        await FallingEdge(dut.clk)
        got = (int(dut.uio_out.value), int(dut.uio_oe.value), int(dut.uo_out.value) & MASK_UO)
        alive = []
        for model, exp in zip(models, expected):
            if got == (exp["out"], exp["oe"], exp["uo"] & MASK_UO):
                alive.append(model)
        assert alive, (f"seed {seed} two={two} cycle {t}: RTL out={got[0]:02x} oe={got[1]:02x} uo={got[2]:02x}, "
                       f"model {[(e['out'], e['oe'], e['uo'] & MASK_UO) for e in expected]}, "
                       f"pc {[[c.pc for c in m.ctx] for m in models]}")
        models = alive

        # On RTL, also compare the engine's internal state. Pins alone can hide a wrong register
        # value for thousands of cycles. The gate-level netlist doesn't have these names.
        if not GATE_LEVEL and t % STATE_CHECK_EVERY == 0:
            matching = [m for m in models if not state_differences(dut, m)]
            assert matching, (f"seed {seed} two={two} cycle {t}: internal state differs, model vs RTL: "
                              f"{[state_differences(dut, m) for m in models]}")
            models = matching
            expected = [m.pins() for m in models]

        # Lines change often enough to satisfy edge waits. AUX_IN3 drops at the start and stays
        # low for a while, which releases the park on both sides on the same clock.
        for bit in range(8):
            if rng.random() < 0.08:
                uio_bits ^= 1 << bit
        for bit in range(4, 8):
            if rng.random() < 0.08:
                ui_bits ^= 1 << bit
        if t < 3:
            ui_bits &= 0x7F
        dut.uio_in.value = uio_bits
        dut.ui_in.value = ui_bits | (UI_IDLE & 0x0F)
        expected = [m.step(ui_bits | (UI_IDLE & 0x0F), uio_bits) for m in models]

    for op, count in models[0].coverage.items():
        COVERAGE[op] = COVERAGE.get(op, 0) + count

    # Capture results are invisible on the pins, so read them back and compare them too. With two contexts
    # at gate level, where there's no state check, the pins may never settle the slot order, and the two
    # orders can record capture a cycle apart. So the result must match one of the models still standing.
    # Taking the first one failed seed 2002 on a correct netlist, whose capture matched the other order.
    dut.ui_in.value = UI_IDLE
    dut.uio_in.value = 0xFF
    await stop(dut)
    # The STOP command takes many cycles and capture runs until it lands, so the RTL may hold a few more
    # entries. The state check above compared the pointer while both were in step.
    pointer = await capture_pointer(dut)
    models = [m for m in models if pointer >= m.cap_ptr or m.cap_wrapped]
    assert models, f"seed {seed}: capture pointer {pointer} is behind every model"
    count = max(min(m.cap_ptr, 12) for m in models)
    if count:
        got = await read_capture(dut, count)
        fits = [m for m in models if got[:min(m.cap_ptr, 12)] == m.cap[:min(m.cap_ptr, 12)]]
        assert fits, f"seed {seed}: capture {got}, models {[m.cap[:min(m.cap_ptr, 12)] for m in models]}"
    dut.ui_in.value = UI_IDLE
    dut.uio_in.value = 0xFF
    await ClockCycles(dut.clk, 4)


@cocotb.test()
async def test_rtl_matches_model_one_context(dut):
    """Random programs at full rate: every output pin, every cycle."""
    await reset(dut)
    await clear_program_memory(dut)
    runs, cycles = (3, 300) if GATE_LEVEL else (40, 800)
    for seed in range(runs):
        await differential_once(dut, 1000 + seed, cycles, False)


@cocotb.test()
async def test_rtl_matches_model_two_contexts(dut):
    """The same at half rate, with both contexts running their own code."""
    await reset(dut)
    await clear_program_memory(dut)
    runs, cycles = (3, 300) if GATE_LEVEL else (40, 800)
    for seed in range(runs):
        await differential_once(dut, 2000 + seed, cycles, True)


@cocotb.test()
async def test_rtl_matches_model_four_contexts(dut):
    """Three and four contexts, each a slot in turn, with PULL and PUSH routed at random through the links."""
    await reset(dut)
    runs, cycles = (2, 300) if GATE_LEVEL else (24, 1200)
    for seed in range(runs):
        await differential_once(dut, 4000 + seed, cycles, 3 + seed % 2)
    await stop(dut)
    for reg in (0, 20, 21):
        await write_reg(dut, reg, 0)


@cocotb.test()
async def test_differential_covered_every_instruction(dut):
    """Passing means nothing for instructions that never ran, so check that they did."""
    unused = sorted(op for op in TABLE if op not in COVERAGE and op != "HALT" and op not in MODEL_ONLY)
    bins = [f"JMP.{c}.{r}" for c in COND.values() for r in ("taken", "not") if (c, r) != (0, "not")]
    bins += [f"WAIT.{c}.{r}" for c in WAITC.values() for r in ("met", "timeout")]
    bins += [f"MODE.{s}" for s in SRC.values()] + [f"TPHASE.{m}" for m in PHASE.values()]
    bins += [f"TSRC.{w}" for w in WANT.values()] + ["PUSH.ok", "PUSH.full"]
    bins += [f"{op}.t" for op, (_, fields) in TABLE.items() if any(name == "tick" for name, _ in fields)]
    # LANES: every shift at every width, and CRCW at every width
    bins += [f"{op}.w{w}" for op in ("OUT", "IN", "OUTB", "INB", "CRCW") for w in (1, 2, 4, 8)]
    missing = sorted(b for b in bins if b not in COVERAGE)
    dut._log.info(f"coverage: {len(bins) - len(missing)}/{len(bins)} bins, {sum(COVERAGE.values())} events")
    if GATE_LEVEL:  # far fewer runs at gate level, so this is only reported
        return
    with open(os.path.join(HERE, "coverage.txt"), "w", encoding="utf-8") as report:
        report.writelines(f"{name} {COVERAGE.get(name, 0)}\n" for name in sorted(set(COVERAGE) | set(bins)))
    assert not unused, f"never executed: {unused}"
    assert not missing, f"bins never hit: {missing}"
