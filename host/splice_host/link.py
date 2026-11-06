# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Talks to host/board/splice_agent.py over USB serial."""


class LinkError(Exception):
    pass


class AgentLink:
    """`stream` needs write(bytes) and readline() -> bytes, as a pyserial port has."""

    def __init__(self, stream):
        self.stream = stream

    def request(self, line):
        self.stream.write(line.encode() + b"\r\n")
        return self._answer(line)

    def _answer(self, what):
        # Anything else on the line is REPL or SDK logging. An empty read is a serial timeout.
        while True:
            raw = self.stream.readline()
            if not raw:
                raise LinkError(f"no answer from the board to {what[:40]!r}")
            text = raw.decode(errors="replace").strip()
            if text.startswith("="):
                return text[1:]
            if text.startswith("!"):
                raise LinkError(f"board said {text[1:]} to {what[:40]!r}")

    def xfer(self, data):
        return list(bytes.fromhex(self.request("x " + bytes(data).hex())))

    def clock(self, hz):
        self.request(f"c {int(hz)}")

    def reset(self):
        self.request("r")

    def select(self, project):
        self.request("s " + project)


def open_board(port, baud=115200, timeout=5.0):
    """Open the serial port, leave whatever is running, and start the agent."""
    import serial  # pyserial. Only the real board needs it

    stream = serial.Serial(port, baud, timeout=timeout)
    stream.write(b"\x03\x03\r\n")  # Ctrl-C twice, to get to the REPL
    stream.write(b"import splice_agent; splice_agent.serve()\r\n")
    link = AgentLink(stream)
    while True:
        # The REPL echoes the import line, which has no `=` at the start, so the first answer is the banner
        if link._answer("start") == "splice agent ready":
            return link
