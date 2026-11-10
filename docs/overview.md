# SPLICE: what we're building and why

Start here. This page explains the project, every decision so far and the ideas behind them, in plain language.
The technical source of truth is [`architecture.md`](architecture.md). If the two disagree, that spec wins, and
this page needs fixing.

*Last updated: 2026-11-07.*

## In one paragraph

SPLICE is a chip for Jane Street's
[protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/). It speaks
hardware protocols such as UART, SPI and I2C. Each protocol is a program rather than fixed hardware, so you can
load new ones after the chip is made. It also does something an ordinary emulator can't: it can sit **in the
middle of a live connection between two chips** and alter the traffic as it passes.

**Where it stands:**
- **Written and tested:** the whole chip exists as RTL (the hardware description). It passes 48 tests in
  simulation, and the same tests on the final post-layout netlist. It passes Tiny Tapeout's build and precheck.
- **Protocols:** 10 protocol families run on the same chip with only a program change. 9 more were added in a
  test where someone else picked most of them, and those needed no change to the chip either.
- **Timing:** an experiment branch now runs at the 50 MHz target in the worst case, but isn't merged yet.
- **Not done:** nothing has been on a real chip or board.

## The competition

| | |
|---|---|
| **Deadline** | January 18, 2027 |
| **Prize** | Jane Street pays to fabricate the winners, around four designs, on a March 2027 shuttle (the shared chip run) |
| **Chip** | Tiny Tapeout, IHP 130 nm CMOS5L process, 8x4 tiles, about 1.23 mm² of die (1724.16 × 710.64 µm) |
| **Judged on** | No fixed weighting. **Flexibility is the main thing:** can it be reprogrammed for protocols it wasn't designed for? Then which protocols are shown, what's unique, and how it was designed and verified. They aren't looking for the fastest chip |
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
This one is no longer just an idea: it runs in simulation at 100 and 400 kHz.

## How it works

- **The engine** is a small processor where every instruction takes an exact, known time. It has two
  **contexts**, so it can run two programs side by side, for example one for each direction of a bus.
- **The line fabric** is a switch on each of the 8 bus lines. On an exact cycle, firmware can make a line
  copy its partner, invert it, be forced high or low, or be driven by the engine. All interposing comes from
  this one mechanism.
- **The open-drain bridge** lets I2C pass through, where either side can pull a line low. SPLICE has to copy
  that to the other side without mistaking its own output for a new signal and latching up.
- **Memory** is two SRAM blocks: one for the program (256 words) and one for captured traffic (512 entries).
- **The capture unit** records timestamped signal changes. Host software turns them into a file PulseView opens.
- **The host interface** is an SPI port that a computer or microcontroller uses to load programs and read
  results. On the competition's demo board that host is an RP2350 microcontroller, running a small program in
  [`host/board/`](../host/board/). The program has to be reloaded whenever the design is selected, because
  deselecting it powers it down.

## The instruction set

The instruction set (ISA) is what firmware is written in. ISA v1 has about 40 instructions in 16-bit words,
and the full table is in [spec section 5](architecture.md#5-timing-engine-and-isa-v1). Three ideas make it
different from an ordinary small processor:

1. **Fused bit instructions.** `OUTB` sends one bit, shifts, and loops, all in one word. Most firmware came
   out 22–43% smaller than with the original draft.
2. **Ticks.** Any pin instruction can wait for a tick and then run on that exact cycle. A tick comes from a
   timer, or from an edge on an input line, so the same instructions can follow an outside clock such as SPI's.
3. **`MODE`.** This switches a line's source on an exact cycle, and it's what makes interposing possible.

There's no hardware for any particular protocol. CRCs, bit stuffing, Manchester and clock recovery are all
done in software, which is why new protocols need only a new program.

## Concepts worth knowing

These come up throughout the project. Each explains something you'll see in the docs or in my updates.

**Clock and cycles.** The chip does everything in steps of one clock cycle: at 50 MHz, one every 20 ns. A pin
can change at most once per cycle, which is why 100BASE-TX, needing a symbol every 8 ns, is impossible here.

**Synchronizer.** An input from outside can change at any moment, even mid-cycle, which can leave a flip-flop
briefly undecided (metastability). Each input passes through two flip-flops first, so the chip sees pins 2
cycles late. Firmware timing has to allow for that, and several trial bugs were about exactly this.

**SRAM macro.** A ready-made memory block from the chip manufacturer. It's far smaller than building memory
out of ordinary logic, but it has to be placed and powered carefully, which is why it has its own README.

**RTL, netlist, GDS.** RTL is the hardware written as code (Verilog). Synthesis turns it into a **netlist**,
which is a list of real logic cells and wires. Layout places and wires those cells into **GDS**, the drawing a
factory uses. **Precheck** is Tiny Tapeout's set of checks that the drawing can be fabricated.

**Timing, slack and corners.** Every signal has to get from one flip-flop to the next within a cycle. **Slack**
is the time to spare, and negative slack means the chip would fail at that speed. Chips are slower when hot and
at low voltage, so timing is checked at **corners**: slow (1.08 V, 125 °C), typical and fast. The slow corner
is the hard one.

**Slew.** How fast a signal rises or falls. A weak cell driving many inputs gives slow edges. That can make
timing numbers less accurate, and over time it's bad for reliability.

**Gate-level simulation.** Running the tests on the netlist after layout, not on the RTL. It catches cases where
synthesis did something unexpected. It also has a quirk, **X-pessimism**: a simulator treats an unknown value
(X) more cautiously than real hardware, and it caught one of our bugs that way.

**Golden model and differential testing.** A separate, simple Python version of the chip. Random programs run
on both, and every pin is compared each cycle. If they ever differ, one of them has a bug.

**Mutation testing.** Deliberately breaking the RTL one way at a time and checking that the tests notice. A
break that survives shows a gap in the tests.

**Formal proof.** A solver checks a property for every possible input, not just the ones a test tries. We use
it for the I2C bridge (it can never drive both sides) and the FIFOs (words come out unchanged and in order).

**Negative control.** A test meant to fail, to prove the real test can tell the difference. For example, CAN
receive with its clock recovery removed has to fail.

## Decisions so far

| Decision | What we chose | Why | What we rejected | What would change it |
|---|---|---|---|---|
| **Priority** | **Adaptability first, speed later** | The organisers judge flexibility above all, and aren't looking for the fastest chip | Spending more time on speed now | Adaptability being settled |
| **Pitch** | Lead with flexibility; interposing is the differentiator | A scan of 22 entrants' READMEs found nobody describing inline alteration | Leading with exact timing, which others already claim | Finding a competitor doing the same |
| **Memory** | Two SRAM macros: 256x16 program, 512x16 capture, 8% of the die | A fetch and a capture write can never collide, and macros are allowed | One shared macro; flop memory (twice the area for a quarter of the storage) | Firmware outgrowing 256 words. The largest trial program uses 223 |
| **Power for the macros** | Our own script, 117 lines | The project aims for original work over reuse, and it passes the same checks | A 667-line script copied from another entrant | Power or routing problems |
| **ISA v1** | Fused instructions, ticks, side-set, `CALL`/`RET`, a bit test, `RAND` | Smallest firmware, and it ran every protocol tried | Spec draft; PIO-style clock divider; timer alone | The gaps the trial found, which come next |
| **Tomasulo / out-of-order** | Rejected | A perfect version saves at most 2.8%, and those cycles would only be spent waiting | | |
| **Two contexts** | One context at full rate, or two at half rate | Altering both directions at once needs two threads of control, and it's far smaller than two engines | Two separate engines | Half rate being too slow in practice |
| **Bridge holdoff** | A programmable register | No fixed value works at both 100 and 400 kHz | A fixed value | Real bus behaviour |
| **Fast pass-through** | One flop instead of two, per line, off by default | 50% faster inline SPI. Only a line where someone turns it on carries the metastability risk | Always on; not having it | Testing shows the risk isn't worth it |
| **Engine decode for timing** | Instructions decoded straight from their bits, one write path per register | It fixed most of the timing problem at the slow corner without changing behaviour | A 1-cycle penalty on taken jumps, which would cost tight programs like USB | |
| **Host board** | RP2350 on the ETR demo board, bit-banging SPI | The board's pins aren't on a hardware SPI port | | The trial showed it would be far too slow for sustained traffic, so this needs work |
| **Line coder** | Deferred | Software already does CRC, stuffing and Manchester for every protocol tried | Building it before the trial's findings are ranked | The ISA review |
| **Match rules** | Stretch goal, encoding reserved | Novel, but unproven beyond UART | Building them now | Proving them on SPI and I2C |

## Verification

The competition looks for novel verification, so this is part of the design rather than a final step. The full
write-up is in [`verification.md`](verification.md).

**Done:**
- **The golden model:** compared with the RTL on 80 random programs, every pin every cycle.
- **Coverage bins:** 46, so every jump condition, wait outcome and line mode is known to have run.
- **Protocol tests:** every protocol, judged by device models written from each protocol's rules.
- **Formal proofs:** for the I2C bridge and the FIFOs, with negative controls that must fail.
- **Mutation testing:** 41 of 43 deliberate breaks were caught, and the 2 that survived led to new tests.
- **Gate-level simulation:** all tests on the post-layout netlist, in CI.

**Real bugs these found:**
- Reading N FIFO words consumed N+1.
- A FIFO pointer that stayed unknown in gate-level simulation.
- A spec gap in when a missed tick sets a flag.
- A capture counting error that was in both the RTL and the model.
- An assembler that moved labels.

[`ai-log.md`](ai-log.md) records what AI tools wrote and how each piece was checked.

## The adaptability trial

This is the most direct answer to the competition's question. A different AI model picked 5 protocols without
seeing the design, and I implemented them with **no changes to the chip**, only firmware. I added 4 of my own,
which count as weaker evidence. Full results are in [`adaptability.md`](adaptability.md).

- **At full speed:** HDLC, DALI, S/PDIF at 48 kHz, MDIO, LIN, SENT and HDMI-CEC.
- **Working, but slower than asked:** SD cards, at 6–8 MHz against 25.
- **Out of reach, as expected:** 100BASE-TX Ethernet needs a symbol every 8 ns, and a pin can only change every
  20 ns. All of its logic works, but at up to 1.25 MBd against 125.

**More useful than the passes is what got in the way.** In order of how much it matters:
- The host link: an 8-word buffer each way over a slow SPI port, with a full-duplex exchange command.
- Too few registers.
- No register-to-register add.
- A timer period that can't be set from a register.
- A flag shared by too many things.
- A `PROBE` instruction whose result reads backwards, which caused 3 of my bugs.
- Contexts that can't pass data to each other.

Fixing the most important of these is the next phase.

## What's been shown, and what hasn't

**Passing in the real chip-build flow:** build, DRC, LVS, precheck, and the full test suite at gate level. The
slow corner passes at 50 MHz with 3.16 ns to spare, with no setup or hold violations.

**Passing in simulation, with idealised electricals:**
- UART, SPI and I2C, as controller and target.
- JTAG, SWD and PS/2.
- WS2812 and 1-Wire.
- CAN and low-speed USB, both directions, with clock recovery.
- I2C pass-through, forcing a NACK, and bit flips inside live UART.
- Two protocols at once.
- The 9 trial protocols.

**Not shown:**
- **Hardware:** anything on a real chip or board.
- **Host link:** sustained high-rate traffic through the real host.
- **Electricals:** pad delays and thresholds, since CMOS5L has none characterised yet.

## The biggest risks

1. **Nothing is proven on silicon.** CMOS5L has no chips back yet, so there are no measured pad numbers.
2. **The host link.** The trial showed it's the main limit on sustained traffic, and the planned board host is
   too slow for it.
3. **Electrical rule violations remain.** Setup margin is now 3.16 ns at the slow corner, but the build still has
   30 slow edges (slew), 15 overloaded nets (capacitance) and 75 fanout violations to clear before signoff. CI
   also doesn't fail a build that misses timing: one variant missed setup by 0.57 ns and still showed green.
4. **ISA changes cost area and timing.** Every gap the trial found has a fix, but each fix adds logic.
5. **Time.** This is a solo entry, with about 10 weeks left.

## What comes next

1. **Merge the timing work** into the main branch, taking the cleanest build that meets 50 MHz, and stop
   working on speed for now.
2. **Rank the trial's gaps** by how many protocols each one helps, and propose the top few ISA and host-link
   changes for approval.
3. **Make the approved changes,** then rerun the trial to measure the difference. A fresh trial, with protocols
   picked again by someone else, would show whether the changes generalise.
4. **Then the rest:** match rules if there's time, demos, and the final documentation.

## Where to read more

| File | What's in it |
|---|---|
| [`architecture.md`](architecture.md) | The full spec, including ISA v1 and the opcode map |
| [`adaptability.md`](adaptability.md) | The blind protocol trial: results, gaps found, and bugs |
| [`verification.md`](verification.md) | How the design is checked, what that found, and known gaps |
| [`isa/flexibility.md`](isa/flexibility.md) | The first protocol study, the I2C bridge, and line coding |
| [`isa/exploration.md`](isa/exploration.md) | How the ISA was chosen, including Tomasulo and the research sources |
| [`../host/README.md`](../host/README.md) | Host software, the board agent, and what hasn't run on a board |
| [`../macro/README.md`](../macro/README.md) | How the SRAM is integrated, and why two checks are off |
| [`ai-log.md`](ai-log.md) | What AI wrote and how it was checked |
