# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# TCAP and CMPU on RTL: one S/PDIF receive binary at several sample rates, against the trial firmware,
# with the trial's SPI host at the fastest rate allowed. The model-level study is lab/explore/scout.md.

import cocotb
from cocotb.triggers import FallingEdge

from test import UI_IDLE, Splice, firmware, load_program, reset, spi_xfer, start, state_differences, stop
from test_trial import spdif_receive, spdif_states, spdif_stream, spdif_wave


def decode_tcap(words):
    """Two words a subframe: preamble code in bits 2:0 of the first, then 29 levels, oldest first."""
    out = []
    for i in range(0, len(words) - 1, 2):
        w1, w2 = words[i], words[i + 1]
        name = {1: "M", 2: "W", 3: "B"}.get(w1 & 7, "?")
        levels = [(w1 >> k) & 1 for k in range(3, 16)] + [(w2 >> k) & 1 for k in range(16)]
        out.append((name, [int(levels[k] == levels[k + 1]) for k in range(28)]))
    return out


def decoded_run(decoded, sent, need=30):
    """Subframes decoded in a row once locked, or 0 if it never lines up or any is wrong."""
    for skip in range(min(12, len(sent))):
        if decoded and decoded[0] == sent[skip]:
            n = min(len(decoded), len(sent) - skip)
            return n if decoded[:n] == sent[skip:skip + n] and n >= need else 0
    return 0


async def receive(dut, program, fs, clock=1.0, count=44):
    """Sample rate fs, with the sender's clock off by `clock`. Returns subframes decoded in a row."""
    from test_trial import spdif_decode_words
    rate = fs / 48000 * clock
    sent = spdif_stream(count)
    await stop(dut)
    await load_program(dut, firmware(program).words)
    await start(dut)
    words, _ = await spdif_receive(dut, spdif_wave(spdif_states(sent), rate), rate=rate)
    import os
    if os.environ.get("TCAP_DEBUG"):
        e = dut.user_project.engine
        dut._log.info(f"DEBUG end: pc {int(e.pc[0].value)} halted {e.halted.value} running {e.running.value} "
                      f"R {[int(e.rr[i].value) for i in range(4)]} tint {int(e.tint[0].value)} tsw {int(e.ts_want[0].value)}")
    await stop(dut)
    decode = decode_tcap if "tcap" in program else spdif_decode_words
    if "tcap" in program:
        # The host reads on a timer, and an empty FIFO reads as 0, which would break the pairing. Word 1 is
        # never 0, since it carries the preamble code. A real 0 in word 2 would be dropped too, which can only
        # fail the test, never pass it wrongly. XFER (exp/host-fifo) removes the guesswork.
        words = [w for w in words if w]
    import os
    if os.environ.get("TCAP_DEBUG"):
        dut._log.info(f"DEBUG {program} {fs}: {len(words)} words, zeros {words.count(0)}, first {[hex(w) for w in words[:12]]}")
        dut._log.info(f"DEBUG decoded {decode(words)[:3]} sent {sent[:3]}")
    return decoded_run(decode(words), sent)


@cocotb.test()
async def test_tcap_spdif_one_binary_at_several_rates(dut):
    await reset(dut)
    for fs, clock in ((32000, 1.0), (44100, 1.0), (48000, 1.0), (48000, 0.8), (48000, 1.1)):
        n = await receive(dut, "explore/spdif_rx_tcap.s", fs, clock)
        dut._log.info(f"MEASURE TCAP S/PDIF {fs} Hz, clock x{clock}: {n} subframes in a row")
        assert n, f"TCAP firmware lost lock at {fs} Hz, clock x{clock}"


@cocotb.test()
async def test_trial_spdif_is_48k_only(dut):
    """Negative control: the same harness must fail the trial firmware at 44.1 kHz and pass it at 48."""
    await reset(dut)
    assert await receive(dut, "trial/spdif_rx.s", 48000), "trial firmware should work at 48 kHz"
    assert not await receive(dut, "trial/spdif_rx.s", 44100), "trial firmware shouldn't lock at 44.1 kHz"


@cocotb.test()
async def test_tcap_spdif_lockstep_with_model(dut):
    """RTL and model on the S/PDIF stream, compared every cycle, so a divergence shows where it starts."""
    await reset(dut)
    prog = firmware("explore/spdif_rx_tcap.s")
    wave = spdif_wave(spdif_states(spdif_stream(20)), 32000 / 48000)
    await stop(dut)
    await load_program(dut, prog.words)
    dut.uio_in.value = 0xFE | wave[0]
    await spi_xfer(dut, [0x10])
    model = Splice()
    model.load(prog.words)
    model.start()
    await FallingEdge(dut.clk)
    for _ in range(200):  # the RTL starts during the START transaction, so catch the model up to it
        if not state_differences(dut, model):
            break
        model.step(UI_IDLE, 0xFE | wave[0])
    assert not state_differences(dut, model), "couldn't line the model up with the RTL"
    for t, level in enumerate(wave):
        if t:
            await FallingEdge(dut.clk)
        diffs = state_differences(dut, model)
        assert not diffs, f"cycle {t}: model vs RTL {diffs}, pc {model.ctx[0].pc}"
        dut.uio_in.value = 0xFE | level
        model.step(UI_IDLE, 0xFE | level)
        while model.e2h:
            model.e2h.pop(0)
