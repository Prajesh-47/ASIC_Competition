![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg)

# SPLICE: programmable bus interposer ASIC

Entry for the [Jane Street protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/).
Tiny Tapeout, IHP 130 nm **CMOS5L**, **6x4 tiles**, 50 MHz.

A protocol emulator you reprogram in firmware, including for protocols nobody planned for. The same
cycle-exact engine can also sit **inline** on a live bus between two chips and alter traffic as it passes.

| Mode | What it does |
|---|---|
| **Emulate** | Engine drives/reads pins directly (bit-banging UART / SPI / I2C) |
| **Observe** | Transparent A↔B pass-through + timestamped edge capture |
| **Interpose** | Engine switches each line's pass-through mux on an exact cycle: flip, drop or inject bits |

Plain-language overview: [`docs/overview.md`](docs/overview.md) · Architecture spec: [`docs/architecture.md`](docs/architecture.md)

## Status

**First working engine.** A program loaded over SPI after reset drives exact UART frames, and the same
netlist then runs SPI, UART RX and I2C with no reset, plus an inline bit flip and two contexts at once.
Random programs match the golden model on every output pin, every cycle. Not built yet: the capture
unit and open-drain bridge. Nothing has been on silicon.

## Repository layout

| Path | Contents |
|---|---|
| `src/` | RTL. Top: `tt_um_prajesh47_splice.v` |
| `test/` | cocotb tests (RTL and gate level) |
| `docs/info.md` | Tiny Tapeout datasheet page |
| `docs/architecture.md` | SPLICE architecture spec |
| `docs/overview.md` | Plain-language summary of the project and its status |
| `docs/isa/` | ISA studies, results sheets, and everything tried |
| `docs/ai-log.md` | What was AI-written and how it was independently checked |
| `info.yaml` | TT project config (tiles, pinout, sources) |
| `macro/` | IHP SRAM macro views, integration notes, and the evidence for each disabled flow check |
| `flow/` | Our power step for the macros and a GDS check of it |
| `lab/isa/` | Test bench behind the ISA studies. Lab code, not the golden model |
| `model/` | Golden model, assembler and example firmware |
| `formal/` | Placeholder: SymbiYosys proofs |
| `host/` | Placeholder: CLI, sigrok export |

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
