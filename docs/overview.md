# SPLICE: what we're building and why

Start here. This page explains the project and every decision so far in plain language. The technical
source of truth is [`architecture.md`](architecture.md); if the two disagree, that spec wins and this page
needs fixing.

*Last updated: 2026-11-06, end of week 1.*

## In one paragraph

SPLICE is a chip for Jane Street's
[protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/). It speaks
hardware protocols such as UART, SPI and I2C, and because each protocol is a program rather than fixed
hardware, you can load new ones after the chip is made. It also does something an ordinary emulator can't:
it can sit **in the middle of a live connection between two chips** and alter the traffic as it passes.

**Where it stands:** the design is decided, but the chip doesn't exist yet. So far there's a tested memory
setup and a simulator. The engine itself hasn't been written.

## The competition

| | |
|---|---|
| **Deadline** | January 18, 2027 |
| **Prize** | Jane Street pays to fabricate the winners, around four designs, on a March 2027 shuttle (the shared chip run) |
| **Chip** | Tiny Tapeout, IHP 130 nm CMOS5L process, 6x4 tiles, about 0.92 mm² of die |
| **Judged on** | No fixed weighting. **Flexibility is the main thing:** can it be reprogrammed for protocols it wasn't designed for? Then which protocols are shown, what's unique, and how it was designed and verified |
| **You submit** | Public repo with RTL and tests, a passing Tiny Tapeout build, architecture and instruction set docs, an assembler with example programs, a verification write-up, and how AI tools were used |

## What the chip does

It has three modes, all run by the same programmable engine:

| Mode | What it does | What it's for |
|---|---|---|
| **Emulate** | Drives and reads the pins directly | Talking to any UART, SPI, I2C or other device |
| **Observe** | Passes traffic through and records when each signal changes | Watching a bus between two chips without cutting traces |
| **Interpose** | Alters traffic as it passes | Flipping a bit, or forcing an error, to test how a device copes |

```
 Chip A  <---->  SPLICE  <---->  Chip B
                   |
         watches, emulates or alters
         the traffic, under firmware control
```

**Example.** A microcontroller writes to a sensor over I2C. SPLICE passes the traffic through, counts clock
edges, and on the third byte hides the sensor's acknowledgement. The microcontroller sees an error on an
exact, repeatable bit, while the sensor sees nothing unusual. That tests whether the error handling works.

## How it works

- **The engine** is a small processor where every instruction takes an exact, known time. It has two
  **contexts**, so it can run two programs side by side, for example one for each direction of a bus.
- **The line fabric** is a switch on each of the 8 bus lines. On an exact cycle, firmware can make a line
  copy its partner, invert it, be forced high or low, or be driven by the engine. All interposing comes from
  this one mechanism.
- **The open-drain bridge** lets I2C pass through, where either side can pull a line low. SPLICE has to copy
  that to the other side without mistaking its own output for a new signal and latching up.
- **Memory** is two SRAM blocks: one for the program and one for captured traffic.
- **The capture unit** records timestamped signal changes. Host software converts them for PulseView.
- **The host interface** is an SPI port that a computer or microcontroller uses to load programs and read
  results. The program has to be reloaded whenever the design is selected, because deselecting it powers it
  down.

## The instruction set

The instruction set (ISA) is what firmware is written in. ISA v1 has about 40 instructions in 16-bit words,
and the full table is in [spec section 5](architecture.md#5-timing-engine-and-isa-v1). Three ideas make it
different from an ordinary small processor:

1. **Fused bit instructions.** `OUTB` sends one bit, shifts, and loops, all in one word. Most firmware came
   out 22–43% smaller than with the original draft.
2. **Ticks.** Any pin instruction can wait for a tick and then run on that exact cycle. A tick comes from a
   timer, or from an edge on an input line, so the same instructions can follow an outside clock such as SPI's.
3. **`MODE`.** This switches a line's source on an exact cycle, and it's what makes interposing possible.

## Decisions so far

| Decision | What we chose | Why | What we rejected | What would change it |
|---|---|---|---|---|
| **Pitch** | Lead with flexibility; interposing is the differentiator | The organisers say flexibility matters most. A scan of 22 entrants' READMEs found nobody describing inline alteration | Leading with exact timing, which others already claim | Finding a competitor doing the same |
| **Memory** | Two SRAM macros: 256x16 program, 512x16 capture, 8% of the die | A fetch and a capture write can never collide, and the organisers confirmed macros are allowed | One shared macro; latch or flop memory (twice the area for a quarter of the storage) | Firmware outgrowing 256 words: move to 512 |
| **Power for the macros** | Our own script, 117 lines, built after an earlier version that reused another entrant's code | The project aims for original work over reuse, and it passes the same checks | Keeping the 667-line copied script | Real design hitting power or routing problems |
| **ISA v1** | Fused instructions, ticks, side-set, `CALL`/`RET`, a bit test, `RAND` | Smallest firmware, and it ran every protocol tried | Spec draft (full-duplex SPI didn't work); PIO-style clock divider (no exact baud); timer alone (longer programs) | Problems found while building the engine |
| **Tomasulo / out-of-order** | Rejected | A perfect version saves at most 2.8%, and those cycles would only be spent waiting | | |
| **Two contexts** | One context at full rate, or two at half rate, chosen at start | Altering both directions at once needs two threads of control. It's far smaller than two engines | Two separate engines; polling (edges move) | Half rate turning out too slow in practice |
| **Bridge holdoff** | A programmable register | In the model, no fixed value works at both 100 and 400 kHz | A fixed value | Real bus behaviour |
| **Fast pass-through** | One flop instead of two, per line, off by default | 50% faster inline SPI in the model. Only a line where someone turns it on carries the risk | Always on (metastability risk); not having it | Testing shows the risk isn't worth it |
| **Line coder** (CRC, stuffing, NRZI, Manchester) | Build it, first on the cut list | Programs about 60% smaller, and the organisers favour it. Software works as a fallback | The old `CRC` opcode | Running short of time |
| **Match rules** | Week-7 stretch, encoding reserved | The most novel thing tested, but unproven beyond UART | Building them now | Proving them on SPI and I2C |
| **Email to Jane Street** | Not needed | Their update answered it | | |

**If time runs short, we cut in this order:** match rules, then the line coder, then capture features and depth,
and only as a last resort the I2C bridge. Verification, a valid week-4 entry, and UART interposing are not cut.

## Verification

The competition specifically looks for novel verification, so this is part of the design rather than a final
step. **Planned:** a separate Python model compared with the hardware cycle by cycle, formal proofs of timing
and the I2C bridge, random traffic tests, and tests on the final gate-level netlist.

**Done so far:** protocol checkers that read only the pins, and negative controls that feed in deliberately
broken firmware to confirm the checkers can fail. Those controls have caught real mistakes in the test
models themselves. [`ai-log.md`](ai-log.md) records what AI tools wrote and how each piece was checked.

## What's been shown, and what hasn't

**Passing in the real chip-build flow:**
- Two SRAM macros plus a small self-test: build, DRC, LVS, Tiny Tapeout's precheck and gate-level tests.
- Two macro-related checks stay turned off. [`macro/README.md`](../macro/README.md) gives the evidence for each.

**Passing in a simulator only, with idealised electricals:**
- UART, SPI and I2C as controller; SPI and I2C as target; JTAG, SWD, PS/2.
- CAN and low-speed USB, transmit only.
- WS2812 and 1-Wire, the only two written with no ISA changes allowed.
- I2C pass-through, and forcing a NACK.

**Not tried yet:**
- USB and CAN receive, and clock recovery.
- Two protocols running at once.
- 10 Mbit Ethernet beyond its line coding. It would need a 60 MHz clock, and receive wasn't attempted.

## The biggest risks

1. **The engine doesn't exist.** Everything above about it comes from a simulator. Timing and area could change.
2. **The I2C bridge.** It works only within a holdoff window that depends on real rise times, and there's no
   formal proof yet. It's also a headline feature.
3. **Nothing is proven on silicon.** CMOS5L has no chips back yet, so there are no characterized pad numbers.
   The SRAM macros have only been reported working on a related process.
4. **Electrical warnings at the macro pins.** The test build still has 15 slew and capacitance violations,
   which the real design has to clear.
5. **Time.** This is a solo entry with 10 weeks of planned work.

## What comes next

| When | Work | Gate |
|---|---|---|
| **Next, week 2** (Nov 16–22) | **Python golden model and assembler, from ISA v1** | Model runs UART transmit |
| Week 3 | Engine RTL and host interface; model-vs-hardware comparison | UART and SPI controller pass |
| Week 4 | I2C firmware, timing proofs, first full build | **A valid competition entry** |
| Weeks 5–8 | Line fabric, capture, line coder, then UART and I2C interposing; match rules if on schedule | Observe and Interpose working |
| Weeks 9–10 | Timing closure, final tests, documentation and demos | Submitted by January 18 |

## Where to read more

| File | What's in it |
|---|---|
| [`architecture.md`](architecture.md) | The full spec, including ISA v1 and the opcode map |
| [`isa/flexibility.md`](isa/flexibility.md) | Other protocols, the I2C bridge, and line coding |
| [`isa/exploration.md`](isa/exploration.md) | How the ISA was chosen, including Tomasulo and the research sources |
| [`isa/experiments.csv`](isa/experiments.csv) | Every idea tried or rejected |
| [`../macro/README.md`](../macro/README.md) | How the SRAM is integrated, and why two checks are off |
| [`ai-log.md`](ai-log.md) | What AI wrote and how it was checked |
