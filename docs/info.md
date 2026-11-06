<!---
This file is used to generate the project datasheet.
-->

## How it works

SPLICE is a reprogrammable protocol emulator with a twist: as well as driving a bus
directly (bit-banging UART, SPI, I2C), it can sit **inline between two chips** on up to four
line pairs and watch, emulate or alter the traffic in real time.

A small cycle-exact timing engine (16-bit ISA, every instruction takes exactly 1 cycle plus its
delay field) controls a per-line source mux. Switching a line between pass-through, inverted,
forced and engine-driven on an exact cycle gives bit-flip fault injection, glitching and
store-and-forward rewriting. A capture unit records timestamped line transitions for
export to sigrok/PulseView.

Full architecture: see `docs/architecture.md` in the project repository.

> Status: early. The engine, SPI loader and line sources work in simulation and gate-level tests. The
> capture unit and the open-drain bridge are not built yet.

## How to test

1. Run the clock at 50 MHz and release `rst_n`. All `uio` pins stay hi-Z until a program configures them.
2. Assemble a program: `python3 -m splice.asm firmware/uart_tx.s -o uart_tx.hex` (in `model/`).
3. Load it over the host SPI port (`ui[2:0]`, `uo[0]`, mode 0, at most core/8): `0x01 0x00 0x00` then the words, big-endian.
4. Push data with `0x20` then words, and start with `0x10`. `uo[3]` goes high while running. Stop with `0x11`.
5. To change protocol, stop, load another program, and start again. Nothing else needs to change.

## External hardware

- The TT demo board RP2040 acts as the SPI host.
- Interposing needs two devices wired to the A side (`uio[3:0]`) and B side (`uio[7:4]`) of each pair.
- I2C needs external pull-ups on both sides. Pads are not 5 V tolerant.
