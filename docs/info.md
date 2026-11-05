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

> Status: SRAM harden experiment. All bus lines are hi-Z and `uo[3]` blinks a ~3 Hz heartbeat at
> 50 MHz. A self-test runs on two SRAM macros after reset: `uo[1]` goes high when it is done and
> `uo[2]` goes high if it passed. Other outputs are tied low.

## How to test

1. Run the clock at 50 MHz and release `rst_n`.
2. `uo[3]` (ACTIVE) should toggle at about 3 Hz.
3. All `uio` pins stay hi-Z until SPLICE is configured over the host SPI port (`ui[2:0]`, `uo[0]`).

## External hardware

- The TT demo board RP2040 acts as the SPI host.
- Interposing needs two devices wired to the A side (`uio[3:0]`) and B side (`uio[7:4]`) of each pair.
- I2C needs external pull-ups on both sides. Pads are not 5 V tolerant.
