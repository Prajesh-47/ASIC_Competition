![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg)

# SPLICE: programmable bus interposer ASIC

Entry for the [Jane Street protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/).
Tiny Tapeout, IHP 130 nm **CMOS5L**, **8x4 tiles**, 50 MHz.

A protocol emulator you reprogram in firmware, including for protocols nobody planned for. The same
cycle-exact engine can also sit **inline** on a live bus between two chips and alter traffic as it passes.

| Mode | What it does |
|---|---|
| **Emulate** | Engine drives/reads pins directly (bit-banging UART / SPI / I2C) |
| **Observe** | Transparent A↔B pass-through + timestamped edge capture |
| **Interpose** | Engine switches each line's pass-through mux on an exact cycle: flip, drop or inject bits |

Plain-language overview: [`docs/overview.md`](docs/overview.md) · Architecture spec: [`docs/architecture.md`](docs/architecture.md)

## Status

**The whole chip is built, hardened and tested in simulation.** One netlist, with only the program changing,
runs 23 protocol families:

- **Built alongside the chip:** UART, SPI, I2C, JTAG, SWD, PS/2, WS2812, 1-Wire, CAN and low-speed USB.
- **Blind trial 1** (protocols picked by a different AI model that hadn't seen the design, plus 4 of the
  author's; no change to the chip): HDLC, DALI, S/PDIF, MDIO, LIN, SENT, HDMI-CEC and SD cards. 100BASE-TX's
  logic works but its 8 ns symbols are out of reach of a 20 ns clock.
- **Blind trial 2** (drawn at random from a list of candidates; no change to the chip): BiSS-C with line-delay
  compensation, NEC infrared, NMRA DCC and ARINC 429, each in both directions.

Up to four protocols run at once, one per context, and contexts can feed each other, so the chip can bridge
one protocol to another by itself. Inline, it passes I2C through an open-drain bridge (formally proven for
specific rise and holdoff values), forces a NACK, and flips bits inside live UART traffic.

Nothing has been on silicon or a board, and pad numbers don't exist yet for CMOS5L. Results and what got in
the way are in [`docs/adaptability.md`](docs/adaptability.md); the plan from here to submission is in
[`docs/roadmap.md`](docs/roadmap.md).

## Try it in a minute

Only Python is needed. This assembles a UART transmitter, runs it on the golden model (the same model the RTL
is compared with on every pin, every cycle) and prints what the pins do:

```sh
cd model
python3 -m splice.run firmware/uart_tx.s --push 0x55 --cycles 6000 --vcd uart.vcd
python3 -m splice.run firmware/uart_rx.s --in 0=10110001011@434 --cycles 6000    # receives 0xA3
```

The programs are in [`model/firmware/`](model/firmware/), and the instruction set is section 5 of the
[architecture spec](docs/architecture.md).

## Repository layout

| Path | Contents |
|---|---|
| `src/` | RTL. Top: `tt_um_prajesh47_splice.v` |
| `test/` | cocotb tests (RTL and gate level) |
| `docs/info.md` | Tiny Tapeout datasheet page |
| `docs/architecture.md` | SPLICE architecture spec |
| `docs/verification.md` | How the design is checked, what that found, and known gaps |
| `docs/overview.md` | Plain-language summary of the project and its status |
| `docs/adaptability.md` | The blind protocol trials: results, gaps found, and what was changed |
| `docs/roadmap.md` | Review of the decisions so far, and the plan to submission |
| `docs/isa/` | ISA studies, results sheets, and everything tried |
| `docs/ai-log.md` | What was AI-written and how it was independently checked |
| `info.yaml` | TT project config (tiles, pinout, sources) |
| `macro/` | IHP SRAM macro views, integration notes, and the evidence for each disabled flow check |
| `flow/` | Our power step for the macros and a GDS check of it |
| `lab/isa/` | Test bench behind the ISA studies. Lab code, not the golden model |
| `model/` | Golden model, assembler and example firmware |
| `formal/` | Formal proofs |
| `host/` | SPI command library, CLI, VCD export, and the demo board's agent. Not yet run on a board |

## Running tests locally

```sh
# needs iverilog + Python 3.11+
python -m venv .venv && source .venv/bin/activate
pip install -r test/requirements.txt
cd test && make
```

Gate-level: CI runs the same tests on the hardened netlist (`gl_test` job).

## License

Apache-2.0
