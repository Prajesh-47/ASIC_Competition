# Line coder: Manchester and biphase-mark receive, biphase transmit

*Branch `explore/isa-code-rx`, from exp/v2 at 501eb31. 2026-11-11. Adopted into `main` on 2026-11-15, after the mirrored macros restored the setup slack it used.*

**Question.** v2's `CODE` does NRZI, bit stuffing, the differential pair and clock recovery in both
directions, but Manchester only on transmit. Is it worth extending the coder, and what does it buy on
protocols SPLICE already has firmware for? (The blind-trial pool in `protocol-candidates.md` was not used:
it has MIL-STD-1553 and USB-PD, both of which this would help, so they stay unseen.)

**Answer.** Yes, cheaply. With no new opcode or field, Manchester now also receives, and Manchester with
NRZI set (a combination the assembler refused) is biphase-mark in both directions. On S/PDIF and DALI the
firmware gets 23-38% shorter, the S/PDIF receiver follows a wider range of sample rates and drift, and the
DALI gear runs in one of four contexts, which the software version can't do without retuning.
Generic synthesis adds about 0.2% of cells. At gds it is +0.18% synthesis area, and timing still closes, with 0.09 ns of slow-corner setup slack against 0.27.

## What changed

| Encoding | Transmit (`OUT`, `OUTB`) | Receive (`IN`, `INB`) |
|---|---|---|
| `code man` (IEEE), `man thomas` | as v2 | **new:** one sample per half-bit. The first half repeats the instruction, like a stuff bit, so `BC`, loops and `CRC` see data bits only. The bit is the second half (IEEE) or the first (Thomas). **Equal halves set `F`** |
| `code bmc` = man + nrzi, `bmc space` | **new:** a change at every bit's start, then a second change mid-bit for a 1 (`space`: for a 0) | **new:** the bit is whether the two halves differ (`space`: are equal). **No change at a bit's start sets `F`** |

`sync` and `diff` combine with both. With the period timer at a half-bit and `sync` on, an edge re-phases the
timer to half a period, so ticks land a quarter bit from the edges: mid-half, the most margin there is. `lvl`
is biphase receive's starting level, as for NRZI. Manchester and biphase still don't combine with stuffing.

The `F` on a broken pair turned out to be the useful part: it is the stop condition for DALI (the idle line
after a frame), the end of lock for S/PDIF, and it catches any missing edge inside a frame without firmware.

RTL (`src/splice_engine.v`, about 20 lines): the transmit and receive repeat terms became one, `rep_r`, still
worked out from registers only, so the instruction just selects it on the way to `pc_next` as before; v2's
critical path (program SRAM out, decode, next fetch address) gains no logic depth from the decode, which
sits on `rx_bit` and `tx_line`, away from `pc_next`. `half` now also steps on receive, and `rxp` holds the
previous half's sample. Model (`model/splice/model.py`), assembler (`code bmc [space]`) and docs section 5.4
match.

## Measurements

Same harness for baseline and candidate in each row: the golden model (`lab/explore/coderx.py`, which reuses
`spdif_edge.py`'s waveform, ideal host and pass rule, and test_trial.py's decoders), then RTL.

| Protocol | Best software on v2 | With the extended coder | What changed |
|---|---|---|---|
| S/PDIF receive | 119 words (`explore/spdif_rx_tcap.s`), 143 (`trial/spdif_rx.s`) | **91 words** (`spdif_rx_code.s`) | Data path is 2 `INB` loops; decoded bits to the host instead of raw levels |
| S/PDIF transmit | 98 words (`trial/spdif_tx.s`) | **67 words** (`spdif_tx_code.s`) | Slots are 2 `OUTB` loops; preambles unchanged |
| DALI gear (receive + reply) | 79 words (`dali_gear.s`) | **49 words** (`dali_gear_code.s`) | Receive is `INB`; the reply uses v2's Manchester transmit |
| DALI controller (backward frame) | 105 words (`dali_controller.s`) | **85 words** (`dali_controller_code.s`) | Receive only; forward frames keep the per-half collision check |

S/PDIF receive, one context, golden model, sender clock offsets -10% to +10% (Y = 30+ subframes exact after
lock, ERR and PERR low, no overflow):

| fs | trial (143 w) | TCAP (119 w) | coded (91 w) |
|---|---|---|---|
| 32 kHz | none | all | all |
| 44.1 kHz | +10% only | all | all |
| 48 kHz | -2% to +1% | all | all |
| 64 kHz | none | -10% only | **all** |
| 88.2, 96 kHz | none | none | none |

One binary covers 32-64 kHz: it calibrates from the stream (the shortest and longest of 96 edge intervals
are one state and a preamble's three, so a quarter of their sum is a state) and loads the period with `TSETR`.
At two contexts the coded receiver works at 32 kHz at every offset and the software ones at no rate at all.
Above that the limit is the preamble window, not decoding: preamble checks and the switch back to `bmc` have
to fit between ticks (about 7 instructions at one context, 3 at two), and each state is about 8 cycles at 48
kHz. Four-context S/PDIF receive would need the preamble classified with no firmware between ticks.

S/PDIF transmit runs at 48 kHz within 0.1% in one context, like the trial version. Neither runs in 2 or 4
contexts: the preamble and its rate stretch are `SET.t` sequences tuned in cycles for one context. One change
of format: the host supplies slot 31 (even parity) as bit 15 of the second word, where the trial version
forced the line to 0 at the end. A firmware alternative is `out.t; crc r, lsb; jmp bcn` with `POLY 1`, 3
instructions a bit, at one context.

DALI gear, golden model, the five cases of `test_dali_gear_addressing_and_reply` (short address, group,
broadcast at 1.1 and 0.9 Te, two not addressed): `dali_gear.s` passes in one context and **fails in four**
(its `DLYR` delays count the context's own slots, so they run four times long; it would need its delays
divided by the context count). `dali_gear_code.s` passes in both, since it counts ticks, not cycles.

RTL:
- `test_protocols.py`: `test_manchester_receive_line_coder` (DALI gear frame at a 60-cycle half bit, sender at
  1.0, 1.1 and 0.9, alone and in one of four contexts, the reply checked, then a frame with one bit's halves
  equal must come back as malformed) and `test_biphase_receive_line_coder` (S/PDIF 48 kHz, both polarities,
  sender 3% fast). Both pass, as do the USB line-coder tests.
- `test_trial.py`: S/PDIF receive and transmit tests loop over the trial and coded programs (the coded
  receiver must pass every drift offset, not just 0.1%), and the DALI tuples include the coded programs.
  The whole first-trial suite passes on this branch: 37 of 37 (run 2026-11-14).
- Differential (`test.py`): 29/29, **84/84 bins**. Prologue adds Manchester receive with equal halves (the code
  error), biphase transmit and receive at both halves; 6 new bins. Random configurations already draw man and
  nrzi together. The prologue grew 10 words, so programs grew (64/96/128/160 to 80/112/160/200 words) to keep
  context starts clear of it.
- Mutants (`test/mutate.sh`, `ONLY='^code:'`): 7 new, 5 updated for the merged repeat term. Run 2026-11-14 from a clean copy: 17 of 18 caught. The survivor, "run never saturates", is equivalent (the counter resets before it can saturate) and has been removed from the list. "CRC sees stuff bits", which looked equivalent on v2, is caught here by the Manchester receive tests.
- Model pytest 22 (one new assembler test), host 12.

## Cost

Generic yosys synthesis of the whole design (`synth -flatten; abc -g AND,NAND,OR,NOR,XOR,XNOR,MUX`): 14,180
cells at 501eb31, 14,209 here, **+29 (+0.2%)**. No flops added.

gds, run 43067325887 against 501eb31's run 43057575382 (`tt_submission/stats`). The run passed, with no
DRC, LVS, antenna, slew or cap violations:

| | 501eb31 | this branch | change |
|---|---|---|---|
| Synthesis area (µm²) | 281,973 | 282,481 | **+508 (+0.18%)** |
| Combinational cells after placement (µm²) | 150,283 | 150,577 | +294 |
| Sequential (µm²) | 105,718 | 105,718 | 0 |
| Timing-repair buffers (µm²) | 80,351 | 76,159 | -4,191 |
| Standard-cell area, total (µm²) | 351,547 | 347,352 | -4,195 (placement noise) |
| Utilisation | 36.6% | 36.3% | |
| Setup WNS, slow corner (ns) | 0.267 | **0.092** | -0.175, still met, TNS 0 |
| Setup WNS, typ corner (ns) | 7.78 | 7.57 | |
| Hold WNS (ns) | 0.080 | 0.101 | |

The logic costs about 500 µm². The total went down only because timing repair placed fewer buffers this run.
Slow-corner setup slack fell from 0.27 to 0.09 ns. The artifact has no path report, so I can't say whether
that is this logic on the critical path or the repair landing differently. The decode itself isn't on
`pc_next` (the repeat term was already there in v2, now with one more register input), but the slack is
thin, and a rerun or a path report should check it before merging.

## Verdict

Adopt. gds closes timing, though with less slow-corner slack (above). It costs no encoding space and almost no logic, the flags it adds do real protocol work
(stop conditions, lock loss), and it extends the coder to two whole families: Manchester receive (DALI, RC-5
infrared, EM4100 RFID, 10BASE-T's coding if a much faster clock existed) and biphase (S/PDIF, AES3, and the
CC line of USB Power Delivery). It is not a speed feature: S/PDIF still runs in one context at 48 kHz, held
back by preamble handling. The firmware savings come from moving the decode and its error checks into
`INB`/`OUTB` loops.

Not done or open:
- Four-context S/PDIF receive. It would need preamble classification in hardware (for example `F` with the
  violation's position) or a second context doing it through a link; not tried.
- Differential Manchester (IEEE 802.5, a change mid-bit always and at the start for a 0) isn't covered:
  `bmc space` is biphase-space (FM0), the other common variant. It would need the bit held across the halves.
- 100BASE-TX's MLT-3 (the Ethernet firmware here) is not a two-level code, so this doesn't touch it.
- The S/PDIF transmit host now computes parity; the test harnesses do.
