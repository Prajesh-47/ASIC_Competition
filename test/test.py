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
from splice.isa import COND, TABLE, encode  # noqa: E402
from splice.model import Splice  # noqa: E402

GATE_LEVEL = os.environ.get("GATES") == "yes"
HALF = 5  # SPI half period in core clocks: SCK is core/10
UI_IDLE = 0xF4  # AUX_IN high, CS high, SCK and MOSI low


def firmware(name, **defines):
    with open(os.path.join(HERE, "..", "model", "firmware", name), encoding="utf-8") as source:
        return assemble(source.read(), defines)


async def spi_xfer(dut, data, ui_high=0xF0):
    """One SPI transaction, mode 0, MSB first. Returns the bytes clocked back."""
    out = []
    dut.ui_in.value = ui_high  # CS low
    await ClockCycles(dut.clk, HALF)
    for byte in data:
        value = 0
        for k in range(7, -1, -1):
            bit = (byte >> k) & 1
            dut.ui_in.value = ui_high | (bit << 1)
            await ClockCycles(dut.clk, HALF)
            value = (value << 1) | (int(dut.uo_out.value) & 1)
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
    assert (await status(dut)) & 1 == 0


@cocotb.test()
async def test_lines_stay_hiz_until_configured(dut):
    """Bus traffic must not make an unconfigured SPLICE drive anything."""
    await reset(dut)
    for value in (0x00, 0xFF, 0xA5, 0x5A):
        dut.uio_in.value = value
        await ClockCycles(dut.clk, 4)
        assert dut.uio_oe.value == 0


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
async def test_host_registers(dut):
    await reset(dut)
    await write_reg(dut, 1, 0x30)
    await write_reg(dut, 3, 0x2A)
    assert (await spi_xfer(dut, [0x06, 1, 0]))[2] == 0x30
    assert (await spi_xfer(dut, [0x06, 3, 0]))[2] == 0x2A


# ------------------------------------------------------------------ Reprogrammed protocols
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


# ------------------------------------------------------------------ RTL against the golden model
PARK_LINE = 11  # AUX_IN3: every random program waits for this line before doing anything else
MASK_UO = 0xFE  # uo[0] is the host's MISO, which the model doesn't include
COVERAGE = {}


def random_program(rng, length, ctx1):
    """Valid random programs that stay in bounds, so no read is undefined."""
    park = encode("WAIT", line=PARK_LINE, c=0, stamp=0)
    words = [park, encode("TSET", p=rng.randrange(1, 30))]
    words += [encode("LDI", r=0, hl=0, v=rng.randrange(1, 40)), encode("MOV", d=6, s=0)]  # set a timeout
    choices = [op for op in TABLE if op not in ("CAPMARK", "CAPOFF", "CAPON")]
    while len(words) < length - 1:
        pc = len(words)
        if pc == ctx1:
            words.append(park)
            continue
        if rng.random() < 0.02:  # reserved encodings must behave as NOP
            words.append(rng.choice([0xC400, 0xC800, 0xCC00, 0xA800, 0xB000, 0xE640, 0xF000, 0xFFFF]))
            continue
        op = rng.choice(choices)
        if op == "HALT" and rng.random() < 0.9:
            op = "NOP"
        if op == "PULLW" and rng.random() < 0.7:
            op = "PULL"  # an empty FIFO would stall PULLW for the rest of the run
        if op == "DLYR" and rng.random() < 0.7:
            op = "NOP"  # a huge register value would too
        fields = {}
        for name, width in TABLE[op][1]:
            value = rng.getrandbits(width)
            if name == "to":
                value = rng.randrange(1, length - 1)
            elif name == "rel":
                value = rng.randrange(0, min(pc, 16))
            elif name == "n" and op == "DLY":
                value = rng.randrange(0, 40)
            elif name == "p":
                value = rng.randrange(1, 40)
            elif name == "dly":
                value = rng.randrange(0, min(1 << width, 6))
            fields[name] = value
        words.append(encode(op, **fields))
    words.append(encode("JMP", c=COND["AL"], to=1))
    return words


async def differential_once(dut, seed, cycles, two):
    """One random program on both, comparing every output pin on every cycle."""
    rng = random.Random(seed)
    length = 48
    ctx1 = length // 2 if two else 0
    words = random_program(rng, length, ctx1 if two else -1)
    odrain, fast = rng.getrandbits(8), rng.getrandbits(8)
    preload = [rng.getrandbits(16) for _ in range(rng.randrange(0, 5))]

    await stop(dut)
    await load_program(dut, words)
    await write_reg(dut, 0, int(two))
    await write_reg(dut, 1, odrain)
    await write_reg(dut, 2, fast)
    await write_reg(dut, 4, 0)
    await write_reg(dut, 5, ctx1)
    if preload:
        await fifo_write(dut, preload)
    dut.uio_in.value = 0xFF
    await spi_xfer(dut, [0x10])
    assert int(dut.uo_out.value) & 0x08, "engine isn't running"

    # Both sides are now parked. With two contexts the slot order isn't known yet, so keep a model
    # for each order and drop whichever disagrees. That's the only freedom the comparison gets.
    models = []
    for parity in (0, 1) if two else (0,):
        model = Splice()
        model.load(words)
        model.two, model.odrain, model.fast, model.ctx1_pc = two, odrain, fast, ctx1
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
    dut.ui_in.value = UI_IDLE
    dut.uio_in.value = 0xFF
    await ClockCycles(dut.clk, 4)


@cocotb.test()
async def test_rtl_matches_model_one_context(dut):
    """Random programs at full rate: every output pin, every cycle."""
    await reset(dut)
    runs, cycles = (3, 300) if GATE_LEVEL else (40, 800)
    for seed in range(runs):
        await differential_once(dut, 1000 + seed, cycles, False)


@cocotb.test()
async def test_rtl_matches_model_two_contexts(dut):
    """The same at half rate, with both contexts running their own code."""
    await reset(dut)
    runs, cycles = (3, 300) if GATE_LEVEL else (40, 800)
    for seed in range(runs):
        await differential_once(dut, 2000 + seed, cycles, True)


@cocotb.test()
async def test_differential_covered_every_instruction(dut):
    """Passing means nothing for instructions that never ran, so check that they did."""
    unused = sorted(op for op in TABLE if op not in COVERAGE and op not in ("CAPMARK", "CAPOFF", "CAPON", "HALT"))
    dut._log.info(f"executed instructions: {dict(sorted(COVERAGE.items()))}")
    assert not GATE_LEVEL and not unused or GATE_LEVEL, f"never executed: {unused}"
