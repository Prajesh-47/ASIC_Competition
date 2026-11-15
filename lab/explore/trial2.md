# Second blind adaptability trial, on the v2 chip

*Branch `explore/trial2-v2`, from `exp/v2` (501eb31). 2026-11-11. Firmware and tests only: no change to
`src/`, the ISA or the assembler. RTL simulation (Icarus), no board.*

**Question:** what can v2, as built, do with protocols nobody picked for it? The first trial
(`docs/adaptability.md`) ran on v1. v2 adds four contexts, link FIFOs, LANES, CRCW, LDP/JMPR, TCAP, TSETR,
CODE, the XFER host link and 512 program words. This trial uses whatever of that helps.

**Answer: all four drawn protocols work, in both directions, at their required rates.** BiSS-C (the hard
one: line-delay compensation) runs at up to 4.17 MHz MA with any cable delay; NEC, DCC and ARINC 429 run at
full rate with lots of margin, and each also runs as transmitter and receiver at once in two contexts of
one chip. No protocol was blocked. The gaps are about how awkward the firmware was, not about what is
possible, and they are ranked at the end.

## Draw

The pool is `lab/explore/protocol-candidates.md`, 12 candidates. The draw:

```
python3 -c "import random; r=random.Random(20261111); print(r.sample(range(1,13),4))"
[8, 5, 6, 3]
```

| # | Protocol | Needs impossible hardware? |
|---:|---|---|
| 8 | BiSS-C encoder interface (master) | No |
| 5 | NEC infrared, transmit and receive | No: the carrier is a digital square wave, and the receive side reads a demodulating IR receiver's logic output, as any MCU does |
| 6 | NMRA DCC, command station and decoder | No: the track signal is an H-bridge driven from two logic lines, and decoders read it through an optocoupler |
| 3 | ARINC 429, transmit and receive | No, though it was the closest call: the bus is three-level bipolar, but every ARINC driver and receiver chip has two logic lines (HI and LO) on its digital side, so SPLICE drives and reads those. The analogue levels belong to the driver chip, as RS-485's do |

No redraw was needed. The protocol list says the pool is designer-chosen, which is the weaker kind of
evidence by the rules in `docs/adaptability.md`; the *draw* from it was random, but the pool wasn't.

## Rules kept

- No change to RTL, ISA or assembler. Firmware in `model/firmware/trial2_*.s`, tests in
  `test/test_trial2.py`. Run: `cd test && make COCOTB_TEST_MODULES=test_trial2`.
- Every device model and checker in the test file is written from the protocol's sources (quoted at the top
  of each section), not from the firmware: the BiSS encoder, the NEC burst decoder and receiver-module
  waveform, the DCC timing checker and track generator, and the ARINC driver checker and receiver input.
- The host is a real host: it only uses XFER (push what fits in the room last reported, pop what the level
  says is there), polling over SPI while the protocol runs.
- Sources: BiSS from TI TIDU794A and Renishaw L-9709-9005-05-A (both read for this trial; the CRC-6 check
  value 001111 for data 101 from TIDU794A is reproduced by the test's CRC); NEC from SB-Projects and Altium;
  DCC from NMRA S-9.1 (read for this trial) and S-9.2 (packet format, from memory, not re-read); ARINC 429
  from the candidate list's sources plus common knowledge of the label bit order (not re-read).

## Results

All on RTL (`test/test_trial2.py`, 17 tests). "Words" is program words out of 512. A loopback test runs a
transmitter and a receiver as two contexts of one chip, wired pin to pin by the testbench.

| Protocol | Works? | Rate reached | Words | Contexts | Tests |
|---|---|---|---:|---:|---:|
| BiSS-C master, line-delay compensation, CDM/CDS, CRC-6 | **Yes** | MA at 4.17 MHz (12 cycles a bit), any period that's a multiple of 4 cycles down from there; cable delay tested to 104 cycles (2 us, over 8 bit times) | 100 | 2 | 3 |
| NEC IR transmit, 38 kHz carrier made in firmware | **Yes** | Full rate; bursts on the 562.5 us grid to the cycle, 108 ms repeat to the cycle | 96 | 1 | 2 |
| NEC IR receive | **Yes** | Full rate; +-10% sender clock, +-120 us burst distortion | 158 | 1 | 3 |
| NEC TX + RX at once | **Yes** | | 254 | 2 | 1 |
| DCC command station (two complementary track lines) | **Yes** | Exact 58/100 us halves, idle packets when the host has none | 59 | 1 | 1 |
| DCC decoder | **Yes** | Full S-9.1 receive window (52-64 us, 90 us up), either polarity, stretched zeros | 81 | 1 | 2 |
| DCC station + decoder at once | **Yes** | | 140 | 2 | 1 |
| ARINC 429 transmit | **Yes** | 100 and 12.5 kbit/s | 44 | 1 | 1 |
| ARINC 429 receive, self-clocked | **Yes** | 100 kbit/s +-1% with +-8-cycle jitter; 12 to 14.5 kbit/s | 88 | 1 | 2 |
| ARINC TX + RX at once | **Yes** | | 132 | 2 | 1 |

**RTL status (re-run 2026-11-14/15):** all 17 tests pass. On the re-run one test failed first, `test_nec_rx_fast_errors`: it
expected error 3 (bit burst) for a frame cut off after 5 bits, and the firmware reported 4 (bit space). The
firmware is right: the five bits are whole, and what's wrong is the space after the last one. The expectation
was corrected, and nothing in the firmware changed.

### BiSS-C (drawn #8): the one that needed v2

The master must clock MA, find the Ack on SLO, measure the round trip from its 2nd MA rise to the Ack's
fall, and sample every later bit that long after the MA edge that caused it, while MA keeps running. Two
things run at once at different phases, so it takes **two contexts**:

- **Context 0** clocks MA from its timer (`set.t MA,0` / `set.t MA,1`) and forwards the host's request to
  context 1 through **link 0**.
- **Context 1** sets `TSRC MA, fall`, waits for the first MA fall, switches to `TSRC SLO, fall`, and after
  `WAIT SLO, lo` reads **`TCAP`**: the cycles between the two ticks, exact, through identical synchronizers on
  both pins. It then sets its *own* period timer (`TSET P`, `TPHASE r1`) to tick in the middle of each SLO
  bit as the master sees it. That is the whole of the line-delay compensation: two instructions.
- Bits go in with `in.t` / `inb.t` and `CRC r2, msb` on each (poly x^6 + x + 1 left-aligned). Feeding the
  received inverted CRC through the same register leaves a constant (0x0800) when it's right.
- To end the frame, context 1 waits for an MA rise and `MODE MA, f1`: MA held high regardless of context 0.
  Context 0 sees its own low not appear on the pad (`probe MA` after `set.t MA,0`) and stops, then drives
  MA to !CDM through the timeout.

Checked by an encoder model with cable delay both ways, Ack of 1 to 20 bits, 20 to 64 data bits, and a
**window mode where SLO is only right in the middle 2 cycles of each 12-cycle bit**: the master reads every
bit right at every delay from 0 to 50 cycles each way, so its sample point is within a cycle of the
centre. The reported delay equals the bench's round trip exactly. CDM reaches the encoder as MA's level
in the timeout and comes back as CDS. A flipped CRC sets the error flag. With no encoder the master
gives up after 40 bits of Ack and says so.

**Limits:** MA's period is at least 12 cycles (4.17 MHz, against BiSS's 10 MHz), set by context 0's loop:
`set.t`, `nop`, `probe`, `set.t`, `jmp`, `jmp` is 6 slots, and each slot is 2 cycles with two contexts. The
`nop` is there only because the pad reads back 3 cycles after a change. The period must be a multiple of 4
cycles: with two contexts, MA's edges can only land on context 0's slots, so an odd half period puts the
2nd rise a cycle off and the delay reads 1 short (found by a 50-cycle test; 48 works).

### NEC infrared (drawn #5)

**Transmit, one context.** The unit timer is reloaded at the end of every burst (`TSET REM` to one cycle
before the unit boundary, `nop.t`, `TSET UNIT`), so every burst starts exactly on the 562.5 us grid however
long the frame, and the next frame or repeat starts exactly 192 units (108 ms) after the last. Inside a
burst the carrier is `set.t L,1 / dly / set L,0 / djnz` on a 1316-cycle timer: 37.99 kHz, 439 cycles high.
Pulse counts per burst come from the timing constants (`.equ N1, (UNIT-HI-5*NCTX)/CAR`). Measured on RTL:
carrier 1316 cycles (1317 once per burst, where the timer is reloaded), every burst start on the grid to
0 cycles, repeat interval exactly 5,400,000 cycles.

**Receive, one context.** Polls the line once per tick of 1/16 unit (35 us), counts lows and highs, and
range-checks them with `CMPU`. A bit's value is simply the line 32 ticks (1.125 ms) after its burst ends:
still in the space means 1, so `IN` shifts the bit straight into SR with no compare. Reports frames with
the inverse checks as flags (extended NEC reports address-inverse failed, by design), repeat codes, and six
kinds of error; a glitch under 3.5 ms is ignored.

**Why not TCAP for receive:** TCAP saturates at 0xFFFF cycles = 1.31 ms, and NEC's 9 ms, 4.5 ms, 2.25 ms
and 1.69 ms all exceed it. Tick counting is coarser (35 us) but covers any length.

### NMRA DCC (drawn #6)

**Command station, one context.** Each half bit is timed from the edge that starts it (`set.t` then
`tset r0`), so branches between bits never move an edge: every half is 2900 or 5000 cycles to the cycle.
The complement line for the H-bridge changes on the same edge by side-set. The firmware adds the preamble,
start bits, XOR byte and end bit, and sends idle packets whenever the host has nothing (`PULL` sets F when
empty), because a DCC track must never stop.

**Decoder, one context.** `TSRC RX, both` makes every edge a tick and `TCAP.t` reads the half bit that just
ended. Polarity doesn't matter. A stretched zero up to 9.8 ms per half decodes, because TCAP saturating at
1.31 ms is still "a 0 half". The XOR is checked in firmware.

### ARINC 429 (drawn #3)

**Transmit, one context.** HI and LO on two lines that change together (side-set), NULL between. The label
goes out MSB first and the rest LSB first by switching `MSB` mid-word; 8 dummy `outb 15` shifts move the
label to the top of SR. Odd parity is a 1-bit CRC (`POLY 1`, `CRC r1, lsb` per bit), so the firmware puts
it right even when the host's bit 32 is wrong.

**Receive, one context.** Self-clocked: wait for a pulse on either line, sample both 1/5 bit later (exactly
one must be high), `IN A`, then wait for NULL. The receive builds the host's word with the same trick in
reverse: label in MSB mode, 8 dummy shifts, then LSB mode, so no bit reversal loop is needed.

## Gaps and awkwardness, ranked by how many of the four they hurt

Nothing blocked a protocol. These made firmware longer, slower or more fragile. "Hurts" counts the four
drawn protocols (BiSS, NEC, DCC, ARINC), not programs.

| Rank | Gap | Hurts | Where, and the awkward sequence |
|---:|---|---:|---|
| 1 | **No compare or subtract against a 16-bit constant.** `CMPI` is equality with 8 bits; `CMPU` needs both operands in registers; there's no `SUB` | 3 (BiSS, NEC, DCC) | Every range check is `ldi r1, LO / cmpu r0, r1 / jmp f, bad / ldi r1, HI+1 / cmpu r0, r1 / jmp nf, bad`: 6 to 8 words. NEC receive is 158 words and about 60 of them are range checks; DCC's `half` is a 3-way classifier of 12 words. BiSS turns TCAP into a delay with `ldi r3, (0x10000-P-H) & 0xFFFF / add r0, r3`. NEC transmit decrements a byte counter with `add r3, 0xFF / ldi r0, 0xFF / and r3, r0` |
| 2 | **Four registers.** `TO` gets borrowed as a fifth wherever no `WAIT` timeout is running | 2 (NEC, DCC) | NEC receive parks the second data word in TO (`mov to, sr` ... `mov sr, to`) and must clear it again before the next `WAIT`, or the wait times out; NEC transmit uses TO as the "second word" flag; DCC transmit holds the byte count in it and DCC receive the running XOR |
| 3 | **`CALL` is one level, and the assembler doesn't say so** | 2 (NEC, DCC) | NEC transmit's bit loop calls the burst routine, so the bit loop had to be inlined into the frame code instead of being a routine of its own. I wrote it nested first: the assembler accepted it and the return would have gone to the wrong place. DCC receive can't have a `bit` routine because `half` is already the one level |
| 4 | **Timing constants depend on the number of contexts.** With n contexts an instruction and each `DLY` count take n cycles, and edges land only on the context's slots | 2 (BiSS, NEC) | NEC transmit has `.equ NCTX` and divides its pulse `dly` by it (`dly HI/NCTX-3`) so the same file runs alone or beside the receiver. BiSS's MA period must be a multiple of 4 cycles at two contexts, or the 2nd MA rise moves a cycle and the measured delay is 1 short (an RTL test caught it at 50 cycles) |
| 5 | **16-bit time limits: `TCAP` saturates at 1.31 ms, with no prescaler** (and `WAIT`'s TO counts slots, also 16 bits) | 2 (NEC, DCC) | NEC's 9, 4.5, 2.25 and 1.69 ms intervals all saturate, so NEC receive counts timer ticks in a polling loop instead (`nop.t / add r0,1 / cmp r0,r1 / jmp z / probe / jmp lo`): 35 us resolution instead of 20 ns. DCC receive accepts a stretched zero only because "saturated" still means "long"; it can't enforce S-9.1's 10 ms upper limit |
| 6 | **No shift of SR by n.** Moving a byte to the top of SR is 8 dummy shifts on a line that doesn't exist | 2 (ARINC, DCC) | `bci 8 / sh: outb 15, sh` (DCC transmit, ARINC transmit twice) and `bci 8 / fill: inb 15, fill` (ARINC receive). It works because lines 12-15 read 1 and drive nothing, which the architecture doc doesn't promise |
| 7 | **Contexts can't signal each other except through the one PULL and one PUSH route each** | 1 (BiSS) | BiSS's receiver pushes to the host, so it has no route left to tell the clock context to stop. It uses the pin instead: `wait MA, rise / mode MA, f1`, and the clock context's `probe MA` after `set.t MA, 0` sees the forced high. A flag or a second push route would do it cleanly |
| 8 | **A pad reads back 3 cycles after the engine changes it** | 1 (BiSS) | The `nop` in BiSS's clock loop exists only to let `probe MA` see the low it just drove. With two contexts that costs 2 cycles a bit: 12 instead of 10, so 4.17 MHz instead of 5 |
| 9 | **`WAIT` and `TSRC` watch one line** | 1 (ARINC) | ARINC receive waits for a pulse on HI *or* LO with a 5-instruction poll (`probe A / jmp hi / probe B / jmp hi / djnz`), so bit starts are found to 5 cycles, not 1. Fine at 100 kbit/s; Wiegand (pool #4) would hit the same |
| 10 | **One Z flag for everything, and `CRC` sets it** | 1 (ARINC) | `tstb / out 15 / crc r1, lsb / jmp z, one` branches on the CRC's Z, not the bit. Every bit went out wrong until the branch moved up between `tstb` and the shift |
| 11 | **A tick left pending while a blocking instruction waits fires the next `.t` at once** | 1 (ARINC), designed around in 2 more | ARINC transmit's timer ran while `PULLW` waited for the host; the first `set.t` then executed straight away on the stale tick and the first HI pulse was 29 cycles instead of 2000 (RTL only: the model harness had the FIFO filled before start). Fixed with `tset HALF` after every `PULLW`. NEC transmit and BiSS restart their timers the same way. The M flag reports a *missed* tick, but nothing says a pending one is stale |
| 12 | **No local labels or namespaces in the assembler, and a duplicate label is reported as "label moved"** | 2 (DCC, ARINC) | Putting a transmitter and receiver in one image for two contexts (`combined()` in the test) hit `pre`, `byte`, `bl`, `gap`, `lab` and `lo8` defined twice. The assembler's message was `label pre moved from 62 to 13`, not "defined twice". Receiver labels were renamed by hand. Worse, `.equ` is global and the last definition silently wins when the words are emitted: ARINC's transmitter `.equ A, 0` and receiver `.equ A, 4` assembled without a word, and the combined transmitter drove the receiver's lines. The loopback test found it as "nothing received" |
| 13 | **`CODE diff` only works with NRZI, stuffing or Manchester on** | 0 (worked round) | DCC's and ARINC's complementary lines would be `CODE diff` with plain NRZ. Side-set did the job (`set.t A, 1 side=0`), so it cost nothing here, but `diff` alone silently does nothing |

**Also found, not from a protocol:** the golden model reads `LDP`'s address as 8 bits (`c.R[s] & 0xFF` in
`model/splice/model.py`), while the RTL uses 9 (`rr[...][8:0]` in `splice_engine.v`). A table above word 255
would read differently in the model and on the chip. None of this trial's firmware uses `LDP`.

**What v2 added that this trial used:** two contexts and a link FIFO (BiSS, all three loopbacks), `TCAP`
(BiSS's delay measurement, DCC's half bits), `TSETR` and the 16-bit timer (NEC's 28,125-cycle unit, DCC's
per-half reload), `CRC` with a per-context polynomial (BiSS's CRC-6, ARINC's parity as a 1-bit CRC), `CMPU`
(every range check), XFER (every host), 512 program words (the NEC pair is 254 words). Not used: LANES,
CRCW, LDP/JMPR, CODE, quad mode.

**Compared with the first trial's gaps:** "TSET takes only a constant", "no register ADD", "contexts can't
pass data", "the host link is narrow" and "no table lookup" did not come up: v2 fixed them or these
protocols didn't need them. "PROBE sets Z when the line is high" caused no bugs this time, with
`jmp hi` / `jmp lo`. "4 registers" came up again (rank 2).

## Bugs I made, and what caught them

All in firmware or tests; none needed an RTL change.

| # | Bug | Kind | Caught by |
|---:|---|---|---|
| 1 | BiSS bench didn't feed MA's pad back to the chip, so `probe MA` always read high | Test | First model run hung; trace |
| 2 | BiSS MA duty was 8/4 cycles: the `probe` sat between the two `set.t`s and pushed the rise past its tick | Firmware | Cycle trace of MA |
| 3 | BiSS never set `POLY` | Firmware | The CRC flag on known-good frames |
| 4 | BiSS window model also delayed the Ack edge, which the master measures, so it moved the sample point with it | Test | Window sweep failing at wide windows only |
| 5 | BiSS at a 50-cycle period read the delay 1 short (odd half period, two contexts) | Firmware limit | RTL test's exact delay check |
| 6 | NEC transmit called the burst routine from the bit routine: two levels of `CALL` | Firmware | Reading it before the first run; the assembler accepted it |
| 7 | NEC transmit started repeats 193 units apart, not 192 (off by one in the wait loop). My first fix didn't apply (a failed `sed`) and the rerun showed it unchanged | Firmware, then process | The independent burst decoder's start-to-start check |
| 8 | NEC receive overwrote the first data word with the second (`mov r2, sr` ran on both passes) | Firmware | Receiver model run against the waveform generator |
| 9 | NEC model harness stopped 1,000 cycles after the last edge, before the receiver's next 1,758-cycle tick | Test | Two "missing frame" cases that passed when scaled down |
| 10 | NEC transmit checker wanted burst lengths to 0.05%, but a burst measured from first rise to last fall is up to a carrier period short | Test | Checker failing on a correct waveform |
| 11 | DCC transmit started with the line already high, so the first half bit had no edge | Firmware | Checker's preamble count |
| 12 | DCC checker counted the tail of a packet cut off at capture start into the next preamble (23) | Test | RTL run |
| 13 | ARINC transmit left 3.5 bit times between words, not 4 | Firmware | Checker's gap check |
| 14 | ARINC transmit branched on the CRC's Z instead of `TSTB`'s (gap 10) | Firmware | Checker decoding the wrong bits |
| 15 | ARINC transmit's first pulse was cut short by a tick left pending during `PULLW` (gap 11) | Firmware | RTL test; the model harness had preloaded the FIFO, so it never showed there |
| 16 | The bench drove the ARINC receiver's lines high (both HI) until the first edge, so the receiver never saw the NULL gap and lost the first word | Test | RTL test: first word missing in every run |
| 17 | Labels, and then `.equ` names, clashed when two programs were assembled into one image (gap 12) | Firmware | Labels: an assembler error with a misleading message. `.equ`: nothing, until the ARINC loopback received no words |
| 18 | ARINC low-speed test started the first word 2000 cycles after the receiver, which wants 8000 of NULL first, and a DCC loopback test expected 3 packets in a window that holds 2 | Test | RTL runs |
| 19 | Long waits written as `ClockCycles` woke Python every clock and made millisecond tests crawl; a `\|` in `COCOTB_TEST_FILTER` breaks the Makefile's shell line | Test infrastructure | Runs that didn't finish |

## Not done

- No board, no gate-level run. No gds (budget 0).
- The NEC sweeps run at 1/5 of real time constants (`NEC_FAST`); the full-rate tests cover one frame and one
  repeat each way. The firmware is the same file; only `.equ` values differ.
- BiSS control communication (register access over many CDM/CDS bits) is host work on top of the per-frame
  CDM/CDS this firmware moves; it isn't implemented. Multi-slave BiSS chains aren't tried.
- DCC: S-9.2's packet rules were taken from memory, not re-read. Decoder address filtering, RailCom cutouts
  and service mode aren't attempted.
- ARINC 429: the HI/LO logic interface stands for a driver/receiver chip; slew-rate and voltage-level rules
  are that chip's, not SPLICE's.
