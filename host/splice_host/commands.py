# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""SPI command bytes for the SPLICE host port (docs/architecture.md section 8).

`Board` works over anything with xfer(bytes) -> bytes. That can be a simulation or real hardware.
"""


def word_bytes(words):
    return [b for w in words for b in (w >> 8, w & 0xFF)]


class Board:
    def __init__(self, xfer):
        self.xfer = xfer

    def load(self, words, base=0):
        self.xfer([0x01, base >> 8, base & 0xFF] + word_bytes(words))

    def set_contexts(self, ctx1, ctx2=0, ctx3=0, pull=0, push=0):
        """Start addresses of contexts 1-3, where 0 means that context isn't used: one context if all are
        0, otherwise as many as the last one used. pull and push route each context's PULL and PUSH, 2 bits
        per context with context 0 lowest: 0 host, 1 link 0, 2 link 1."""
        starts = [ctx1, ctx2, ctx3]
        used = max([k + 1 for k, pc in enumerate(starts) if pc] or [0])
        self.write_reg(0, used)
        for reg, pc in zip((4, 16, 18), starts):
            self.write_reg(reg, pc >> 8)
            self.write_reg(reg + 1, pc & 0xFF)
        self.write_reg(20, pull)
        self.write_reg(21, push)

    def read_program(self, count, base=0):
        data = self.xfer([0x02, base >> 8, base & 0xFF] + [0] * (2 * count))
        return [data[3 + 2 * i] << 8 | data[4 + 2 * i] for i in range(count)]

    def write_reg(self, reg, value):
        self.xfer([0x05, reg, value])

    def read_reg(self, reg):
        return self.xfer([0x06, reg, 0])[2]

    def start(self):
        self.xfer([0x10])

    def stop(self):
        self.xfer([0x11])

    def status(self):
        return self.xfer([0x12, 0])[1]

    def levels(self):
        """(status, engine-to-host words waiting, host-to-engine words of room)."""
        data = self.xfer([0x12, 0, 0, 0])
        return data[1], data[2], data[3]

    def exchange(self, words=(), slots=0):
        """One full-duplex XFER: pushes `words` while popping up to max(len(words), slots) words.

        Returns (words read, free). Only words the chip reported as waiting are returned, so a stored 0
        is data. Words past the room are dropped, so send no more than the free space the last call
        returned: that room can only grow until the host writes again.
        """
        words = list(words)
        if len(words) > 255:
            raise ValueError("XFER sends at most 255 words")
        count = max(len(words), slots)
        data = self.xfer([0x22, len(words), 0] + word_bytes(words + [0] * (count - len(words))))
        level, free = data[1], data[2]
        got = [data[3 + 2 * i] << 8 | data[4 + 2 * i] for i in range(min(level, count))]
        return got, max(free - len(words), 0)

    def push(self, words):
        self.xfer([0x20] + word_bytes(words))

    def pop(self, count):
        data = self.xfer([0x21] + [0] * (2 * count))
        return [data[1 + 2 * i] << 8 | data[2 + 2 * i] for i in range(count)]

    def read_capture(self):
        """Capture entries, oldest first."""
        from .capture import in_order
        pointer = self.read_reg(14) << 8 | self.read_reg(13)
        wrapped = bool(self.read_reg(10) & 0x40)
        count = 512 if wrapped else pointer
        if not count:
            return []
        data = self.xfer([0x04, 0, 0] + [0] * (2 * count))
        words = [data[3 + 2 * i] << 8 | data[4 + 2 * i] for i in range(count)]
        return in_order(words, pointer, wrapped)
