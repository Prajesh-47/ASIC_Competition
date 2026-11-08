# Adaptability trial

The competition's main question is whether the chip can be reprogrammed for protocols it wasn't designed for.
This trial tests that directly, with protocols chosen by someone other than the designer.

*Run 2026-11-06 to 2026-11-07, in RTL simulation. No board or silicon.*

## Rules

Fixed before any results came in:

1. **Someone else picks.** The user asked a different AI model for 5 protocols, with a prompt that left out the
   chip's design, and the list was used as given, unedited. I added 4 of my own, marked below as weaker evidence.
2. **No changes to the chip.** No RTL changes and no ISA changes. Only firmware and tests.
3. **Everything is reported,** including failures and the bugs I made along the way.
4. **Expected results come from outside the firmware.** Device models and decoders are written from each
   protocol's rules. Test vectors are checked against published sources where possible, and marked where they
   couldn't be.

Firmware is in [`model/firmware/trial/`](../model/firmware/trial/), and tests are in
[`test/test_trial.py`](../test/test_trial.py). To run them: `cd test && make COCOTB_TEST_MODULES=test_trial`.
They take about 30 minutes, since DALI and CEC frames last milliseconds.

## Results

All 35 tests pass on the final firmware. A passing test doesn't always mean the protocol works at its required
rate, so the table shows that separately.

| Protocol | Chosen by | Works? | At the required rate? | Program size | Tests |
|---|---|---|---|---:|---:|
| HDLC, RFC 1662 framing, full duplex | Outside | Yes | Yes, 1 Mbit/s each way, with the host at the fastest SPI allowed | 159 words | 7 |
| DALI controller and gear | Outside | Yes | Yes, 1200 bit/s, and ±10% | 108 + 90 | 6 |
| S/PDIF transmit and receive, 48 kHz | Outside | Yes | Yes. Transmit 47.97 kHz (0.065% off), receive tolerates −2% to +1% | 98 + 143 | 6 |
| SD host, native 4-bit mode | Outside | Yes | **No.** Peaks at 6.25 MHz read and 8.33 MHz write against 25 MHz. CRC-16 is done by the host | 223 | 5 |
| 100BASE-TX | Outside, as the expected limit | Logic only | **No, and it can't.** The chain works up to at least 1.25 MBd against 125. A pin moves at most every 20 ns, so 50 MBd is a hard ceiling | 159 + 122 | 4 |
| MDIO, clause 22 | Me | Yes | Yes, 2.5 MHz | 61 | 1 |
| LIN responder, with auto-baud | Me | Yes | Yes, 19200 baud, and ±10% | 155 | 2 |
| SENT transmitter | Me | Yes | Yes, 3 µs ticks | 107 | 1 |
| HDMI-CEC initiator and follower | Me | Yes | Yes | 80 + 71 | 3 |

Program memory holds 256 words. Every program fits on its own, but the largest, SD, uses 223.

### What was checked

- **HDLC:** the plan's vector (FCS of `123456789` is 0x906E, sent as 6E 90), at least 20 ones in a row, flag
  patterns inside the payload, a wrong FCS, a frame under 4 octets, a frame that isn't whole bytes, an abort,
  shared flags, and 64-byte frames both ways at once.
- **DALI:** exact Manchester waveform and 38 Te frame length, YES and NO replies, the shortest and latest
  allowed reply times, the bit-rate limits, frames with too few or too many bits, collision detection with an
  intact retry, and gear addressing by short address, group and broadcast.
- **S/PDIF:** a whole 192-frame block read back by a separate edge-only decoder, the plan's preamble codes,
  inverted polarity, a parity error, a damaged preamble, and clock error from −3% to +3%.
- **SD:** the plan's vector (CMD0 is `40 00 00 00 00 95` on the wire), CMD0, CMD8, CMD55, ACMD41, CMD2 and
  CMD3, with R1, R2, R3, R6 and R7, then full 512-byte reads and writes on 4 lines. Also a bad response CRC, a
  missing response with recovery, a flipped data bit, and DAT2 stuck low in each direction.
- **100BASE-TX:** 4B/5B, J/K and T/R, idle, the scrambler over a long run, MLT-3 steps, invalid code groups, the
  fastest working rate, and a real 125 MBd stream to show how much a 50 MHz sampler misses: at least 60%.

### Sources

- **Checked against a source:**
  - HDLC flag, stuffing, FCS and length rules, from RFC 1662.
  - DALI timing, from Microchip AN1465.
  - S/PDIF preambles and slot layout, from the AES3 article.
  - 4B/5B codes.
  - CEC bit timing, MDIO frame fields, the LIN sync byte, and SENT's 56-tick sync and 12 + n nibble times.
- **Calculated independently:** the plan's CMD0 CRC-7 and HDLC FCS vectors both match.
- **Not confirmed by any source read here, and marked in the firmware:**
  - The 100BASE-TX scrambler polynomial.
  - SENT's CRC-4 and its seed.
  - LIN's parity and checksum formulas.
  - CEC's ACK length.

  Wikipedia also gives LIN's minimum break as 11 bits, not 13. The firmware accepts 11.

## What the trial found

These limit how well the chip adapts. They matter more than any single result above.

| Gap | Where it showed | Effect |
|---|---|---|
| **The host link is narrow** | HDLC, S/PDIF, SD, Ethernet | The FIFO holds 4 words, and reading one word over SPI takes about 4 µs at the fastest rate allowed. Sustained traffic needs a host on a precise timer at the fastest SPI. Full-duplex HDLC failed at core/10 and only passed at core/8. The bit-banged board agent would be far too slow |
| **4 registers per context** | SD | Four per-line CRC-16s plus a counter don't fit, so the host does them |
| **No register-to-register ADD** | LIN | The checksum adds a byte by counting up to it, about 1,000 cycles a byte. Fine at LIN rates, but not much faster |
| **TSET takes only a constant** | LIN, DALI | A measured baud rate has to be applied with TPHASE before every bit. DALI's 20,833-cycle half bit is over the timer's 8,191, so it counts 3 ticks |
| **A tick nobody consumes sets F** | SD, DALI | F also signals a full FIFO and a timeout, so a running timer corrupts both. Firmware has to stop the timer first |
| **PROBE sets Z when the line is high** | S/PDIF, CEC, earlier firmware | It caused 3 of my bugs. Z usually means zero, and here it means one |
| **No table lookup or indirect jump** | 100BASE-TX, SD | 4B/5B is a tree of tests, and decoding is a chain of compares. Slow, and big in program memory |
| **Contexts can't pass data** | HDLC, S/PDIF | Two contexts share the FIFOs by direction only, so one can't feed the other, for example to translate between protocols |
| **One instruction per line change** | SD, 100BASE-TX | Four data lines take four instructions, which is what limits SD's clock |

## Bugs I made, and what caught them

All were in firmware or tests. None needed an RTL change.

- **Wrong branch on PROBE**, three times: S/PDIF receive, CEC follower, and once more during S/PDIF tuning.
  Caught by device-model tests.
- **Off-by-cycle loops:** LIN's baud counter ran at 9 cycles a count, not 8, and S/PDIF sampled preamble states
  too early, then too late. Caught by clock-error sweeps.
- **Protocol timing:** the SD read missed the card's start bit by clocking idle cycles first, and the
  100BASE-TX code-group path stacked up ticks. Caught by the card model and an MLT-3 checker.
- **Test models:** the MDIO turnaround came a clock late, the SD card ignored its own stuck line, and the hosts
  for S/PDIF and Ethernet were too slow. Each was found by tracing, and fixed before trusting the result.

## Since the trial

The tables above record the trial as it ran, and the trial firmware is kept unchanged. Fixes since then are
measured against it.

| Change | Gaps it closes | Measured |
|---|---|---|
| **`ADDR d, s`**: add a register operand. **`TSETR s`**: timer period from a register, with the timer widened from 13 to 16 bits (branch `exp/isa-addr-tsetr`) | Register-to-register ADD; TSET takes only a constant; the 8,191-cycle limit | **LIN** (`model/firmware/lin.s`): the checksum takes 8 instructions a byte instead of about 1,000 cycles, and each byte sets the bit time once instead of before every bit. On the golden model, with the node set up for each rate, it receives worst-case frames down to 56 cycles a bit (about 890 kbaud), against 1,666 (30 kbaud) for the trial version. 145 words, against 155. On RTL, both versions pass the trial's LIN tests, and the new one also passes at 480 kbaud. **Timer:** a 20,833-cycle period, DALI's half bit, ticks exactly on RTL. **Cost** (gds run 42494287966 against main's 42483107251): standard-cell area +2.1% (147,386 to 150,410 µm²). Setup slack at the slow corner +2.41 ns, from +3.16. Slew violations 63, from 30; capacitance 23, from 15; fanout 84, from 75 |
| **DALI on the 16-bit timer** (`model/firmware/dali_controller.s`, `dali_gear.s`) | 8,191-cycle timer limit | One tick per half bit instead of 3 ticks of Te/3. Every edge of a forward frame now lands exactly on a whole Te on RTL, where 3 × 6,944 drifted a cycle per half bit. The controller's collision check, a third into each released half, became a delay. 107 and 80 words, against 108 and 90. All six DALI trial tests pass for both versions |

## Next

These gaps point to ISA and host-link changes. The plan:
1. Rank them by how many protocols each one helps.
2. Get approval for the top few.
3. Make the changes.
4. Rerun this trial to measure the difference.

A second trial, with protocols picked fresh by someone else, would test whether the changes generalise rather
than only fixing the cases seen here.
