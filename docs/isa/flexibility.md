# Flexibility study

Follow-up to [`exploration.md`](exploration.md), run on 2026-11-06. The organisers' update says flexibility
is the main thing they look at: can the chip be reprogrammed for protocols it wasn't designed for? The first
study only covered UART, SPI and I2C. **Nothing here is approved, and all of it is in a cycle-exact model:
there is no RTL or silicon behind these results.**

- [`flex_results.csv`](flex_results.csv): every protocol.
- [`flex_results_holdoff.csv`](flex_results_holdoff.csv): the I2C bridge window.
- `lab/isa/flex.py`, `flex_fw.py`, `flex_controls.py`, `heldout.py`: to reproduce it.

## Result

**Loom-2 passes the flexibility test in the model, but it needed three small additions, which should go in
before the freeze: `CALL`/`RET`, a bit-test instruction, and a shift flag.** With those fixed, two held-out
protocols ran with no ISA changes. The I2C bridge works, and forcing a NACK works, but only with a
**programmable** holdoff, because no fixed value covers both 100 and 400 kHz buses.

## Protocols run

Each program was judged by a device model or decoder written from the protocol's rules. "Min" is the smallest
period that passed, in cycles at 50 MHz, with an ideal bus and no pad delay, so treat these as upper limits.

| Protocol | Role | Words | Result | Min period |
|---|---|---:|---|---:|
| JTAG, read IDCODE | Controller | 23 | Pass | Half period 3 |
| SWD, read DPIDR with turnaround | Controller | 44 | Pass | Half period 4 |
| PS/2, device-clocked | Receiver | 5 | Pass | Clock period 6 |
| SPI modes 0 and 2 | Target | 12 | Pass | Half period 7 (3.6 MHz) |
| SPI modes 1 and 3 | Target | 10 | Pass | Half period 6 (4.2 MHz) |
| I2C at 100 and 400 kHz | Target | 27 | Pass | Not searched |
| CAN, software CRC and stuffing | Transmit | 58 | Pass, including backing off lost arbitration | Bit 14 |
| USB low speed, software | Transmit | 59 | Pass | Bit 14; **27 with two contexts**, and USB needs 33 |
| Manchester, software | Transmit | 14 | Pass at half-bit 4 or more | Half-bit 4 |
| **I2C bridge, with clock stretching** | **Interpose** | **5** | **Pass at 100 and 400 kHz** | Not searched |
| **I2C, 3rd ACK forced to NACK** | **Interpose** | **12** | **Pass at 100 and 400 kHz** | Not searched |

### Held-out protocols

I added `CALL`, `TSTB` and the shift flag while writing the protocols above, so those protocols helped shape
the instruction set. For a fairer test, I then allowed no more changes and wrote two protocols I hadn't touched:

| Protocol | Words | Result |
|---|---:|---|
| WS2812 addressable LEDs, pulse widths checked against the datasheet windows | 18 | Pass, first run |
| 1-Wire: reset, presence, write a command, read a reply | 41 | Pass, after fixing a test that ended too early |

This is the closest test here to "protocols it wasn't designed for". It's still only two protocols, chosen by
the same author, and both are simple. A stronger version would have someone else pick them.

## What the ISA needs

Writing these programs showed what was missing from Loom. These are worth adding whichever candidate you pick:

| Addition | Why | Evidence | Cost |
|---|---|---|---|
| `CALL`/`RET`, one level | Without it, the bit-send routine would be duplicated for each field of a frame | Used by I2C target, USB and CAN | About 1.1K µm² |
| `TSTB`, test the next output bit | Software CRC, stuffing and NRZI all branch on the bit about to go out | Used by CAN, USB, Manchester, WS2812, 1-Wire | Small; not measured alone |
| Shifts set `F` to the bit shifted out | CRC feedback needs it, and there's no other flag for it | Used by CAN and USB | Small; not measured alone |
| Tick on both edges of a line | One instruction per clock edge for SPI targets, in all 4 modes | SPI target | Part of the tick source |
| **Programmable bridge holdoff** | See below | Sweep | One register |

Other limits, none of which blocked a protocol:
- **Four registers are tight.** CAN and USB use all four, plus `SR` and `BC`.
- **A 12-bit delay reaches only 82 µs,** so 1-Wire's long delays take several words.
- **There's no dedicated shift-only instruction.** Firmware uses `OUT` to an unconnected line, which works but is awkward. A named form would be clearer.
- **Together the programs total 299 words,** over the 256-word program memory. Each fits alone and the host loads what it needs, but a combined multi-protocol image would need the 512-word macro.

## The I2C bridge

This is the first test of the spec's open-drain bridge, using its state machine on modelled buses with
pull-up rise time, a controller, and a target that ACKs and stretches the clock.

**Does pass-through work?** `Y` means every byte and ACK arrived correctly, and both buses ended idle.

| Rate | Rise time | Holdoff, in cycles |||||||||||||
|---|---:|---|---|---|---|---|---|---|---|---|---|---|---|---|
| | | 0 | 1 | 2 | 4 | 8 | 16 | 24 | 32 | 48 | 64 | 96 | 128 | 192 |
| 100 kHz | 0 | · | · | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| 100 kHz | 100 ns | · | · | · | · | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| 100 kHz | 300 ns | · | · | · | · | · | · | Y | Y | Y | Y | Y | Y | Y |
| 100 kHz | 500 ns | · | · | · | · | · | · | · | Y | Y | Y | Y | Y | Y |
| 100 kHz | 1000 ns | · | · | · | · | · | · | · | · | · | Y | Y | Y | Y |
| 400 kHz | 0 | · | · | Y | Y | Y | Y | Y | Y | Y | · | · | · | · |
| 400 kHz | 100 ns | · | · | · | · | Y | Y | Y | Y | Y | · | · | · | · |
| 400 kHz | 300 ns | · | · | · | · | · | · | Y | Y | Y | · | · | · | · |
| 400 kHz | 500 ns | · | · | · | · | · | · | · | Y | Y | · | · | · | · |
| 400 kHz | 1000 ns | · | · | · | · | · | · | · | · | · | · | · | · | · |

(One cycle is 20 ns. The I2C limits are 1000 ns rise at 100 kHz and 300 ns at 400 kHz, so the last row is outside the standard.)

**What this means:**
- **Too short a holdoff fails:** the bridge reads its own slowly rising line as a new low.
- **Too long a holdoff also fails at 400 kHz:** it misses a real edge. In this model that limit is between 48 and 64 cycles.
- **No fixed value works.** A 1000 ns bus at 100 kHz needs 64 or more, and 400 kHz can't exceed 48. So holdoff has to be a register, which answers the spec's open question about holdoff against rise time.

**What this doesn't show:** the commercial TCA9517 solves this with an analog trick that our digital pads can't
use, so this window is the design's real risk, and it depends on real rise times. The model has no noise, no
real pad thresholds and only one stretch pattern. There's no formal proof yet.

## Should the chip do line coding itself?

The organisers say that for USB and Ethernet, the more of the protocol work (line coding, CRC, clock recovery)
the chip does, the better. I tested a line coder: CRC, bit stuffing, NRZI, Manchester and arbitration.

| Workload | Loom words | With coder | Loom min | With coder min |
|---|---:|---:|---:|---:|
| USB low speed transmit | 59 | 24 | Bit 14 | Bit 4 |
| USB, two contexts | 59 | 24 | Bit 27 | Bit 8 |
| CAN transmit | 58 | 22 | Bit 14 | Bit 8 |
| Manchester transmit | 14 | 6 | Half-bit 4 | Half-bit 3 |

Area: about 8.5K µm² on top of Loom-2 (40,505 vs 32,022 µm²). In the sketch there's one coder, shared by both
contexts, which is a real limit if both need it at once.

- **USB and CAN don't need it to work.** Software passes, even USB with two contexts, with little to spare (27 cycles of the 33 available).
- **It cuts those programs by about 60%,** and it's the only route to 10 Mbit Ethernet transmit.
- **Ethernet needs a clock change as well.** 10BASE-T needs a half-bit of 2.5 cycles at 50 MHz, which can't be done with whole cycles. At 60 MHz it's 3 cycles, which the coder reached in this model. Software's minimum of 4 only reaches 6.25 Mbit at 50 MHz. 60 MHz timing is untested, and it's close to Tiny Tapeout's clock ceiling.

**I recommend building it, and leaving it first on the cut list.** It supersedes the spec's `CRC` opcode. It's
cheap and fits what the organisers asked for, but software is a proven fallback for everything except Ethernet.

## Revised recommendation

**Loom-2, plus `CALL`/`RET`, `TSTB`, the shift flag, both-edge ticks and a programmable holdoff.** About 32K µm²
in the sketch. If it's built, add the line coder at about 40.5K µm².

## Limits of this study

- **Everything but WS2812 and 1-Wire influenced the ISA.**
- **Transmit only for USB and CAN.** There's no receive, so no USB or CAN clock recovery has been tried, and a CAN controller in practice needs to receive. **Ethernet receive wasn't attempted.** USB also has no real host or handshake.
- **Protocols ran alone.** Nothing tested two protocols at once, which is what the two contexts are for.
- **I wrote the firmware, the device models and the checkers.** All checks pass: 36 in `flex_controls.py` and 5 in `heldout.py`. The SPI controls exposed one weak model, which I fixed, so there may be others.
- **Ideal electricals,** apart from pull-up rise time on I2C and 1-Wire. Minimum periods ignore pads, whose CMOS5L numbers don't exist yet.
- **Area figures are unsimulated synthesis sketches,** as before.
