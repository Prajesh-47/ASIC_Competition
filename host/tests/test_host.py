# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Host tools, with no board or simulator. Run from host/: python3 -m tests.test_host"""

import os
import random
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "board"))
sys.path.insert(0, os.path.join(HERE, ".."))

import splice_agent  # noqa: E402
from splice_host.__main__ import build_parser, read_words, run_command, status_text  # noqa: E402
from splice_host.commands import Board  # noqa: E402
from splice_host.link import AgentLink, LinkError  # noqa: E402

FIRMWARE = os.path.join(HERE, "..", "..", "model", "firmware")


class Target:
    """An SPI mode 0 target written from the spec: samples MOSI on rise, changes MISO on fall, echoes one byte late."""

    def __init__(self):
        self.sck = self.Pin(self, "sck")
        self.mosi = self.Pin(self, "mosi")
        self.cs_n = self.Pin(self, "cs_n", 1)
        self.miso = self.Pin(self, "miso")
        self.received, self.shift_in, self.count, self.out = [], 0, 0, 0
        self.rises_with_cs_high = 0

    class Pin:
        def __init__(self, target, name, level=0):
            self.target, self.name, self.level = target, name, level

        def value(self, level=None):
            if level is None:
                return self.level
            old, self.level = self.level, level
            if self.name == "sck" and (old, level) == (0, 1):
                self.target.rise()
            if self.name == "sck" and (old, level) == (1, 0):
                self.target.fall()

    def rise(self):
        if self.cs_n.level:
            self.rises_with_cs_high += 1
            return
        self.shift_in = (self.shift_in << 1 | self.mosi.level) & 0xFF
        self.count += 1
        if self.count % 8 == 0:
            self.received.append(self.shift_in)
            self.out = self.shift_in

    def fall(self):
        self.miso.level = self.out >> 7 & 1
        self.out = self.out << 1 & 0xFF


def test_bitbang_is_mode_0_msb_first():
    t = Target()
    data = bytes(random.Random(1).randrange(256) for _ in range(20))
    got = splice_agent.spi_xfer(data, t.sck, t.mosi, t.cs_n, t.miso)
    assert bytes(t.received) == data
    assert bytes(got)[1:] == data[:-1], "reads back one byte late, so MISO was sampled on the wrong edge"
    assert t.cs_n.level == 1 and t.sck.level == 0 and t.rises_with_cs_high == 0


class QuadTarget:
    """Quad mode from the spec: IO0-3 sampled on rise, high nibble first, bit 3 on IO3; answers one byte late."""

    def __init__(self):
        self.sck = Target.Pin(self, "sck")
        self.cs_n = Target.Pin(self, "cs_n", 1)
        self.ins = [Target.Pin(self, f"in{k}") for k in range(4)]
        self.outs = [Target.Pin(self, f"out{k}") for k in range(4)]
        self.received, self.shift_in, self.count, self.out = [], 0, 0, 0
        self.rises_with_cs_high = 0

    def rise(self):
        if self.cs_n.level:
            self.rises_with_cs_high += 1
            return
        self.shift_in = (self.shift_in << 4 | sum(p.level << k for k, p in enumerate(self.ins))) & 0xFF
        self.count += 1
        if self.count % 2 == 0:
            self.received.append(self.shift_in)
            self.out = self.shift_in

    def fall(self):
        for k, p in enumerate(self.outs):
            p.level = (self.out >> (4 + k)) & 1
        self.out = self.out << 4 & 0xFF


def test_quad_bitbang_nibble_order_and_edges():
    t = QuadTarget()
    data = bytes(random.Random(2).randrange(256) for _ in range(20))
    got = splice_agent.quad_xfer(data, t.sck, t.cs_n, t.ins, t.outs)
    assert bytes(t.received) == data
    assert bytes(got)[1:] == data[:-1], "reads back one byte late, so IO was sampled on the wrong edge"
    assert t.cs_n.level == 1 and t.rises_with_cs_high == 0


def test_agent_quad_request_writes_register_23_then_switches():
    sent = []
    port = splice_agent.Port(lambda d: sent.append(("single", bytes(d))) or bytes(len(d)),
                             lambda d: sent.append(("quad", bytes(d))) or bytes(len(d)))
    assert splice_agent.handle("q 1", port.xfer, FakeBoard(), port) == "="
    port.xfer(b"\x12\x00")
    assert splice_agent.handle("q 0", port.xfer, FakeBoard(), port) == "="
    port.xfer(b"\x12\x00")
    assert sent == [("single", bytes([5, 23, 1])), ("quad", b"\x12\x00"), ("quad", bytes([5, 23, 0])),
                    ("single", b"\x12\x00")]


class FakeBoard:
    def __init__(self):
        self.calls = []
        self.shuttle = self

    def clock_project_PWM(self, hz, quiet=False):
        self.calls.append(("clock", hz))

    def reset_project(self, held):
        self.calls.append(("reset", held))

    def enable(self):
        self.calls.append(("enable",))

    @property
    def tt_um_prajesh47_splice(self):
        return self


def test_agent_requests():
    board = FakeBoard()
    assert splice_agent.handle("x 01ff\r\n", lambda d: bytes(reversed(d)), board) == "=ff01"
    assert splice_agent.handle("c 50000000", None, board) == "="
    assert splice_agent.handle("r", None, board) == "="
    assert splice_agent.handle("s tt_um_prajesh47_splice", None, board) == "="
    assert board.calls == [("clock", 50000000), ("reset", True), ("reset", False), ("enable",)]
    assert splice_agent.handle("", None, board) is None
    assert splice_agent.handle("q", None, board).startswith("!")
    assert splice_agent.handle("x zz", lambda d: d, board).startswith("!"), "bad hex must not kill the agent"
    assert splice_agent.handle("s nothing", None, board).startswith("!")


class Stream:
    """The serial port, with the agent on the far end, plus REPL noise around its answers."""

    def __init__(self, xfer, board=None, noise=b""):
        self.xfer, self.board, self.noise, self.lines = xfer, board, noise, []
        self.dead = False

    def write(self, data):
        if not self.dead:
            answer = splice_agent.handle(data.decode(), self.xfer, self.board)
            self.lines += [self.noise, answer.encode() + b"\r\n"] if self.noise else [answer.encode() + b"\r\n"]

    def readline(self):
        return self.lines.pop(0) if self.lines else b""


def test_link_round_trip():
    link = AgentLink(Stream(lambda d: bytes(b ^ 0xFF for b in d), noise=b"ttboard: logging\r\n"))
    assert link.xfer([0x00, 0x0F]) == [0xFF, 0xF0]


def test_link_reports_errors_and_silence():
    link = AgentLink(Stream(lambda d: d))
    try:
        link.request("q")
    except LinkError as e:
        assert "unknown" in str(e)
    else:
        raise AssertionError("error answer was ignored")
    stream = Stream(lambda d: d)
    stream.dead = True
    try:
        AgentLink(stream).xfer([1])
    except LinkError as e:
        assert "no answer" in str(e)
    else:
        raise AssertionError("timeout was ignored")


class Chip:
    """Enough of the host port for CLI tests: program memory, registers and the commands' framing."""

    def __init__(self, corrupt=False):
        self.mem, self.regs, self.started, self.corrupt = {}, {}, 0, corrupt

    def xfer(self, d):
        d = list(d)
        out = [0] * len(d)
        if d[0] == 0x01:
            base = d[1] << 8 | d[2]
            for i in range((len(d) - 3) // 2):
                self.mem[base + i] = d[3 + 2 * i] << 8 | d[4 + 2 * i]
        elif d[0] == 0x02:
            base = d[1] << 8 | d[2]
            for i in range((len(d) - 3) // 2):
                word = self.mem.get(base + i, 0) ^ (1 if self.corrupt else 0)
                out[3 + 2 * i], out[4 + 2 * i] = word >> 8, word & 0xFF
        elif d[0] == 0x05:
            self.regs[d[1]] = d[2]
        elif d[0] == 0x06:
            out[2] = self.regs.get(d[1], 0)
        elif d[0] == 0x10:
            self.started += 1
        return out


def cli(chip, *argv):
    class Out(list):
        def write(self, text):
            self.append(text)

        def flush(self):
            pass

    args, out = build_parser().parse_args(list(argv)), Out()
    run_command(args, Board(chip.xfer), None, out)
    return "".join(out)


def test_cli_load_assembles_and_checks():
    chip = Chip()
    source = os.path.join(FIRMWARE, "uart_tx.s")
    assert "loaded" in cli(chip, "load", source, "-D", "BIT=20", "--run")
    words, _ = read_words(source, {"BIT": 20})
    assert [chip.mem[i] for i in range(len(words))] == list(words) and chip.started == 1


def test_cli_load_two_contexts_sets_registers():
    chip = Chip()
    with tempfile.NamedTemporaryFile("w", suffix=".s", delete=False) as f:
        f.write(".ctx1 other\nhere:\n    jmp here\nother:\n    jmp other\n")
    try:
        cli(chip, "load", f.name)
    finally:
        os.unlink(f.name)
    assert chip.regs[0] == 1 and (chip.regs[4] << 8 | chip.regs[5]) == 1


def test_cli_load_notices_bad_readback():
    try:
        cli(Chip(corrupt=True), "load", os.path.join(FIRMWARE, "uart_tx.s"), "-D", "BIT=20")
    except LinkError as e:
        assert "read back" in str(e)
    else:
        raise AssertionError("corrupted load was accepted")


def test_cli_hex_file_and_registers():
    chip = Chip()
    with tempfile.NamedTemporaryFile("w", suffix=".hex", delete=False) as f:
        f.write("1234\nabcd // note\n\n")
    try:
        cli(chip, "load", f.name)
    finally:
        os.unlink(f.name)
    assert chip.mem == {0: 0x1234, 1: 0xABCD} and chip.regs[0] == 0
    cli(chip, "reg", "3", "0x40")
    assert cli(chip, "reg", "3").strip() == "0x40"


def test_exchange_encodes_and_keeps_only_waiting_words():
    sent = []

    def xfer(data):
        sent.append(list(data))
        # two words waiting (one of them 0), room for 5, then a third word arrives during the burst
        return [0, 2, 5, 0x12, 0x34, 0x00, 0x00, 0x56, 0x78]

    got, free = Board(xfer).exchange([0xAAAA], slots=3)
    assert sent == [[0x22, 1, 0, 0xAA, 0xAA, 0, 0, 0, 0]]
    assert got == [0x1234, 0x0000] and free == 4


def test_status_names_match_the_spec():
    assert status_text(0b00010001) == "0x11 running e2h_empty"
    assert status_text(0b00100000) == "0x20 irq"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"{len(tests)}/{len(tests)} passed")
