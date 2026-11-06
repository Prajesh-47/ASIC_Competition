# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Runs on the demo board's RP2350 under the Tiny Tapeout MicroPython SDK. Bridges USB serial to SPLICE's SPI port.

Copy to the board with `mpremote cp splice_agent.py :`. The host CLI starts it.

Each request is one line, and each answer is one line starting with `=` or, on an error, `!`:

    x <hex>     SPI transaction. Answers with the bytes clocked back
    c <hz>      Start the project clock
    r           Reset the project, leaving it selected
    s <name>    Select a project on the chip by name

SCK has to stay at or below core/8 (spec section 8). This bit-bangs, so SCK is whatever MicroPython manages
with no delay added. Run the core at 8 times that or faster.
"""

SCK, MOSI, CS_N = 0, 1, 2  # ui_in bits
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


def handle(line, xfer, board):
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

    def xfer(data):
        return spi_xfer(data, sck, mosi, cs_n, miso)

    print("=splice agent ready")
    while True:
        answer = handle(sys.stdin.readline(), xfer, board)
        if answer is not None:
            print(answer)
