# SPLICE: what we're building and why

Start here. This page explains the project, every decision so far and the ideas behind them, in plain language.
The technical source of truth is [`architecture.md`](architecture.md). If the two disagree, that spec wins, and
this page needs fixing.

*Last updated: 2026-11-14.*

## In one paragraph

SPLICE is a chip for Jane Street's
[protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/). It speaks
hardware protocols such as UART, SPI and I2C. Each protocol is a program rather than fixed hardware, so you can
load new ones after the chip is made. It also does something an ordinary emulator can't: it can sit **in the
middle of a live connection between two chips** and alter the traffic as it passes.

**Where it stands:**
- **Written and tested:** the whole chip exists as RTL (the hardware description), in its second version
  (v2), sized for the 8x4 tiles the organisers confirmed. Its tests pass in simulation and again on the final
  post-layout netlist, and it passes Tiny Tapeout's build and precheck.
- **Protocols:** 23 protocol families run on the same chip with only a program change. 13 of them came from
  two blind trials, where the protocols weren't picked to suit the design, and needed no change to the chip.
- **Timing:** it meets 50 MHz in the worst case. The margins were thin, and a fix is being adopted
  ([`roadmap.md`](roadmap.md)).
- **Not done:** nothing has been on a real chip or board.
- **What's next:** one last round of features, a freeze on 2026-11-29, then a third trial, demos and the
  write-up. The plan is in [`roadmap.md`](roadmap.md).

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

- **The engine** is a small processor where every instruction takes an exact, known time. It has four
  **contexts**, so it can run up to four programs side by side, for example one for each direction of a bus.
  Two **link** buffers let one context's output feed another's input, so the chip can bridge one protocol to
  another (UART in, SPI out) with no host involved. Instructions can move 1, 2, 4 or 8 lines at once
  (**lanes**), read tables from program memory, and jump through a register.
- **The line fabric** is a switch on each of the 8 bus lines. On an exact cycle, firmware can make a line
  copy its partner, invert it, be forced high or low, or be driven by the engine. All interposing comes from
  this one mechanism.
- **The open-drain bridge** lets I2C pass through, where either side can pull a line low. SPLICE has to copy
  that to the other side without mistaking its own output for a new signal and latching up.
- **Memory** is two SRAM blocks: one for the program (512 words) and one for captured traffic (512 entries).
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

There's no hardware for any particular protocol. What v2 does have is a few general building blocks that
many protocols share, each set up by firmware: a CRC step with a programmable polynomial, and a line coder
(`CODE`) for NRZI, bit stuffing, Manchester and clock recovery. Everything they do can also be done in plain
instructions, and was at first; the blocks make it shorter and fast enough to share the engine between four
programs.

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
| **Memory** | Two SRAM macros: 512x16 program, 512x16 capture, 7% of the 8x4 die | A fetch and a capture write can never collide, and macros are allowed | One shared macro; flop memory (twice the area for a quarter of the storage) | Firmware outgrowing 512 words across all four contexts. The largest single program, SD with lanes, uses 246 |
| **Power for the macros** | Our own script, 117 lines | The project aims for original work over reuse, and it passes the same checks | A 667-line script copied from another entrant | Power or routing problems |
| **ISA v1** | Fused instructions, ticks, side-set, `CALL`/`RET`, a bit test, `RAND` | Smallest firmware, and it ran every protocol tried | Spec draft; PIO-style clock divider; timer alone | The gaps the trial found, which come next |
| **Tomasulo / out-of-order** | Rejected | A perfect version saves at most 2.8%, and those cycles would only be spent waiting | | |
| **Four contexts** | 1 to 4 contexts, each at 1/n rate, all with their own timers, plus two link FIFOs between them (v2, 8x4) | Altering both directions at once needs two threads of control, and bridging or running several protocols needs more. Far smaller than separate engines, and on 8x4 the area is there | Two contexts only (v1); light contexts with latch storage (+16% instead of +54%, but latches on unproven silicon) | 1/n rate being too slow for a protocol: run it with fewer contexts |
| **Bridge holdoff** | A programmable register | No fixed value works at both 100 and 400 kHz | A fixed value | Real bus behaviour |
| **Fast pass-through** | One flop instead of two, per line, off by default | 50% faster inline SPI. Only a line where someone turns it on carries the metastability risk | Always on; not having it | Testing shows the risk isn't worth it |
| **Engine decode for timing** | Instructions decoded straight from their bits, one write path per register | It fixed most of the timing problem at the slow corner without changing behaviour | A 1-cycle penalty on taken jumps, which would cost tight programs like USB | |
| **Host board** | RP2350 on the ETR demo board, bit-banging SPI | The board's pins aren't on a hardware SPI port | | The trial showed it would be far too slow for sustained traffic, so this needs work |
| **Line coder** | Built in v2 (`CODE`) | USB took half the instructions and ran in one of four contexts, which the software version couldn't | Leaving it in software, which worked but couldn't share the engine | |
| **Feature freeze** | 2026-11-29, after one last round from trial 2's gaps | Trial 2 found nothing the chip couldn't do, and every feature spends timing margin and write-up time | Adding features until the deadline | A trial finding a protocol that's blocked |
| **Timing margin** | Hold first: repair hold to 0.2 ns, and mirror the SRAM macros for setup | A slower clock fixes a setup miss on a real chip; nothing fixes a hold miss | Leaving hold at 0.08 ns | |
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

## The adaptability trials

These are the most direct answer to the competition's question. Full results are in
[`adaptability.md`](adaptability.md) and [`../lab/explore/trial2.md`](../lab/explore/trial2.md).

**Trial 1, on v1.** A different AI model picked 5 protocols without seeing the design, and I implemented them
with **no changes to the chip**, only firmware. I added 4 of my own, which count as weaker evidence.

- **At full speed:** HDLC, DALI, S/PDIF at 48 kHz, MDIO, LIN, SENT and HDMI-CEC.
- **Working, but slower than asked:** SD cards, at 6-8 MHz against 25.
- **Out of reach, as expected:** 100BASE-TX Ethernet needs a symbol every 8 ns, and a pin can only change every
  20 ns.

What got in the way mattered more than the passes: a narrow host link, too few registers, no register add, a
timer that couldn't be set from a register, contexts that couldn't pass data. **v2 was built from that list.**

**Trial 2, on v2.** Four protocols drawn at random from a list of 12 candidates: BiSS-C (an encoder interface
that has to measure and cancel its cable's delay), NEC infrared, NMRA DCC (model railway control) and
ARINC 429 (avionics). **All four work in both directions at full rate, again with no change to the chip.**
Nothing was blocked. The gaps left are about how long the firmware is, and the top few are the last feature
round before the freeze. One caution: the list of candidates was mine, so a third trial, with protocols named
by someone else, will run on the frozen chip.

## What's been shown, and what hasn't

**Passing in the real chip-build flow:** build, DRC, LVS, precheck, and the full test suite at gate level. The
slow corner passes at 50 MHz with 1.44 ns to spare, and the worst hold slack is 0.12 ns (gds run 43600548235).

**Passing in simulation, with idealised electricals:**
- UART, SPI and I2C, as controller and target.
- JTAG, SWD and PS/2.
- WS2812 and 1-Wire.
- CAN and low-speed USB, both directions, with clock recovery.
- I2C pass-through, forcing a NACK, and bit flips inside live UART.
- Up to four protocols at once, and one protocol feeding another through a link.
- The 13 protocols of the two blind trials.

**Not shown:**
- **Hardware:** anything on a real chip or board.
- **Host link:** sustained high-rate traffic through the real host.
- **Electricals:** pad delays and thresholds, since CMOS5L has none characterised yet.

## The biggest risks

1. **Nothing is proven on silicon.** CMOS5L has no chips back yet, so there are no measured pad numbers.
2. **The program lives in an SRAM macro with no silicon result on this process.** If the macro fails, so does
   the chip. A small fallback store is a candidate experiment ([`roadmap.md`](roadmap.md)).
3. **Timing margin.** Setup has 1.44 ns to spare at the slow corner and hold 0.12 ns. Hold is the one a slower
   clock can't fix, so it's the number to raise before the freeze. Four fanout violations remain; slew and
   capacitance are clean.
4. **The host on the demo board** bit-bangs SPI and hasn't run on hardware. The 4-bit host mode and `XFER`
   cut what it has to do, but only in simulation so far.
5. **Time.** This is a solo entry, with nine weeks left and a feature freeze on 2026-11-29 to protect them.

## What comes next

The full plan, with dates, is in [`roadmap.md`](roadmap.md). In short:

1. **Now:** make `main` the v2 chip, with the timing-margin fixes.
2. **By 2026-11-29:** a last round of small features from trial 2's gaps, then freeze the chip.
3. **December:** a third blind trial on the frozen chip, a tutorial, demos, and the spec rewritten as a
   reference.
4. **January:** the verification and AI-use write-ups, signoff, submission by the 18th.

## Where to read more

| File | What's in it |
|---|---|
| [`architecture.md`](architecture.md) | The full spec, including ISA v1 and the opcode map |
| [`adaptability.md`](adaptability.md) | The blind protocol trial: results, gaps found, and bugs |
| [`roadmap.md`](roadmap.md) | The review of every decision, and the plan to submission |
| [`verification.md`](verification.md) | How the design is checked, what that found, and known gaps |
| [`isa/flexibility.md`](isa/flexibility.md) | The first protocol study, the I2C bridge, and line coding |
| [`isa/exploration.md`](isa/exploration.md) | How the ISA was chosen, including Tomasulo and the research sources |
| [`../host/README.md`](../host/README.md) | Host software, the board agent, and what hasn't run on a board |
| [`../macro/README.md`](../macro/README.md) | How the SRAM is integrated, and why two checks are off |
| [`ai-log.md`](ai-log.md) | What AI wrote and how it was checked |
