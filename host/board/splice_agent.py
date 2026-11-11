# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Runs on the demo board's RP2350 under the Tiny Tapeout MicroPython SDK. Bridges USB serial to SPLICE's SPI port.

Copy to the board with `mpremote cp splice_agent.py :`. The host CLI starts it.

Each request is one line, and each answer is one line starting with `=` or, on an error, `!`:

    x <hex>     SPI transaction. Answers with the bytes clocked back
    c <hz>      Start the project clock
    r           Reset the project, leaving it selected
    s <name>    Select a project on the chip by name
    q <0|1>     Leave or enter quad host mode: writes SPLICE's register 23, then switches this agent to match

SCK has to stay at or below core/8 (spec section 8). This bit-bangs, so SCK is whatever MicroPython manages
with no delay added. Run the core at 8 times that or faster.
"""

SCK, MOSI, CS_N = 0, 1, 2  # ui_in bits
QUAD_IN = (1, 4, 5, 6)    # ui_in bits of IO0-3 in quad mode: MOSI, AUX_IN0-2
QUAD_OUT = (0, 4, 5, 6)   # uo_out bits of IO0-3: MISO, AUX_OUT0-2
GPIO_UI_IN0 = 17  # ttboard/pins/gpio_map_dbv3.py, GPIOMapTTDBv3. Checked against SDK v3.1.1, not on a board
GPIO_UO_OUT0 = 33


def spi_xfer(data, sck, mosi, cs_n, miso):
    """One SPI transaction, mode 0, MSB first. The pins only need value(), so this also runs on a PC."""
    answer = bytearray(len(data))
    cs_n.value(0)
    for i, byte in enumerate(data):
        got = 0
        for k in range(7, -1, -1):
            mosi.value((byte >> k) & 1)
            got = (got << 1) | miso.value()
            sck.value(1)
            sck.value(0)
        answer[i] = got
    cs_n.value(1)
    return answer


def quad_xfer(data, sck, cs_n, ios_out, ios_in):
    """One quad transaction: 4 bits each way per SCK edge, high nibble first, bit 3 on IO3."""
    answer = bytearray(len(data))
    cs_n.value(0)
    for i, byte in enumerate(data):
        got = 0
        for nib in (byte >> 4, byte & 15):
            for k in range(4):
                ios_out[k].value((nib >> k) & 1)
            got = got << 4 | sum(ios_in[k].value() << k for k in range(4))
            sck.value(1)
            sck.value(0)
        answer[i] = got
    cs_n.value(1)
    return answer


class Port:
    """The SPI port in single-lane or quad mode. `q` changes SPLICE's mode and this one together."""

    def __init__(self, single, quad):
        self.single, self.quad_fn, self.quad = single, quad, False

    def xfer(self, data):
        return self.quad_fn(data) if self.quad else self.single(data)

    def set_quad(self, on):
        self.xfer(bytes([0x05, 23, 1 if on else 0]))
        self.quad = bool(on)


def handle(line, xfer, board, port=None):
    """Answer one request line. Split from serve() so it can be tested without a board."""
    line = line.strip()
    if not line:
        return None
    op, _, arg = line.partition(" ")
    try:
        if op == "x":
            return "=" + bytes(xfer(bytes.fromhex(arg))).hex()
        if op == "c":
            board.clock_project_PWM(int(arg), quiet=True)
        elif op == "r":
            board.reset_project(True)
            board.reset_project(False)
        elif op == "s":
            getattr(board.shuttle, arg).enable()
        elif op == "q" and port is not None:
            port.set_quad(int(arg))
        else:
            return "!unknown request"
        return "="
    except Exception as e:
        return "!" + repr(e)


def serve():
    import sys
    from machine import Pin
    from ttboard.demoboard import DemoBoard

    board = DemoBoard.get()
    sck = Pin(GPIO_UI_IN0 + SCK, Pin.OUT, value=0)
    mosi = Pin(GPIO_UI_IN0 + MOSI, Pin.OUT, value=0)
    cs_n = Pin(GPIO_UI_IN0 + CS_N, Pin.OUT, value=1)
    miso = Pin(GPIO_UO_OUT0, Pin.IN)
    # Quad pins: IO0 reuses MOSI and MISO. Assumes the GPIO map is contiguous per port, as above
    ios_out = [mosi] + [Pin(GPIO_UI_IN0 + b, Pin.OUT, value=1) for b in QUAD_IN[1:]]
    ios_in = [miso] + [Pin(GPIO_UO_OUT0 + b, Pin.IN) for b in QUAD_OUT[1:]]

    port = Port(lambda data: spi_xfer(data, sck, mosi, cs_n, miso),
                lambda data: quad_xfer(data, sck, cs_n, ios_out, ios_in))

    print("=splice agent ready")
    while True:
        answer = handle(sys.stdin.readline(), port.xfer, board, port)
        if answer is not None:
            print(answer)
