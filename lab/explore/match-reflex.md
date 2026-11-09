# Match → MODE reflex (`ARM` / `MATCH`)

*2026-11-09, branch `explore/scout-match-reflex`. Exploration only: nothing here is approved, and no RTL in
`src/` was changed. This is idea #2 of the scout round (`git show dc3dc7f:lab/explore/scout.md`).*

**In one line:** a hit on a context's shift register switches a line's fabric source with no instruction in
between. In the golden model, one context gets bit-exact interposing at 2–3× the rate, but on decoding alone
the current ISA keeps up. In two contexts it is exact at **any** bit period from 4 cycles, where the current
ISA is exact only at even periods (from 10–12), and it decodes at higher rates. It costs
**10.8K–15.2K µm²** in a synthesis sketch, about twice the scout's estimate. Verdict in [section 6](#6-verdict).

## Contents
1. [Semantics](#1-semantics)
2. [Encoding](#2-encoding)
3. [Scenarios and method](#3-scenarios-and-method)
4. [Scorecard](#4-scorecard)
5. [Area](#5-area)
6. [Verdict](#6-verdict)
7. [Risks and what this doesn't show](#7-risks-and-what-this-doesnt-show)

---

## 1. Semantics

Each context gets one match rule and one action. Nothing is shared between the contexts.

**What's compared.** After every shift *into* SR (`IN`, `INB`), the hardware checks
`(SR & M) == (V & M)`. If `MATCH` gave a position, it also checks `BC == pos`. SR is checked after the shift,
so the bit just sampled counts. Shifts out (`OUT`, `OUTB`) and writes to SR (`SRI`, `MOV`, `PULLW`) never
trigger a match. So a match always means "the bits on the wire so far", whether it's a sliding window
(sync hunting, with no position) or a fixed field in a frame (with a position, or with `SRI 0` and a mask).

**What it switches.** It switches one fabric line's source (`HIZ`, `ENG`, `PASS`, `INV`, `F0`, `F1`, `ODB`), the
same register `MODE` writes. It can also pulse `TRIG_OUT` on the hit, for a scope.

**When it switches** (`ARM`'s `when` field):

| `when` | Line changes | Use |
|---|---|---|
| `now` | On the same clock edge as the `IN` that completed the match, exactly as if that `IN` had been a `MODE` | Force a level as soon as the decision is known (CAN error injection, cut a frame) |
| `half` | At the context timer's next **half-period point**, and stays | Switch on the next bit boundary while sampling mid-bit |
| `pulse` | From the next half-period point to the one after, then back to the source it had | Flip, force or drop exactly one bit cell |
| `off` | Never: disarms | |

**Why the half-period point.** Firmware samples in the middle of each bit, so the timer's ticks fall mid-bit,
but the change has to land on a bit boundary. The half-period point is when the period counter equals
`period >> 1`, so it falls `period - (period >> 1)` cycles after a tick. With mid-bit ticks that is the next
boundary, as the fabric sees it (the reflex and `PASS` both work on the synchronised line, so the delay
cancels). The hardware adds a 17-bit equality compare per context. With edge ticks (`TSRC`), there is no
period, so `half` and `pulse` use the tick itself. That suits a clocked bus, where the change follows the
clock edge.

**Re-arming.** With `once`, a hit disarms the rule, and firmware re-arms it with one `ARM`. Without it, the rule
stays armed and fires on every hit. While a queued or running switch is pending, new hits are ignored.
`ARM` cancels a queued switch but never cuts short a pulse already on a line, so a re-arm can't leave a line
stuck on its pulse source.

**Priority within a cycle.** Deferred reflexes (`half`, `pulse`, restore) apply first, then the instruction
executing that cycle, which can include a `now` hit from its own shift. So a `MODE` on the same line in the
same cycle overrides a deferred reflex. If both contexts aim at the same line, the last writer wins, as for
two `MODE`s.

**Two contexts.** Each context's rule watches only its own shifts, and its `half` and `pulse` follow its
own timer. Because those land on the timer's cycle, not on the context's instruction slot, they stay exact
when the context runs at half rate. This turned out to be the larger effect (section 4).

Inspirations, named and not copied (no code was read or used):
[can2040's match state machine](https://github.com/KevinOConnor/can2040/blob/master/docs/Code_Overview.md),
which compares raw bits and a bit position, then has the transmit state machine inject the ACK.
[NXP FlexIO's match-continuous shifter mode](https://www.nxp.com/docs/en/supporting-information/FTF-ACC-F1179_Introduction_to_FlexIO.pdf),
which compares on each shift. Both URLs come from the scout survey. Neither switches an interposer's
pass-through path. Doing that is the new part.

## 2. Encoding

The two codes reserved for match rules, `10101` and `10110`, are reused, so **no opcode moved**.
`lab/isa/isa_v1.py` still proves the map is a prefix code (Kraft sum 0.913, unchanged), and that the model's
table matches it. It doesn't touch `11101010`/`11101011` (`CMPU`/`TCAP` on `exp/integrate-1`).

| Opcode | Instruction | Fields (bits) | Meaning |
|---|---|---|---|
| `10101` | `ARM` | when 2, once 1, trig 1, line 3, src 3, 1 spare | Set the action and arm (`when` = 3 disarms) |
| `10110` | `MATCH` | v 2, m 2, pe 1, pos 5, 1 spare | Copy `Rv` and `Rm` into the context's value and mask. `pe`: also require `BC == pos` |

Assembler: `match r2, r3`, `match r2, r3 pos=0`, `arm 4, inv, pulse once`, `arm 5, f0, now trig`, `arm off`.
Model: `model/splice/model.py` (`reflex_hit`, `reflex_edge`, and `half_now` in `timer`).

## 3. Scenarios and method

All runs are on the golden model. Everything is in `lab/explore/match_reflex.py`
(`python3 lab/explore/match_reflex.py`). Both programs in each pair get the same waveform, harness and pass rule,
and each gets its own best constants from a sweep at each rate.

**Scenario 1: inline UART case flip** (`model/firmware/explore/uart_case_{base,reflex}.s`). RX passes through
to TX. Every byte whose bits 0–4 equal a rule has bit 5 inverted in flight (`a` ↔ `A`). Bit 4 decides, and the
flip must start on the bit-5 boundary. The stream is 16 back-to-back frames with 1 stop bit, half of them
matching. In two contexts the program runs in both directions at once (line 0 → 4 and line 1 → 5), with a
different rule each way.
- **exact**: the output equals the ideal output (input with the chosen bit cells inverted) delayed by the
  2-cycle pass latency, on **every cycle**.
- **decoded**: a UART receiver at the true rate, sampling mid-bit, reads the expected bytes with valid stop bits.
  Run at nominal and at ±2% source clock error.

**Scenario 2: sync-word hunt** (`model/firmware/explore/sync_{base,reflex}.s`). A raw NRZ stream with a 16-bit
sync word at 6 random offsets, and nowhere else. The bit after each sync word must come out inverted,
cycle-exact. The window has to be compared after **every** bit.

**Baselines**, written to be as fast as the current ISA allows:
- Scenario 1: `SRI 0` per frame, so SR is exactly bits 0–4 at the end and one `CMP sr, r2` decides.
  `TPHASE half` comes before the compare, so `MODE.t` lands on the boundary tick.
- Scenario 2: `in.t` / `cmp` / `jmp nz` per bit. On a hit, `DLYR` places two untimed `MODE`s on the
  boundaries, and sampling carries on through the flip, so no sync word can be missed. Its alignment
  constant `ADJ` is swept along with the preset.

## 4. Scorecard

Rates are in cycles per bit at 50 MHz. Lower is faster: 8 cycles is 6.25 Mbit/s, 4 is 12.5 Mbit/s. "Fastest"
means that rate and every slower one up to 40 cycles pass.

### Scenario 1: inline UART case flip

| | Current ISA | Reflex |
|---|---|---|
| Program words, 1 context / both directions | 20 / 41 | **16 / 33** |
| Slots from the deciding sample to the earliest line change | 4 (`TPHASE`, `CMP`, `JMP`, `MODE.t`) | **0** with `now`. `pulse` lands on the timer |
| 1 context, exact at | every period from **8** (6.25 Mbaud) | every period from **3** (16.7 Mbaud) |
| 1 context, fastest decoded at −2% / nominal / +2% | 5 / 4 / 4 | 4 / 4 / 4 |
| 2 contexts, both directions, exact at | **even periods only, from 12**: 12, 14, … 40 | **every period from 4** |
| 2 contexts, fastest decoded at −2% / nominal / +2% | 9 / 6 / 8 | **7 / 4 / 4** |

The decode sweep stops at 4 cycles a bit, so "4" means 4 or better. **Decoded alone, one context is a tie:** a
flip a cycle or two off still decodes, because the receiver samples mid-bit. The one-context gain is
cycle-exactness, not function.

*Correction made during the run:* my first reflex program reached `WAIT` 2 slots later than the baseline, and
decoded worse at −2% (13 against 9 in two contexts). With both programs reaching `WAIT` at the same point,
those are the numbers above. The baseline wasn't changed.

### Scenario 2: sync-word hunt

| | Current ISA | Reflex |
|---|---|---|
| Program words, 1 context / 2 contexts | 18 / 37 | **13 / 27** |
| Instructions per bit while hunting | 3 | **2** (`in.t`, `jmp`) |
| Slots from the deciding sample to the earliest line change | 4 (`CMP`, `JMP`, `DLYR`, `MODE`) | **0** |
| 1 context, fastest exact | 5 (the sample may sit late in the bit); **6** with the sample held mid-bit | **2** |
| 2 contexts, fastest exact | **10, and even periods only**: exact at 10, 12, … 40, never at 11, 13, … 39 | **4, any period** (5, 7, 9, 11, 13, 39 all checked) |

**Why the current ISA is exact only at even periods in two contexts.** A context issues on alternate
cycles, so any `MODE` it executes lands on a cycle of one parity. With an odd bit period, consecutive bit
boundaries alternate parity, so every second flip is a cycle off. The reflex's `half` and `pulse` land on the
timer's cycle, which is independent of the slot. A cycle off at 20 ns doesn't stop a receiver decoding, but it
breaks the claim that every change lands on an exact cycle.

**Commands:**
```
python3 lab/explore/match_reflex.py                   # every number above; about 40 minutes
python -m pytest model/tests                          # 26 passed, 12 of them in test_explore_reflex.py
cd lab/isa && python3 isa_v1.py                       # prefix code and model table
```
Its output, which these tables come from, is in [section 4.1](#41-harness-output).

### 4.1 Harness output

Lists of consecutive rates are shortened with "…". "Mid-bit 40" for the two-context baseline means 39 failed,
because 39 is odd.
```
Scenario 1: inline UART case flip
  1 ctx base   words= 20 exact at: [8, 9, 10, … 40]
      decoded, fastest at -2% / 0 / +2%: 5 / 4 / 4
  1 ctx reflex words= 16 exact at: [3, 4, 5, … 40]
      decoded, fastest at -2% / 0 / +2%: 4 / 4 / 4
  2 ctx base   words= 41 exact at: [12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38, 40]
      decoded, fastest at -2% / 0 / +2%: 9 / 6 / 8
  2 ctx reflex words= 33 exact at: [4, 5, 6, … 40]
      decoded, fastest at -2% / 0 / +2%: 7 / 4 / 4
Scenario 2: sync-word hunt
  1 ctx base   words= 18 exact at: [5, 6, 7, … 40]     fastest with the sample held mid-bit: 6
  1 ctx reflex words= 13 exact at: [2, 3, 4, … 40]     fastest with the sample held mid-bit: 2
  2 ctx base   words= 37 exact at: [10, 12, 14, … 40] (even only)   mid-bit: 40
  2 ctx reflex words= 27 exact at: [4, 5, 6, … 40]     fastest with the sample held mid-bit: 4
```

## 5. Area

`lab/explore/reflex_sketch.v`, using `lab/isa/area/synth.sh` with the slow-corner CMOS5L library (yosys,
synthesis only). It covers two contexts: rule and action registers, the compare on the next SR value, the
`BC` position compare, the half-period compare per context, pulse state, and a second write port on the
fabric's eight 3-bit source registers. `BASELINE` builds only the existing `MODE` write port, and is subtracted.

| Variant | Cells | µm² | Flops | Added µm² |
|---|---:|---:|---:|---:|
| Existing `MODE` write only (`-DBASELINE`) | 119 | 2,025 | 24 | |
| Reflex, `MATCH` copies value and mask (as modelled) | 1,110 | 17,199 | 138 | **15,174** |
| Reflex, compares against live `R2`/`R3` (`-DLIVE_REGS`) | 974 | 12,847 | 74 | **10,822** |

That is 9.1% or 6.5% of the roughly 167K µm² of standard cells on `exp/isa-crc-final` (the scout's figure). The
scout estimated 6–8K. The 64 value/mask flops account for 4.35K of the 15.2K (the difference between the two
variants). The rest is the compares, the action and pulse state, and the line-indexed writes to the source
registers from three writers. Not included: decode, `TRIG_OUT` OR-ing, and timing. The 16-bit compare sits on
the SR next-state path, the same path `IN` already uses. Untried ways to cut it: one rule shared by both
contexts, an 8-bit compare (CAN identifiers need 11), or dropping `pos` (a mask plus `SRI 0` does the same).

## 6. Verdict

**A real but narrow win, and not worth 10–15K µm² as specified now.**

- **One context:** the fastest bit-exact rate improves 8 → 3 cycles a bit for the UART flip, and 6 → 2 for
  sync hunting with mid-bit sampling. But on decoding alone, the current ISA reaches 4–5 cycles too. And it is
  already bit-exact at 6.25 Mbaud, above real UARTs and near the provisional 33 MHz pad limit. In one context
  this is precision at rates mostly out of reach electrically, not new capability.
- **Two contexts:** this is where it matters. Interposing both directions at once is SPLICE's distinguishing
  demo. There the current ISA keeps every change on an exact cycle only when the bit period is even (from 12
  for the UART flip, 10 for sync hunting). The reflex keeps it at every period from 4, decodes faster
  (4 against 6 cycles nominal, 7 against 9 at −2%), and its programs are shorter (33 against 41 words,
  27 against 37).
- **The part that buys most of the two-context win is the half-period landing,** not the matching. A
  cheaper "timed `MODE`" (the scout's idea #3: a `MODE` that waits for the next half-period point) would
  likely fix the parity problem for a fraction of the area. **This is a hypothesis: it wasn't built or
  measured here.** The match itself buys the zero-instruction decision and the shorter hunting loop.

**Recommendation:** don't build `ARM`/`MATCH` at 15K. Prototype the half-period `MODE` next (one opcode
qualifier plus the 17-bit compare per context), and measure it on these same two scenarios. Keep `ARM`/`MATCH`
reserved with the field layout above, and build it only if a protocol turns up where the decision and the
action are too close for instructions in two-context mode (inline CAN ACK on both sides is the candidate).

## 7. Risks and what this doesn't show

- **Model only.** No RTL, no differential test against RTL, no gate-level run. The reflex timing is my spec.
- **I designed both programs.** The baselines are as tight as I could make them, but a cleverer one might gain
  a cycle (for example by sampling bit 4 earlier, which trades away clock tolerance). Scenario 2's baseline
  was measured both ways.
- **Ideal electricals:** edges on the 20 ns grid, no jitter. Scenario 2 has no clock-error runs.
- **CAN ACK and error injection weren't measured.** The software CAN receiver spends about 15 instructions a bit
  on destuffing, so a reflex on its SR would fire after that path, not at the sample. That makes CAN the weakest
  case for a reflex on SR, and the strongest case for matching on the raw line before destuffing (not designed).
- **Interaction with the open-drain bridge (`ODB`)** wasn't tested. A reflex that switches a bridged line away
  from `ODB` mid-transfer needs the same care as a `MODE` doing it.
- **The 64 value/mask flops** are about 29% of the area. The `LIVE_REGS` variant saves them, but ties up two
  registers while armed, which CAN and USB firmware can't spare.
