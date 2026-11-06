# ISA v1 exploration

Overnight study for choosing ISA v1, run on 2026-11-05. **Nothing here is approved.** No engine RTL was
written, and all of this is on the local branch `exp/isa-lab`.

- [`results.csv`](results.csv): the measured numbers.
- [`experiments.csv`](experiments.csv): every idea considered, tried or rejected, and why.
- [`../../lab/isa/`](../../lab/isa/): the test bench, to reproduce all of it.

> **Update, 2026-11-06:** the organisers say flexibility is the main thing they look at, so I ran a second study. See [`flexibility.md`](flexibility.md), which adds to this recommendation and supersedes parts of the proposed ISA below.

## Recommendation

**Choose candidate B, "Loom-2".** It is the spec's engine plus fused bit instructions, a bit timer whose
tick can follow an input edge, and two hardware contexts. Reserve encoding space for hardware match
rules as a stretch feature.

The study changed what I think the problem is:

1. **Cycles aren't the bottleneck.** On most workloads, every variant reaches a bit period of a few
   cycles, above what the pads can drive anyway (33 MHz output limit).
2. **Area isn't the bottleneck.** The engine sketch is about 15–31K µm², out of roughly 500K µm² placeable
   after the macros. Those sketches leave out the fabric, capture unit and host interface.
3. **What differs is code size and concurrency.** Fused instructions cut most firmware by 22–43%, and
   doing two directions at once separates the candidates sharply.

## Candidates

| | Candidate | Adds to the spec's engine | Sketch area (µm²) | Both directions at once |
|---|---|---|---:|---|
| V0 | Spec draft | Nothing | 14,872 | Polling only: not exact, 40 words, bit period ≥ 30 |
| A | Loom | Fused bit ops, tick qualifier, bit timer, tick source, no-shift `OUT`, side-set, `RAND` | 22,486 | Same as V0 |
| **B** | **Loom-2** | A plus two contexts | **30,998** | **Flips exact at 115,200 baud in 16 words; rewriting decodes correctly in 40** |
| C | Loom-2 + match rules | B plus hardware byte matchers | About 61,000 (B plus the match delta, not synthesized together) | 11 words, with no engine involved |
| D | Two separate engines | A twice, plus a second program macro | About 73,000 (estimated, includes 28,127 for the macro) | Rewriting works down to a bit period of 4 |

**Why B over A:** concurrent rewriting in both directions is the capability the pitch promises. Polling
can't do it exactly, and B costs only 8.5K µm² more.

**Why B over D:** D's only measured advantage is a minimum bit period of 4 instead of 6, and 6 cycles is
already 8.3 Mbaud. It costs more than twice the area.

**Why not C now:** the matcher is the most novel thing tested, and no entrant I surveyed describes
anything like it. But it costs about 30K µm², was only tested on UART framing, and has to work on SPI
and I2C to be worth a headline. Reserving the encoding keeps the option open for week 7.

## Measured results

Periods are in clock cycles at 50 MHz. Each number comes from firmware that ran on the cycle-exact model
and was accepted by a decoder that reads only the pins.

**Program size in words at the nominal rate**

| Workload | V0 spec | V1 PIO-style | V2 deadline | A/B Loom | Loom vs spec |
|---|---:|---:|---:|---:|---:|
| UART TX | 14 | 6 | 17 | 8 | −43% |
| UART RX | 9 | 9 | 10 | 7 | −22% |
| SPI master, full duplex | 18 | 13 | 18 | 13 | −28% |
| I2C master, two bytes | 36 | 33 | 36 | 23 | −36% |
| UART bit flip, inline | 8 | 8 | 12 | 10 | **+25%** |
| UART rewrite, inline | 28 | 26 | 28 | 20 | −29% |
| Seeded fuzz step | 29 | 29 | 29 | 21 | −28% |
| SPI inline, replace a byte | 21 | Not run | Not run | 15 | −29% |

The bit flip gets worse because timer setup costs more than the delays it replaces. A deadline timer
alone (V2) makes programs longer than the spec, which is a useful warning: the savings come from fusing,
not from the timer.

**Minimum period**

| Workload | V0 | V1 | V2 | A/B Loom |
|---|---:|---:|---:|---:|
| UART rewrite, bit period | 13 | 9 | 13 | **4** |
| I2C master, quarter period | 3 | 3 | 3 | 1, ignoring bus rise time |
| SPI master, half period | 4 | 4 | 4 | 4 |
| SPI inline, half period | 6 | Not run | Not run | 6 |

UART rewrite is the only place Loom clearly saves cycles: it has less to do between frames. V1 is limited
to whole multiples of its divider, so its UART runs at 432 cycles per bit instead of 434.

**Two directions at once**

| Workload | Variant | Words | Min bit period | Exact at 115,200 baud |
|---|---|---:|---:|---|
| Flip one bit both ways | V0, V1, V2, V3 polling | 40 | 30 | No, decodes correctly but edges move |
| | Rules (V4) | 11 | 4 | Yes |
| | **Two contexts (B)** | 16 | 4, not exact there | **Yes** |
| Rewrite a byte both ways | **Two contexts (B)** | 40 | 6 | Decoder accepts the bytes |
| | Two engines (D) | 40 | 4 | Decoder accepts the bytes |
| Flip after matching a byte | Two contexts (B), software | 34 | 6 | Decoder accepts the bytes |
| | Hardware matcher (V7) | 11 | 4 | Decoder accepts the bytes |

**Reaction, pin edge to pin effect:** software 4 cycles, hardware rule 3 cycles.

**Sketch area, slow corner, synthesis only**

| Sketch | µm² | Flops | Longest logic path, no wires |
|---|---:|---:|---:|
| Base | 14,872 | 144 | 3.54 ns |
| Bit timer | 17,426 | 172 | 3.76 ns |
| Loom without tick source | 21,537 | 204 | 3.47 ns |
| **Loom (A)** | 22,486 | 210 | 3.96 ns |
| Loom + two-line `MODE` | 22,991 | 210 | 3.78 ns |
| **Loom-2 (B)** | 30,998 | 322 | 3.95 ns |
| Loom + 2 rules | 39,044 | 370 | 3.47 ns |
| Loom + rules + 2 matchers | 52,828 | 500 | 3.77 ns |
| Tomasulo storage only (no functional units, no fetch) | 19,322 | 226 | Not measured |

The zero-cost jump (Question 2) looks safe so far: 6.56 ns worst macro read plus about 4 ns of logic,
against a 20 ns cycle. Wires, buffers and setup aren't counted.

## The Tomasulo question

**Reject it, on measured evidence.** I replayed real firmware traces through an ideal out-of-order core
with unlimited reservation stations, perfect branch prediction and free renaming. Pin operations and
timed waits kept their order, because those are the point of the program.

| Workload | In-order cycles | Ideal out-of-order | Speedup |
|---|---:|---:|---:|
| UART TX | 21,283 | 21,229 | 1.003× |
| UART RX | 20,624 | 20,570 | 1.003× |
| SPI master | 2,025 | 1,970 | 1.028× |
| UART flip | 18,664 | 18,660 | 1.000× |
| UART rewrite | 20,200 | 20,139 | 1.003× |
| Fuzz step | 21,336 | 21,211 | 1.006× |

The best case is 2.8%, and those cycles would be spent waiting for the next bit edge anyway. Its storage
alone (19.3K µm²) is larger than the whole base engine sketch. Timing anomalies in dynamically scheduled
processors would also undercut the design's main promise (Lundqvist and Stenström, RTSS 1999).

The same traces also answer what to fuse: the most frequent executed pairs are `OUT+DLY`, `DLY+JMP` and
`JMP+OUT`, which is the bit loop that `OUTB`/`INB` collapse into one word.

## Bold ideas, and how they went

| Idea | Source | Result | Verdict |
|---|---|---|---|
| Tick from an input edge (`TSRC`) | Mine | 29% less code on inline SPI, **no speed gain**, 1.6K µm² or less | Keep, for code size and for following external clocks |
| Hardware match rules | RMT match-action | 11 words vs 34, engine-free, about 30K µm² | Stretch; unproven beyond UART |
| One flop on the PASS path | Mine | Inline SPI 4.17 → 6.25 MHz, pin to pin 60 → 40 ns, flip gets 1 cycle of skew | **Your decision**, see below |
| Deadline timer alone | PRET, FlexPRET | Longer programs than the spec | Not on its own; useful with fusing |
| Two-line atomic `MODE` | Mine | 0.5K µm², no workload needed it | Leave out until one does |
| XOR overlay in the fabric | Mine | Rewrite already works by switching source mid start bit | Rejected, not needed |
| Tomasulo | Requested | At most 2.8% | Rejected |

## Bugs in the draft ISA that the bench found

These hold whichever candidate you pick:

1. **Full-duplex SPI doesn't work on spec v0.** `IN` and `OUT` both shift the one `SR`. Fix: an `OUT` that
   doesn't shift, which costs one encoding bit instead of PIO's second 16-bit register.
2. **Flipping needs a frame holdoff.** Re-arming straight after a flip treats a data edge as a new start
   bit. Hardware rules need this too, so they need a third register.
3. **Firmware must set a line's value before taking it,** or SCK glitches at startup.
4. **Each context needs its own edge memory.** With two contexts, an edge can fall in the other context's
   slot and be missed.
5. **A `PULL` that can stall is fine, and I'm reversing my earlier advice.** It only ever stalls in idle
   time, and blocking saves words. The rule becomes: only explicit wait instructions can stall.
6. **The tick source was added to the timer, so a missed tick must set `F`.** Otherwise a late program
   silently produces a wrong waveform.

## Proposed ISA v1, if you choose B

Sixteen-bit words. Prefix-code check: the sum of 2^−(opcode bits) is 0.74 including `ARM`, so there is room
to spare. Field widths below are what `lab/isa/isa_enc.py` enforces.

| Instruction | Fields (bits) | Meaning |
|---|---|---|
| `SET` | line 4, value 1, tick 1, side 2, delay 3 | Drive a line |
| `MODE` | line 3, source 3, tick 1, delay 4 | Switch a line's source: **the injection primitive** |
| `OUT` / `IN` | line 4, tick 1, side 2, delay 3–4; `OUT` adds no-shift 1 | One bit |
| **`OUTB` / `INB`** | line 4, tick 1, side 2, back 4 | **One bit, shift, loop while bits remain** |
| `WAIT` | line 4, condition 2, stamp 1 | Stall until a level or edge, with timeout |
| `WAITT` | None | Stall until the next tick |
| `TSET` | period 13, or restart the phase | Bit timer |
| **`TSRC`** | line 4, edge 1 | **Tick from an input edge** |
| `DLY` / `DLYR` | immediate 12 / register 2 | Delay |
| `PULLW` / `PULL` | count 5 | Load `SR` and the bit count; stall, or set `F` if empty |
| `PUSH`, `BCI`, `SRI`, `MSB`, `SIDE` | | FIFO and setup |
| `LDI`, `MOV`, `ADD`, `XOR`, `AND`, `CMP`, `CMPI`, `SHR`, `SHL`, `RAND` | | Data |
| `JMP` | condition 3, address 9 | Conditions: AL, Z, NZ, F, !F, BC=0, BC≠0, and a spare |
| `DJNZ` | register 2, address 9 | Loop |
| `PROBE` | line 4, tick 1 | Read a line into Z, no stall |
| *Reserved* | `ARM`, `MATCH` | Hardware rules, if the stretch goes ahead |

Scheduling is set at start: one context at full rate, or two contexts at half rate each. The two modes
were simulated separately, but the switch between them wasn't built.

## Decisions for you

1. **Which candidate?** I recommend B.
2. **Fast pass-through?** It gains 50% on inline SPI and a third on latency, at two costs. Timed flips gain
   1 cycle of skew, which a constant should fix but wasn't tested. And a line passes through one flop, not
   two, so metastability can reach the output pin. I can't measure that risk here. I lean towards making it
   a per-line option, off by default, because it can only affect lines where someone turned it on.
3. **Match rules as a week-7 stretch?** I recommend yes, provided a follow-up proves them on SPI and I2C.
4. **Update spec section 5 with the chosen ISA.** This is the step before engine RTL.

## Limits of this study

- **I wrote both the firmware and the checkers.** They are independent code, and 12 negative controls pass
  (`lab/isa/controls.py`), but a shared misunderstanding could pass both. Better firmware could also shrink
  any column, and V1's firmware in particular is an adaptation, not Raspberry Pi's.
- **Ideal electrical model.** No pad delay, bus rise time or capacitance. I2C has no clock stretching, and
  the open-drain bridge isn't modelled at all, so **no I2C interposing was tested**. That is the biggest gap,
  because it's a headline feature.
- **Area figures are synthesis sketches.** They weren't simulated or placed, they use simplified decode, and
  they leave out the fabric, capture and host interface. Compare them with each other, not against a budget.
  Candidates C and D are sums, not synthesized designs.
- **The 500K µm² figure is rough:** 60% density on the die minus macros, before routing and buffers.
- **Competitor overlap comes from README searches,** not from reading their RTL.
- **Not tested at all,** and rejected or deferred by reasoning only: VLIW, transport-triggered, stack machines,
  bit-serial, a wider instruction word, four contexts. See `experiments.csv`.

## Sources

Read directly, as extracted text:
- Edwards and Lee, [The Case for the Precision Timed (PRET) Machine](https://www.cs.columbia.edu/~sedwards/papers/edwards2007case.pdf), DAC 2007
- Zimmer, [Predictable Processors for Mixed-Criticality Systems and Precision-Timed I/O](https://www2.eecs.berkeley.edu/Pubs/TechRpts/2015/EECS-2015-181.pdf), Berkeley 2015 (FlexPRET: get time, delay until, interrupt on expire)
- Raspberry Pi, [RP2040 datasheet](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf), chapter 3 (PIO: side-set, delay field, 4-instruction UART TX)
- May, [The XMOS XS1 Architecture](https://www.xmos.com/download/The-XMOS-XS1-Architecture) and [Introduction to XS1 ports](https://www.xmos.com/download/Introduction-to-XS1-ports(3).pdf) (timed ports, timestamps, events, guaranteed thread rate)
- Texas Instruments, [TCA9517 datasheet](https://www.ti.com/lit/ds/symlink/tca9517.pdf) (avoids I2C lockup with an analog "buffered low", which SPLICE's digital pads can't copy)

Known only from search results or abstracts, so don't treat these as checked:
- Lundqvist and Stenström, "Timing Anomalies in Dynamically Scheduled Microprocessors", RTSS 1999
- Bosshart et al., ["Forwarding Metamorphosis"](https://people.cs.rutgers.edu/~sn624/552-F20/papers/rmt.pdf), SIGCOMM 2013 (RMT; the fetch failed)
- Li and von Hanxleden and others, [Kiel Esterel Processor](https://rtsys.informatik.uni-kiel.de/~biblio/downloads/papers/asplos06.pdf) (reactive processors)

Other entrants, from their READMEs: [Escapement](https://github.com/parthDOOM/escapement) (event queues, 4-context barrel), [Weft](https://github.com/fuzzylogique/weft) (`TSET`/`WAITT`, one instruction per cycle), [Tarang](https://github.com/prasadp4009/tt_um_pemu) (32-bit timed ISA, independent observer), [TD-µT](https://github.com/JessicaTemplet/td-ut-protocol-emulator) (two microcode engines).

**What this means for uniqueness:** deadline timers and multiple contexts are already common among entrants,
so neither is a differentiator. The fused instructions are close to PIO's approach. What's left that I
didn't find elsewhere is `MODE`-based interposing, following a bus clock with the same instructions, and
hardware match rules on live traffic.
