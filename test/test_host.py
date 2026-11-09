# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# The board agent's SPI code against the chip. The agent's bit-bang routine runs in Python and its
# pin activity is recorded, then replayed onto ui_in one change at a time at the fastest SCK the
# spec allows. MISO is sampled exactly where the agent would read it.

import os
import sys

import cocotb
from cocotb.triggers import ClockCycles

from test import HERE, UI_IDLE, firmware, reset

sys.path.insert(0, os.path.join(HERE, "..", "host", "board"))
sys.path.insert(0, os.path.join(HERE, "..", "host"))
from splice_agent import CS_N, MOSI, SCK, spi_xfer as agent_xfer  # noqa: E402
from splice_host.commands import Board  # noqa: E402

CYCLES_PER_CHANGE = 4  # one SCK phase, so SCK is core/8


class RecordedPin:
    def __init__(self, bit, log):
        self.bit, self.log = bit, log

    def value(self, level=None):
        if level is None:
            self.log.append(("read",))
            return 0
        self.log.append(("set", self.bit, level))


def record(data):
    log = []
    agent_xfer(data, RecordedPin(SCK, log), RecordedPin(MOSI, log), RecordedPin(CS_N, log), RecordedPin(None, log))
    return log


async def replay(dut, data):
    """Returns the bytes the agent would have read."""
    ui, bits = UI_IDLE, []
    for event in record(data):
        if event[0] == "read":
            bits.append(int(str(dut.uo_out.value)[-1]))
        else:
            ui = (ui & ~(1 << event[1])) | (event[2] << event[1])
            dut.ui_in.value = ui
            await ClockCycles(dut.clk, CYCLES_PER_CHANGE)
    await ClockCycles(dut.clk, CYCLES_PER_CHANGE)
    return [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]


def commands(call):
    """The transactions `call(board)` sends, so the host library's encoding is what gets replayed."""
    sent = []
    call(Board(lambda data: sent.append(list(data)) or [0] * len(data)))
    return sent


@cocotb.test()
async def test_agent_spi_against_rtl(dut):
    await reset(dut)
    words = firmware("uart_tx.s", BIT=20).words
    for transaction in commands(lambda b: b.load(words)):
        await replay(dut, bytes(transaction))
    back = await replay(dut, bytes(commands(lambda b: b.read_program(len(words)))[0]))
    assert [back[3 + 2 * i] << 8 | back[4 + 2 * i] for i in range(len(words))] == list(words)

    await replay(dut, bytes(commands(lambda b: b.write_reg(3, 0x5A))[0]))
    assert (await replay(dut, bytes(commands(lambda b: b.read_reg(3))[0])))[2] == 0x5A

    await replay(dut, bytes(commands(lambda b: b.start())[0]))
    await ClockCycles(dut.clk, 8)
    assert int(str(dut.uo_out.value)[-4]) == 1, "ACTIVE should be high after a replayed start"
    await replay(dut, bytes(commands(lambda b: b.stop())[0]))
    await ClockCycles(dut.clk, 8)
    assert int(str(dut.uo_out.value)[-4]) == 0, "ACTIVE should be low after a replayed stop"


@cocotb.test()
async def test_agent_exchange_against_rtl(dut):
    """The library's XFER, through the agent's bit-bang code: words go out and come back echoed."""
    from test import ECHO_AFTER_FALL, assemble
    await reset(dut)
    for transaction in commands(lambda b: b.load(assemble(ECHO_AFTER_FALL).words)):
        await replay(dut, bytes(transaction))
    await replay(dut, bytes(commands(lambda b: b.start())[0]))
    dut.uio_in.value = 0xFE
    out = [0x0000, 0x8001, 0xFFFF]
    returned = []

    def decode(call, back):
        return Board(lambda data: back).exchange(*call)

    for call in ((out, 0), ((), 4)):
        back = await replay(dut, bytes(commands(lambda b: b.exchange(*call))[0]))
        returned.append(decode(call, back))
        await ClockCycles(dut.clk, 80)
    assert returned[0][0] == [], f"nothing was waiting yet: {returned[0]}"
    assert returned[1][0] == out, f"{[hex(w) for w in returned[1][0]]}"
