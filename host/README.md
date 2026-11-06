# host/

Host software for the SPI port (`docs/architecture.md` section 8).

| File | Contents |
|---|---|
| `splice_host/commands.py` | `Board`: load, run, FIFO, registers and capture, over anything with `xfer(bytes) -> bytes` |
| `splice_host/capture.py` | Turns capture memory into timed edges, and writes VCD, which PulseView opens |
| `splice_host/link.py` | Serial link to the board agent |
| `splice_host/__main__.py` | Command line |
| `board/splice_agent.py` | Runs on the demo board's RP2350 and bridges USB serial to the SPI port |
| `tests/test_host.py` | Tests that need no board: `python3 -m tests.test_host` |

## Use

The board needs the [Tiny Tapeout MicroPython SDK](https://github.com/TinyTapeout/tt-micropython-firmware), which it ships with.

```sh
pip install pyserial mpremote
mpremote cp board/splice_agent.py :
export SPLICE_PORT=/dev/ttyACM0

python3 -m splice_host setup --hz 50e6
python3 -m splice_host load ../model/firmware/uart_tx.s -D BIT=434 --run
python3 -m splice_host push 0x55
python3 -m splice_host capture -o trace.vcd
```

## What has and hasn't been checked

- Checked: the agent's SPI routine replayed onto the RTL at SCK = core/8 (`test/test_host.py`), and the serial
  protocol and CLI against stand-ins (`tests/test_host.py`).
- **Not checked: anything on a real board.** The GPIO numbers come from the SDK's `gpio_map_dbv3.py` (v3.1.1).
  The SDK calls, the startup over the REPL and the real SCK rate have never run. Bit-banged SCK is expected to
  be a few hundred kHz, but that's an estimate. The core must run at 8 times SCK or faster.
- The clock in `setup` depends on what the SDK's PWM can produce. Its limit on this board isn't documented.
