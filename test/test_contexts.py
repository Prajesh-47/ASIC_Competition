# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# Up to four contexts, and links between them (explore/uarch-contexts). Each context's PULL and PUSH can be
# routed to the host or to one of two link FIFOs, so one context's output feeds another's input with no
# host in between. Only top-level ports are used, so these also run on the gate-level netlist.

import os

import cocotb
from cocotb.triggers import ClockCycles

from test import HERE, assemble, capture, fifo_read, fifo_write, firmware, load_program, read_reg, reset, start, status, stop, write_reg
from test_protocols import level


def uart_wave(data, bit, lead=2):
    wave = [1] * (lead * bit)
    for byte in data:
        wave += sum(([b] * bit for b in [0] + [(byte >> k) & 1 for k in range(8)] + [1]), []) + [1] * 7
    return wave


def uart_decode(samples, bit):
    """8N1 from a list of line levels, one per cycle: find each start edge, sample mid-bit."""
    out, t = [], 0
    while t < len(samples) - 10 * bit:
        if samples[t] == 0 and (t == 0 or samples[t - 1] == 1):
            mid = t + bit // 2
            assert samples[mid] == 0, f"start bit at {t} isn't low in its middle"
            byte = sum(samples[mid + bit * (k + 1)] << k for k in range(8))
            assert samples[mid + 9 * bit] == 1, f"no stop bit for byte {len(out)}"
            out.append(byte)
            t = mid + 9 * bit
        t += 1
    return out


def spi_bytes(bits):
    return [sum(b << (7 - i) for i, b in enumerate(bits[j:j + 8])) for j in range(0, len(bits) - 7, 8)]


async def set_contexts(dut, prog, nctx, pull_sel=0, push_sel=0):
    await write_reg(dut, 0, nctx - 1)
    for reg, pc in ((4, prog.ctx[0]), (16, prog.ctx[1]), (18, prog.ctx[2])):
        await write_reg(dut, reg, pc >> 8)
        await write_reg(dut, reg + 1, pc & 0xFF)
    await write_reg(dut, 20, pull_sel)
    await write_reg(dut, 21, push_sel)


async def clear_contexts(dut):
    await stop(dut)
    for reg in (0, 20, 21):
        await write_reg(dut, reg, 0)


@cocotb.test()
async def test_registers_read_back(dut):
    """The new host registers hold what's written, and register 0 still means two contexts when written 1."""
    await reset(dut)
    for reg, value in ((0, 3), (16, 1), (17, 0xA5), (18, 0), (19, 0x5A), (20, 0x9C), (21, 0x63)):
        await write_reg(dut, reg, value)
        got = await read_reg(dut, reg)
        assert got == value, f"register {reg}: wrote {value:#x}, read {got:#x}"
    await write_reg(dut, 0, 1)
    assert await read_reg(dut, 0) == 1
    # Links empty and nothing halted
    assert await read_reg(dut, 22) == 0x50, f"register 22 is {await read_reg(dut, 22):#x}"
    await clear_contexts(dut)


async def uart_to_spi(dut, pull_sel, push_sel):
    bit, half = 40, 10
    prog = firmware("two_protocols.s", BIT=bit, HALF=half)
    await load_program(dut, prog.words)
    await set_contexts(dut, prog, 2, pull_sel, push_sel)
    sent = [0x55, 0xA3, 0x0F, 0xE1, 0x00, 0xFF, 0x96, 0x3C]
    await start(dut)
    wave = uart_wave(sent, bit)
    st = {"psck": 0, "mosi": []}

    def bus(t, out, oe):
        sck = level(out, oe, 4, idle=0)
        if sck and not st["psck"]:
            st["mosi"].append(level(out, oe, 5))
        st["psck"] = sck
        return 0xFE | (wave[t] if t < len(wave) else 1)

    await capture(dut, len(wave) + 40 * half, bus)
    return sent, spi_bytes(st["mosi"])


@cocotb.test()
async def test_bridge_needs_the_link(dut):
    """Negative control: the same program without the routing sends no SPI at all, because context 1 waits
    on an empty host FIFO and context 0's bytes go to the host."""
    await reset(dut)
    sent, got = await uart_to_spi(dut, 0, 0)
    assert got == [], f"SPI sent {got} with no link"
    words = await fifo_read(dut, 4)
    assert [w >> 8 for w in words] == sent[:4], f"host got {words}"
    await clear_contexts(dut)


@cocotb.test()
async def test_uart_to_spi_bridge_without_the_host(dut):
    """two_protocols.s unchanged, with context 0's PUSH routed to link 0 and context 1's PULLW from it: UART
    bytes come out as SPI, and the host FIFO is never used. On main this program needs the host to read
    each UART byte and write it back."""
    await reset(dut)
    sent, got = await uart_to_spi(dut, 1 << 2, 1)
    assert got == sent, f"SPI sent {[hex(b) for b in got]}"
    assert (await status(dut)) & 0x10, "the host FIFO got data"
    await clear_contexts(dut)


@cocotb.test()
async def test_four_protocols_at_once(dut):
    """Four contexts, a quarter rate each: a UART-to-SPI bridge through link 0, UART transmit from the host,
    and a second UART receive to the host, all at the same time, each judged on its own pins."""
    await reset(dut)
    await four_protocols(dut, firmware("four_protocols.s", BIT=40, HALF=10))


@cocotb.test()
async def test_program_above_word_256(dut):
    """explore/uarch-prog512: the same four protocols with all their code above word 300, which a 256-word
    program memory can't hold. Word 0 jumps there. SD alone uses 245 of 256 words on main."""
    await reset(dut)
    with open(os.path.join(HERE, "..", "model", "firmware", "four_protocols.s"), encoding="utf-8") as f:
        source = f.read()
    pad = "        jmp     high\n" + "        nop\n" * 299 + "high:\n"
    prog = assemble(pad + source, {"BIT": 40, "HALF": 10})
    assert len(prog.words) > 320 and min(prog.ctx) >= 300, f"{len(prog.words)} words, starts {prog.ctx}"
    await four_protocols(dut, prog)


async def four_protocols(dut, prog):
    bit, half = 40, 10
    await load_program(dut, prog.words)
    await set_contexts(dut, prog, 4, pull_sel=1 << 2, push_sel=1)
    bridge, to_host, from_host = [0x55, 0xA3, 0x0F, 0xE1], [0x12, 0xC3, 0x7E, 0x81], [0x4B, 0x00, 0xFF, 0x2D]
    await fifo_write(dut, from_host)  # UART transmit sends the low byte, LSB first
    await start(dut)

    wave_a, wave_b = uart_wave(bridge, bit), uart_wave(to_host, bit, lead=5)
    st = {"psck": 0, "mosi": [], "rises": [], "tx": []}

    def bus(t, out, oe):
        sck = level(out, oe, 4, idle=0)
        if sck and not st["psck"]:
            st["mosi"].append(level(out, oe, 5))
            st["rises"].append(t)
        st["psck"] = sck
        st["tx"].append(level(out, oe, 1))
        a = wave_a[t] if t < len(wave_a) else 1
        b = wave_b[t] if t < len(wave_b) else 1
        return 0xFA | a | (b << 2)

    cycles = max(len(wave_a), len(wave_b)) + 12 * bit
    await capture(dut, cycles, bus)
    assert spi_bytes(st["mosi"]) == bridge, f"bridge sent {[hex(b) for b in spi_bytes(st['mosi'])]}"
    tx = uart_decode(st["tx"], bit)
    assert tx == from_host, f"UART transmit sent {[hex(b) for b in tx]}"
    words = await fifo_read(dut, len(to_host))
    assert [w >> 8 for w in words] == to_host, f"UART receive got {[hex(w) for w in words]}"

    # A quarter rate delays a tick by up to three cycles, and no more
    gaps = [b - a for a, b in zip(st["rises"], st["rises"][1:])]
    within_byte = [g for i, g in enumerate(gaps) if (i + 1) % 8]
    assert all(abs(g - 2 * half) <= 3 for g in within_byte), f"SCK period off: {sorted(set(within_byte))}"
    await clear_contexts(dut)


@cocotb.test()
async def test_link_full_sets_f_and_drops(dut):
    """A link is a 4-word FIFO like the host's: a fifth PUSH with nobody pulling sets F and is dropped, and
    the four words come out in order on the other side."""
    await reset(dut)
    # Context 0 pushes 1 to 5 into link 0 while context 1 waits, and raises AUX_OUT0 if a PUSH set F.
    # Context 1 then moves what crossed the link to the host.
    prog = assemble("""
        ldi r0, 0
        ldi r1, 5
    lp: add r0, 1
        mov sr, r0
        push
        djnz r1, lp
        jmp f, full
        halt
    full: set 8, 1
        halt
    rd: dly 300
    rd2: pullw 16
        push
        jmp rd2
    .ctx1 rd
    """)
    await load_program(dut, prog.words)
    await set_contexts(dut, prog, 2, pull_sel=1 << 2, push_sel=1)
    await start(dut)
    await ClockCycles(dut.clk, 200)
    assert int(dut.uo_out.value) & 0x10, "the fifth PUSH into a full link didn't set F"
    await ClockCycles(dut.clk, 600)
    words = await fifo_read(dut, 4)
    assert words == [1, 2, 3, 4], f"crossed the link: {words}"
    assert (await status(dut)) & 0x10, "a fifth word crossed"
    await clear_contexts(dut)
