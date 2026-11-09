# ISA exploration: lanes, table reads, and a mailbox between contexts

*Branch `explore/isa-lanes-table`, from main at a158ff5. 2026-11-09. Prototype only: nothing here is approved.*

Three changes, aimed at three of the trial's gaps (docs/adaptability.md, "What the trial found"):

| Idea | Gap it targets | Inspiration |
|---|---|---|
| **A. LANES + CRCW**: OUT/IN/OUTB/INB move 1, 2, 4 or 8 lines at once, and one instruction steps one lane's CRC over a whole word | "One instruction per line change" (SD, 100BASE-TX) | RP2040 PIO's `in pins, n` / `out pins, n` (pin count per shift). CRCW is my own |
| **B. LDP + JMPR**: read a program word as data from the address in a register, and jump to a register | "No table lookup or indirect jump" (100BASE-TX, SD) | Ordinary `LPM`-style program-memory loads (AVR) and computed goto |
| **C. ROUTE**: a context's PUSH and/or PULL go through a one-word mailbox to the other context instead of the host | "Contexts can't pass data" (HDLC, S/PDIF, translation) | My own; the point is that existing firmware composes unchanged |

All three are in the golden model, the assembler, the opcode checker **and RTL** (`src/splice_engine.v`), and the
RTL matches the model on the existing random differential tests (which now also draw the new instructions).
I kept everything opt-in: with LANES 1 and ROUTE off, every existing program behaves as before.

## What changed

| File | Change |
|---|---|
| `model/splice/isa.py`, `lab/isa/isa_v1.py` | `LANES w` (`11101011`), `ROUTE m` (`11101100`), `LDP d, s` (`11101101`), `JMPR s` (`11101010`); `CRC` gains `word` and `lane` fields in its spare bits, so old CRC encodings are unchanged. Prefix code checks; free encoding space 8.7% to 7.1% |
| `model/splice/model.py` | Lane groups for OUT/IN/OUTB/INB (lane-aligned base, line base+k is bit k, SR shifts by W, BC counts groups); `lastb` is the whole group; `crc r, dir, lane` steps one lane's bit; `crcw r, dir, lane` steps one lane's 4 bits of SR (2 bits at 8 lanes) in one cycle; LDP takes 2 slots, its word landing in the second; mailbox |
| `model/splice/asm.py` | The above, plus `.word` for tables |
| `src/splice_engine.v` | Same, in RTL. LDP reads in place of a fetch: with one context in its own cycle, with two in the other context's slot |
| `test/test.py` | Random programs draw the new opcodes (LDP and JMPR get an in-program address first, and program memory is cleared to match the model's zeros); state check covers lanes, route, LDP and the mailbox |
| `test/test_trial.py` | `test_sd_lanes_read_write_and_crc`, `test_translate_uart_to_spi_through_mailbox` |
| `model/tests/test_asm.py` | 3 unit tests: CRCW against 4 serial CRCs, LDP/JMPR, ROUTE |
| `lab/explore/` | Model-level trial harness (imports the trial's device models and decoders unchanged from `test/test_trial.py`), SD/Ethernet/translation drivers, and the rewritten firmware in `fw/` |

**Why a model harness:** RTL on main doesn't have the new instructions, so baseline and candidate run through the
same Python harness on the golden model, against the trial's own `SdCard`, MLT-3 decoder, descrambler and
4B/5B checks. The harness reproduces the RTL trial's numbers for the baseline (below), so it is measuring the
same thing. Its host is ideal (moves a FIFO word the cycle it can), which isolates the engine; the real SPI
host is the lead's area and is reported separately where it matters.

## Scorecard

### A. LANES + CRCW, on SD 4-bit (`fw/sd_host_lanes.s`)

The data loops become `nop side=0 / inb 0, loop side=1` (read) and `nop side=1 / outb 0, loop side=0` (write):
one nibble every 2 cycles, which is SD's 25 MHz. After each word, 4 `crcw` instructions update the four
CRC-16s. Everything else in the program is the main firmware unchanged.

| SD, same harness | trial `trial/sd_host.s` | main `sd_host.s` (CRC in firmware) | **lanes `fw/sd_host_lanes.s`** |
|---|---:|---:|---:|
| Trial checks passed (init, read, write, corruption, DAT fault) | 5/5 | 5/5 | **5/5** |
| Program words (of 256) | 223 | 245 | **246** |
| Read clock, fastest | 6.25 MHz | 4.17 MHz | **25.0 MHz** (meets spec) |
| Read data phase, cycles per 16-bit word | 51.6 | 65.0 | **21.0** (3.1x main) |
| Read average clock over the block | 3.9 MHz | 3.1 MHz | **9.5 MHz** |
| Write clock, fastest | 8.33 MHz | 8.33 MHz | **25.0 MHz** |
| Write data phase, cycles per word | 33.6 | 26.2 | **10.2** (2.6x main) |
| CRC-16 checked on chip | no (host) | yes, names the line | **yes, names the line** |
| Whole CMD17 read (cmd + block), ideal host | 15,912 | 21,385 | **9,919** |
| Whole CMD17 read, RTL with the trial's SPI host | 95,273 | 95,273 | **95,272** |

The last row is the honest catch: on RTL with the real SPI host, the whole block takes the same time for all
three, because the host link (~366 cycles a word) sets the pace. Lanes only shows up end to end once the host
link is fixed. It does already deliver the spec clock rate (25 MHz bursts; SD lets the host pause the clock
between words) and frees the engine: 21 of every 366 cycles instead of 65.

RTL confirmation: `test_sd_lanes_read_write_and_crc` passes (read and write at 25.00 MHz, flipped bit named as
0xC101).

Commands:
```sh
cd lab/explore
python3 sd.py trial/sd_host.s sd_host.s fw/sd_host_lanes.s          # the scorecard rows, about 1.5 min
python3 sd.py --phase trial/sd_host.s sd_host.s fw/sd_host_lanes.s  # cycles per word in the data phases
cd ../../test && make COCOTB_TEST_MODULES=test_trial COCOTB_TEST_FILTER=test_sd_lanes_read_write_and_crc
```
(Data phase: the span of SD clock edges under 40 cycles apart, over 260 words. Average clock = 4 / that × 50 MHz.)

Other protocols this opens, not measured here: QSPI flash (quad I/O at 25 MHz), eMMC 8-bit (`lanes 8`, CRCW
does 2 steps per lane), 8080/6800 parallel LCD and camera buses, MII nibbles. RMII at 100 Mbit/s (2 bits per
cycle at a 50 MHz reference clock) is within the pin rate, but would need zero-overhead word reloads and a host
link far faster than today's, so I don't claim it.

### B. LDP + JMPR, on 100BASE-TX's 4B/5B (`fw/eth_tx_ldp.s`, `fw/eth_rx_ldp.s`)

Each file is the trial firmware with only the decode replaced: the 4B/5B tree (transmit) by a 16-word table, and
the 17-compare chain (receive) by a 32-word table. So the difference is LDP alone.

| 100BASE-TX, same harness | trial firmware | **with LDP** |
|---|---:|---:|
| Transmit: program words | 159 | **107** (-33%) |
| Transmit: fastest passing symbol time | 36 cycles (1.39 MBd) | **33 cycles (1.52 MBd)** |
| Receive: program words | 122 | **95** (-22%) |
| Receive: fastest passing bit time | 32 cycles (1.56 MBd) | **14 cycles (3.57 MBd)** |
| Invalid group and RX_ERROR, J/K, T/R checks | pass | pass |

The trial table quotes 1.25 MBd for transmit: that is the coarse sweep (60, 40, 30, ...); the fine sweep here
gives 36. Transmit barely speeds up because its limit is the scrambler and MLT-3 work per symbol, not the
nibble decode. 125 MBd is still impossible on the pins (a pin moves at most every 20 ns); LDP is about code size
and O(1) decode for any table-shaped code (4B/5B, 5B/6B and 3B/4B of 8b/10b, 4-bit CRC tables, JTAG TAP
state tables). JMPR wasn't needed by these programs; it is there for dispatch tables and costs little.

Commands:
```sh
cd lab/explore
python3 eth.py                                              # baseline (coarse sweep)
python3 eth.py --tx fw/eth_tx_ldp.s --rx fw/eth_rx_ldp.s
python3 -c "import eth; print([b for b in range(60,10,-1) if eth.tx_ok('trial/eth_tx.s',b)[0]][-1])"   # fine sweep
```

### C. ROUTE mailbox, on UART-to-SPI translation (`fw/uart_to_spi.s`)

`model/firmware/two_protocols.s` (UART receive in context 0, SPI controller in context 1) plus two lines:
`route push` in context 0 and `route pull` in context 1. Everything else is unchanged, which is the point:
existing receivers and transmitters chain without edits.

Baseline: the same two programs with no ROUTE, so the host must read each UART byte and write it back.

| UART in, SPI out (SPI half period 2 cycles, two contexts) | host relay, perfect host (gap 0) | host relay, 1 word/366 cycles each way | **ROUTE, no host** |
|---|---:|---:|---:|
| Fastest UART bit, 10 bytes back to back | 5 cycles (10 Mbaud) | 26 cycles (1.9 Mbaud) | **5 cycles (10 Mbaud)** |
| Fastest UART bit, 40 bytes back to back | | between 30 (fails) and 36 (passes) | **5 cycles** (still passes) |
| Last data sample to first SCK rise | 14 cycles | 13 cycles | **12 cycles** |
| With SPI half period 10 | 13 cycles | 27 cycles | 15 cycles |
| Program words | 19 | 19 | 21 |
| Host involvement | every byte, twice | every byte, twice | **none** |

366 cycles a word is the pace the RTL SD test's SPI host achieved (95,273 cycles for 260 words); it is a
stand-in for the real link, not a measurement of it. The 10-byte run flatters the relay because the 4-deep FIFOs
absorb the burst; at 40 bytes it falls to the host's pace. **Honest reading:** against a perfect host, ROUTE
ties (and loses 13 vs 15 at the slower SPI clock, because the mailbox is 1 word where the FIFOs are 4). Its win
is that no real host is that fast, and with ROUTE the host link stays free: the chip translates on its own.

RTL confirmation: `test_translate_uart_to_spi_through_mailbox` passes (6 bytes, UART at 8.33 Mbaud, SPI at
12.5 MHz, no host).

Command: `cd lab/explore && python3 translate.py` (about 10 minutes; it sweeps the UART bit time).

### Area (yosys, CMOS5L slow liberty, engine module only, `lab/isa/area/synth.sh`)

| Engine | Area | vs main | abc delay estimate |
|---|---:|---:|---:|
| main (a158ff5) | 72,356 µm² | | 6.13 ns |
| + LANES (and lane CRC select, 8-bit `lastb`) | 77,066 | +4,710 | 6.02 ns |
| + CRCW | 79,996 | +7,640 | 7.70 ns |
| + ROUTE | 82,157 | +9,801 | 7.18 ns |
| + LDP + JMPR (the branch) | 84,648 | **+12,292 (+17% of the engine)** | 7.27 ns |

Against the whole chip's 167,224 µm² of standard cells that is about +7%, at 27% utilization. The abc delay is
logic only and noisy to about ±0.5 ns; CRCW's four chained steps are the one real path addition. No gds run
yet (see below).

Commands: `LIBDIR=<filtered liberty dir> OUTDIR=<dir> bash lab/isa/area/synth.sh eng splice_engine <engine.v>`
on `git show main:src/splice_engine.v` and on this branch's file. The partial rows disable pieces with `sed`.

## Verdict

| Idea | Verdict | Why |
|---|---|---|
| **A. LANES + CRCW** | **Beats** | SD reaches its 25 MHz clock both ways with on-chip CRC, at the same program size; 3.1x read and 2.6x write engine throughput. Opens a class of parallel buses. +7.6K µm² |
| **B. LDP (+JMPR)** | **Beats on size, small on speed** | -33% / -22% words for 4B/5B, receive 2.3x faster, transmit 8% faster. +~2.5K µm². Strongest argument is program memory: SD is at 246 of 256 words |
| **C. ROUTE** | **Beats on capability, ties on speed against a perfect host** | Protocol translation with no host, composing existing firmware unchanged: 10 Mbaud sustained, where a 366-cycle host relay sustains under 1.7 Mbaud. +2.2K µm² |

**Recommendation:** adopt A first (largest gap closed, measured on RTL), then C (cheapest, unique capability:
receive one protocol and re-send it as another on chip, fits the interposer pitch), then B if program memory
stays tight. Each is independent; the RTL keeps them in separate, small blocks.

## Risks

- **25 MHz read timing on a real board.** The 2-cycle loop relies on the fixed input latency: each INB reads the
  nibble from the previous clock, so one clock primes the loop. That holds if the round trip (pad out, card's
  up to 14 ns output delay, pad in) is under one 20 ns cycle. If a board is slower, the offset grows by a clock
  and the firmware needs a second priming clock; a 3-cycle loop (16.7 MHz) is the safe fallback. Not tested on
  hardware.
- **Host link still caps end-to-end SD** (95,272 cycles a block, same as main). Lanes is wasted until that is fixed.
- **CRCW timing:** four chained CRC steps from instruction bits into the register file. Fine in logic-only
  estimates (7.7 of 20 ns), unknown against the real slow-corner path until a gds run.
- **Area +7% of the chip**, and encoding space down to 7.1% free.
- **LDP** reads only the 256-word macro and takes 2 slots (4 cycles with two contexts); tables cost program words.
- **ROUTE's mailbox is one word deep.** A producer that outruns its consumer gets F on PUSH, like a full FIFO, and
  must check it; the UART receiver here doesn't, so it relies on SPI being faster than UART.
- `lanes 1`/`2` with CRCW uses stride 4, which is only meaningful for 4 lanes: documented, not useful.
- RTL status: the whole `test` module passes (22/22, coverage 46/46 bins, new opcodes included). Only the trial
  tests I added (SD lanes, translation) were run from `test_trial`; DALI, HDLC,
  S/PDIF, LIN, CEC trial tests were not rerun (hours), though the differential tests cover lanes=1 equivalence.

## What to try next

1. **Autopush/autopull** (PIO-style) on top of LANES: when BC reaches 0, push or pull and reload BC with no
   instruction. Removes the per-word overhead (SD read would go from 21 to ~13 cycles a word) and is what a
   continuous stream like RMII would need.
2. **A hardware word counter** (DJNZ on TO, or a wider BC): 6 of SD's 13 per-word overhead cycles are a counter
   shuffled through TO because all 4 registers hold CRCs.
3. **Deeper mailbox** (2-4 words) if translation of bursty protocols matters.
4. A second blind trial with QSPI/eMMC/parallel-LCD style protocols, to test whether lanes generalise.
