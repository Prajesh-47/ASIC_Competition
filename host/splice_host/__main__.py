# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Command line for a SPLICE chip on the demo board. Run with `python3 -m splice_host --help` from host/."""

import argparse
import os
import sys

from .capture import to_vcd
from .commands import Board
from .link import LinkError, open_board

PROJECT = "tt_um_prajesh47_splice"
STATUS_BITS = ["running", "halted0", "halted1", "h2e_full", "e2h_empty", "irq"]


def read_words(path, defines):
    """(words, context 1 start) from assembler source (.s), or from a hex file with one word per line."""
    if path.endswith(".s"):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "model"))
        from splice.asm import assemble
        with open(path) as f:
            program = assemble(f.read(), defines=defines)
        return program.words, program.ctx1
    with open(path) as f:
        return [int(line.split("//")[0], 16) for line in f if line.split("//")[0].strip()], 0


def parse_define(text):
    name, _, value = text.partition("=")
    return name, int(value or "1", 0)


def status_text(value):
    names = [name for i, name in enumerate(STATUS_BITS) if value >> i & 1]
    return f"0x{value:02x} {' '.join(names)}".strip()


def run_command(args, board, link, out=sys.stdout):
    if args.cmd == "setup":
        link.select(PROJECT)
        link.clock(args.hz)
        link.reset()
    elif args.cmd == "load":
        words, ctx1 = read_words(args.file, dict(args.define))
        board.stop()
        board.load(words)
        board.set_contexts(ctx1)
        if board.read_program(len(words)) != list(words):
            raise LinkError("program read back different from what was loaded")
        print(f"loaded {len(words)} words", file=out)
        if args.run:
            board.start()
    elif args.cmd == "run":
        board.start()
    elif args.cmd == "stop":
        board.stop()
    elif args.cmd == "status":
        print(status_text(board.status()), file=out)
    elif args.cmd == "reg":
        if args.value is None:
            print(f"0x{board.read_reg(args.reg):02x}", file=out)
        else:
            board.write_reg(args.reg, args.value)
    elif args.cmd == "push":
        board.push(args.words)
    elif args.cmd == "pop":
        print(" ".join(f"0x{w:04x}" for w in board.pop(args.count)), file=out)
    elif args.cmd == "capture":
        entries = board.read_capture()
        with open(args.output, "w") as f:
            f.write(to_vcd(entries, args.tick_ns))
        print(f"{len(entries)} entries written to {args.output}", file=out)


def build_parser():
    number = lambda text: int(text, 0)
    parser = argparse.ArgumentParser(prog="splice_host", description=__doc__)
    parser.add_argument("--port", default=os.environ.get("SPLICE_PORT"), help="serial port, or set SPLICE_PORT")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup", help="select SPLICE on the chip, start its clock and reset it")
    p.add_argument("--hz", type=float, default=50e6)
    p = sub.add_parser("load", help="stop, load a program and check it by reading it back")
    p.add_argument("file", help=".s to assemble, or a hex file")
    p.add_argument("-D", dest="define", action="append", type=parse_define, default=[], metavar="NAME=VALUE")
    p.add_argument("--run", action="store_true", help="start after loading")
    sub.add_parser("run", help="start the program")
    sub.add_parser("stop", help="stop the program and release the lines")
    sub.add_parser("status", help="print the status byte")
    p = sub.add_parser("reg", help="read a register, or write one if a value is given")
    p.add_argument("reg", type=number)
    p.add_argument("value", type=number, nargs="?")
    p = sub.add_parser("push", help="write words to the FIFO")
    p.add_argument("words", type=number, nargs="+")
    p = sub.add_parser("pop", help="read words from the FIFO")
    p.add_argument("count", type=number)
    p = sub.add_parser("capture", help="read capture memory and write VCD, which PulseView opens")
    p.add_argument("-o", dest="output", required=True)
    p.add_argument("--tick-ns", type=int, default=20, help="length of a capture tick, from the clock and prescaler")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.port:
        sys.exit("no serial port: use --port or set SPLICE_PORT")
    try:
        link = open_board(args.port)
        run_command(args, Board(link.xfer), link)
    except LinkError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
