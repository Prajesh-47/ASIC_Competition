# What we are building, and where it stands

This is the plain-language summary of SPLICE. The technical source of truth is
[`architecture.md`](architecture.md). If the two disagree, that spec wins and this page needs fixing.

*Last updated: 2026-11-05 (week 1).*

## The goal

SPLICE is a solo entry for
[Jane Street's protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/).
The brief is an open-source chip that can speak hardware protocols such as UART, SPI and I2C, and
that can be reprogrammed for new protocols after fabrication. Jane Street will pay to fabricate the
most novel designs. Submissions close on **January 18, 2027**, and the target is the March 2027
shuttle (the shared chip run).

The design is built with Tiny Tapeout on IHP's 130 nm CMOS5L process, in 6x4 tiles (about 0.92 mm²
of die) at a 50 MHz target clock.

## The idea

SPLICE is a protocol emulator you reprogram in firmware, including for protocols nobody planned for. A
protocol is a program you load, not fixed hardware, and it can run as either end of the conversation.

The same engine also controls the pass-through path of each bus line, which adds something an ordinary
emulator can't do: SPLICE can sit **in the middle of a live connection between two chips** and alter the
traffic as it passes:

```
 Chip A  <---->  SPLICE  <---->  Chip B
                   |
         watches, emulates or alters
         the traffic, under firmware control
```

It has three modes, all run by the same programmable core:

| Mode | What it does | What it is for |
|---|---|---|
| **Emulate** | Drives and reads the pins directly | Talking to any UART, SPI or I2C device |
| **Observe** | Passes traffic through and records when each signal changes | Sniffing a bus between two chips without cutting traces |
| **Interpose** | Alters traffic as it passes | Flipping a bit, dropping data or rewriting a byte to test how a device copes |

**Example.** A microcontroller reads a sensor over UART. SPLICE sits between them, waits for the
start of a byte, counts forward to a chosen bit, inverts that one bit, then goes back to passing
traffic through. The microcontroller receives a corrupted byte on an exact, repeatable cycle, which
shows whether its error handling works.

## How it works

- **Timing engine:** a small processor where every instruction takes an exact, known number of clock
  cycles, so firmware timing can be proven rather than measured. Protocols are firmware, not fixed
  hardware, which is what makes the chip reprogrammable.
- **Line fabric:** a switch on each of the 8 bus lines, which the engine can change on an exact
  cycle. A line can be driven by the engine, copy its partner line, invert it, be forced high or low,
  or be released. The novel part is that this one mechanism provides all the interposing.
- **Open-drain bridge:** extra logic that lets I2C pass through. Either side of an I2C bus can pull a
  line low, so SPLICE has to mirror that without latching itself up. We plan to prove this formally.
- **Capture unit:** stores timestamped signal changes in memory. Host software converts them for
  PulseView, so users get its protocol decoders for free.
- **Host interface:** an SPI port that a computer or microcontroller uses to load firmware and read
  back results.

## Why verification matters here

The competition specifically looks for novel verification, so it is a headline feature rather than a
final step. The plan includes a separate Python model of the chip compared with the hardware cycle by
cycle, formal proofs of timing and of the I2C bridge, random traffic tests, and tests on the final
gate-level netlist. Because AI tools help write this project, [`ai-log.md`](ai-log.md) records what
they wrote and how each piece was checked independently.

## Where we are

**Done in week 1**

- The repo is set up from the Tiny Tapeout CMOS5L template. Only a placeholder design exists: a
  heartbeat counter and a self-test of the two SRAM blocks, with all bus lines safely released at
  reset.
- The placeholder has been through the full chip-build flow at 6x4. Harden, gate-level test and Tiny
  Tapeout's precheck all passed, with 0 DRC and 0 LVS errors.
- The GitHub Pages viewer job fails because Pages isn't enabled yet. This does not affect the chip.
- Memory area was estimated by synthesis, and the use of IHP's SRAM blocks on this process was
  researched ([spec section 7](architecture.md#7-memory)).

**Not started:** the engine, line fabric, capture unit and host interface. Engine RTL waits for the
instruction set approval below.

## Decisions

**Memory (approved and test-built).** SPLICE will use two IHP SRAM blocks: 256x16 for the
program and 512x16 for capture, using about 8% of the die. Separate blocks mean an instruction fetch
and a capture write can never collide, which keeps the design simple to prove. A test build with both
blocks passes the full flow, including precheck and gate-level tests.

The fallback is 128x16 + 64x16 built from standard latches. It fits, at about twice the area for a
quarter of the storage, but 64 words of capture holds only a few bytes of SPI traffic. The options
compared are in [spec section 7.3](architecture.md#73-decision-two-macros-not-one-unified-macro).

**Instruction set (still needed).** It has to be frozen as v1 in week 2.

## What is still uncertain

- **SRAM acceptance:** confirmed. The organisers say IHP SRAM macros may be used, including more than
  one, if they fit and pass precheck.
- **SRAM on real chips:** unproven on this process. CMOS5L has no silicon back yet, and the only silicon
  report we found is from the related SG13G2 process.
- **Area figures:** these are synthesis estimates from an older local tool version. The method landed
  within 3% of the real flow on the placeholder, but that design contains no memory, so the memory
  numbers have not been confirmed by a full build.

## What comes next

| When | Work | Gate |
|---|---|---|
| Week 2 (Nov 16–22) | Python model and assembler; instruction set frozen | Model runs UART transmit |
| Week 3 | Engine RTL and host interface; model-vs-hardware comparison | UART and SPI master pass |
| Week 4 | I2C firmware, timing proofs, first full build | A valid competition entry |
| Weeks 5–8 | Line fabric, capture, then UART and I2C interposing | Observe and Interpose working |
| Weeks 9–10 | Timing closure, final tests, documentation and demos | Submitted by Jan 18 |

If time runs short, features get cut in this order: CRC instruction, then capture features and depth,
and only as a last resort the I2C bridge. Verification, the week-4 valid entry and UART interposing
are not cut, because interposing is what sets SPLICE apart.
