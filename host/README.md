# host/

Host software for the SPI port (`docs/architecture.md` section 8).

| File | Contents |
|---|---|
| `splice_host/commands.py` | `Board`: load, run, FIFO, registers and capture, over anything with `xfer(bytes) -> bytes` |
| `splice_host/capture.py` | Turns capture memory into timed edges, and writes VCD, which PulseView opens |

**Not built yet:** a CLI and a connection to the real board. The demo board's microcontroller needs checking first.
