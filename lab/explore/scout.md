# Scout: how other programmable-I/O designs get their flexibility, and what SPLICE lacks

*2026-11-09, branch `explore/scout-edge-timing`. Exploration only: nothing here is approved, and no RTL was changed.*

This is a survey of programmable I/O and protocol engines, a ranked list of ideas SPLICE doesn't have, and a
model-level prototype of the top one. Every claim taken from the web has a link. Web content was treated as
data. No other entrant in this competition was studied.

## Contents

1. [Survey](#1-survey)
2. [Ranked ideas](#2-ranked-ideas)
3. [Prototype: edge-interval capture (`TCAP`) and an unsigned compare (`CMPU`)](#3-prototype-tcap-and-cmpu)
4. [What this doesn't show](#4-what-this-doesnt-show)

A separate list of untried protocols for a future blind trial is in
[`protocol-candidates.md`](protocol-candidates.md).

---

## 1. Survey

For each design: the mechanism behind its flexibility, and whether SPLICE has something equivalent.

| Design | What gives it flexibility | SPLICE equivalent? |
|---|---|---|
| **RP2040 / RP2350 PIO** | Small state machines with a 9-instruction ISA. Each instruction can carry side-set and a delay ([MicroPython rp2 docs](https://docs.micropython.org/en/latest/library/rp2.html)). Jump conditions include `X--`, `X!=Y` and `PIN` ([Pico SDK PIO instructions](https://picodocs.pinout.xyz/group__pio__instructions.html)). Autopush and autopull frame FIFO words with no instruction ([MicroPython rp2 docs](https://docs.micropython.org/en/latest/library/rp2.html)). IRQ flags 4–7 are internal, so state machines can wait on each other ([MicroPython rp2 docs](https://docs.micropython.org/en/latest/library/rp2.html)). RP2350 adds a version field, IRQs that cross to the next or previous PIO block, more FIFO join modes, and `IN_COUNT` masking ([MicroPython PR notes](https://notes.alelec.net/assets/mpy-triage/pair/536.html)), with 12 state machines in place of 8 ([Jeff Geerling](https://www.jeffgeerling.com/blog/2024/raspberry-pi-pico-2-rp2350-adds-more-pio-risc-v-cores)) | **Mostly yes.** Side-set, delay fields, fused bit loops, `DJNZ`, `TSRC` edge ticks. **No:** autopush/autopull, waiting on another context, and multi-pin `OUT`/`IN`. **SPLICE has and PIO lacks:** the pass-through fabric with `MODE`, the open-drain bridge, and a capture unit. The PIO jump conditions have no timer capture, so a pulse width is measured with a counting loop |
| **XMOS xCORE ports** | Every port has a 16-bit counter. Its value is recorded as a timestamp when data moves between the pins and the transfer register, and software can read it ([XMOS, How to perform timed output](https://www.xmos.com/documentation/XM-002509-PC/html/index.html)). **Timed output** holds data until the port counter reaches a set time ([AN03002](https://www.xmos.com/documentation/XM-015260-AN/html/doc/rst/an03002.html)). **Conditional input** waits until the pins equal or differ from a value, and a timestamp is set when the condition is met ([XMOS, How to wait for a condition](https://xmos.com/documentation/XM-002521-PC/html)). Buffered and serialising ports decouple sampling from computing ([AN03002](https://www.xmos.com/documentation/XM-015260-AN/html/doc/rst/an03002.html), [AN03003](https://www.xmos.com/documentation/XM-015262-AN/html/doc/rst/an03003.html)) | **Conditional input:** yes (`WAIT`). **Timestamps: no.** SPLICE knows *that* an edge happened (an edge tick) but not *when*, unless firmware was busy-waiting for it. **Timed output:** only by counting instructions |
| **TI PRU-ICSS** | Deterministic RISC cores with direct pin registers. Enhanced GPIO has direct, 16-bit parallel-capture and 28-bit shift-in input modes, and a shift-out mode ([TI PRU-ICSSM IO modes](https://software-dl.ti.com/processor-industrial-sw/esd/motor_control_sdk/am263px/2025_00_00_06/docs/api_guide_am263px/DEVELOPER_GUIDE_PRUICSSM_IO_MODES.html)). One eCAP capture module per subsystem, and an IEP timer with capture and compare ([TI AM64x MCU+ SDK](https://software-dl.ti.com/mcu-plus-sdk/esd/AM64X/latest/exports/docs/api_guide_am64x/COMPONENT_PRU_GUIDE_INTRO.html), [TI ICSSG IO modes](https://software-dl.ti.com/processor-industrial-sw/esd/motor_control_sdk/am243x/2025_00_00_06/docs/api_guide_am243x/DEVELOPER_GUIDE_PRUICSSG_IO_MODES.html)) | **Deterministic timing:** yes. **Hardware shift-in:** in part (`INB`). **Capture timer (eCAP): no** |
| **Infineon PSoC Smart I/O and UDB** | Smart I/O is a small LUT fabric between a GPIO port and the peripherals, so pin-level logic needs no CPU ([PSoC 6 PDL Smart I/O](https://infineon.github.io/psoc6pdl/pdl_api_reference_manual/html/group__group__smartio.html), [EE Times Asia](https://www.eetasia.com/building-pin-level-digital-logic-with-smart-i-os/)). A UDB has two small PLDs plus an 8-bit datapath with compare and condition outputs ([Infineon KBA85325](https://community.infineon.com:443/t5/Knowledge-Base-Articles/Comparison-of-Resource-Utilization-Between-PSoC-3-PSoC-5-and-PSoC-5LP-UDBs-and/ta-p/248280)) | **In part.** Each line has a fixed source menu (PASS, INV, F0, F1, ENG, ODB). There's **no programmable logic** combining two signals, for example the engine's bit XOR the partner line |
| **NXP FlexIO** | Shifters and timers that trigger each other. A timer can be triggered by a shifter's status flag, by another timer, or by a pin, and can run as a dual 8-bit baud/bit counter ([AN5397](https://nxp.jp/docs/en/application-note/AN5397.pdf), [AN13358](https://cn.nxp.com/docs/en/application-note/AN13358.pdf), [AN14794](https://www.nxp.jp/docs/en/application-note/AN14794.pdf)). The shifter has **match store** and **match continuous** modes ([MCUXpresso fsl_flexio.h](https://codex.ac6.fr/Zephyr/xref/hal_nxp-2.7.6/mcux/drivers/imx/fsl_flexio.h)). In match continuous, data is shifted in and compared with the shifter buffer on each shift ([NXP FlexIO training](https://www.nxp.com/docs/en/supporting-information/FTF-ACC-F1179_Introduction_to_FlexIO.pdf), [S32K144 register docs](https://docs.rs/s32k144/0.10.0/src/s32k144/flexio/shiftctl2.rs.html)) | **Chaining:** no. One timer per context, and it can't be triggered by a pin or by another timer. **Match:** the `ARM`/`MATCH` encodings are reserved but not built |
| **Microchip CLB (PIC16F13145)** | 32 basic logic elements, each a 4-input LUT and a flip-flop, which run without the CPU ([Microchip datasheet excerpt](https://docs.rs-online.com/65bc/A700000013963398.pdf), [Microchip product page](https://www.microchip.com/en-us/products/microcontrollers/8-bit-mcus/pic-mcus/pic16f13145), [All About Circuits](https://www.allaboutcircuits.com/industry-articles/efficient-complex-discrete-logic-implementation-with-microcontroller-clbs/)) | **No.** As for Smart I/O |
| **Glasgow** | An FPGA (iCE40HX8K in the 2020 hardware) is reconfigured per "applet". Gateware is written in Amaranth, and the host side is Python talking to FIFOs ([CNX Software](https://www.cnx-software.com/2020/12/23/glasgow-interface-explorer-ice40-fpga-hardware-debugging-tool/?amp=1), [FOSDEM 2021 slides](https://box.attie.co.uk/FOSDEM21_Glasgow_Attie.pdf)) | **Different trade-off.** Glasgow gets total flexibility by changing hardware. SPLICE has fixed hardware and changes firmware, so it reloads in microseconds and its timing is exact and published |
| **Bus Pirate 5/6** | An RP2040 or RP2350 with PIO behind level-shifted I/O. 1-Wire, I2C, half-duplex UART, 2-wire, LED and infrared modes use PIO ([Bus Pirate forum](https://forum.buspirate.com/t/pio-based-modes-not-working-on-bp6-5xl/569), [Bus Pirate docs](https://docs.buspirate.com/docs/)). An I2C sniffer uses all four state machines of one PIO, up to 500 kHz ([Hackster](https://www.hackster.io/news/ian-lesnet-brings-i2c-sniffing-to-the-bus-pirate-5-at-up-to-500khz-9f7da7ea05cc)) | **Same idea as SPLICE's emulate and observe modes.** No interposing |
| **can2040 (CAN on RP2040 PIO)** | A "sync" state machine aligns sampling to incoming edges and raises a sample IRQ at each sampling point. A **"match" state machine** holds a 32-bit key from the CPU, which is the expected raw bits and a bit position, and fires when the last 21 raw bits and an 11-bit position counter agree. The tx state machine then injects the ACK bit. Unstuffing is done by the CPU ([can2040 Code Overview](https://github.com/KevinOConnor/can2040/blob/master/docs/Code_Overview.md)) | **Edge resync:** yes (`TPHASE`). **Hardware sequence match driving an output: no.** SPLICE's CAN receive does this in firmware |
| **Pico-PIO-USB** | USB low and full speed on PIO. The transmitter is one state machine with 22 instructions, and the receiver is two state machines with 31 ([Pico-PIO-USB README](https://github.com/sekigon-gonnoc/Pico-PIO-USB)). One forum reading of the code says the receiver resynchronises on edges, while NRZI is removed in software ([EEVblog](https://www.eevblog.com/forum/projects/usb-sniffer-using-rp2040/msg4890290/)) | **Yes, for low speed** (`usb_ls_rx.s`). Full speed (12 Mbit/s, about 4 cycles a bit) is out of reach at 50 MHz |
| **V-USB (USB on AVR)** | Cycle-counted assembly that samples near bit centres from the sync pattern. The 16.5 MHz build adds a software PLL that copes with ±1% clock error ([V-USB source mirror](https://windfis.ch/git/gcn_usb/tree/usbdrv/usbdrvasm165.inc)) | **Yes,** and SPLICE's exact timing makes it easier |
| **ChipWhisperer triggers** | Triggers on a UART byte pattern, or a waveform match by sum of absolute differences, used to time fault injection. Husky can chain a GPIO change, a UART string within a time window, and a waveform match ([NewAE forum](https://forum.newae.com/t/uart-trigger-not-working-on-chipwhisperer-husky/5742), [NewAE forum](https://forum.newae.com/t/finiding-specific-encryption-alghoritm-by-triggering-how/237)) | **In part.** SPLICE matches in firmware and has `TRIG_OUT`. No hardware pattern trigger |
| **Bus man-in-the-middle tools** | TPM Genie interposes on I2C to a TPM and modifies traffic ([TPM Genie](https://awesome.ecosyste.ms/projects/github.com%2Fnccgroup%2Ftpmgenie)). Synacktiv concluded that a microcontroller is poorly suited to patching SPI MISO on the fly, and pointed to an FPGA ([Synacktiv](https://synacktiv.com/publications/car-hijacking-swapping-a-single-bit)). CAN bridges modify and forward frames ([Hackster on the CAN Bridge](https://www.hackster.io/news/emile-nijssen-s-open-source-can-bridge-makes-automotive-man-in-the-middle-a-cinch-b4dfeb952b04)). Comma.ai split a FlexRay bus through an FPGA ([comma.ai](https://blog.comma.ai/hacking-an-audi-performing-a-man-in-the-middle-attack-on-flexray/)). Facedancer's proxy mode does USB man-in-the-middle ([Facedancer](https://github.com/usb-tools/facedancer)) | **This is SPLICE's differentiator.** Each of these is one bus on store-and-forward hardware, or an FPGA. SPLICE does cut-through bit-level changes from a firmware-programmable fabric |
| **Academic and patents** | Linköping's configurable port processor is a software-reconfigurable protocol processor: a deep-pipeline serial processor plus a microcontroller ([Coolchips 2000](https://www.da.isy.liu.se/pubs/tomhe/Coolchips2000.pdf)). Patents describe programmable I/O subsystems running downloaded code for several protocols ([US 7,975,094](https://patents.google.com/patent/US7975094)), and shared hardware plus reconfigurable elements for unanticipated protocols ([US 7,464,192](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/7464192)) | Same goal. SPLICE's distinctive parts are exact per-instruction timing and the interposing fabric |

**What runs through the survey.** The mechanisms SPLICE lacks fall into four groups:
1. **Knowing when something happened:** XMOS timestamps, PRU eCAP and IEP capture.
2. **Reacting without an instruction:** FlexIO match and trigger chaining, can2040's match state machine.
3. **Logic in the pin path:** CLB, Smart I/O, UDB PLDs.
4. **Framing conveniences:** PIO autopush and autopull, multi-pin `IN`/`OUT`.

Group 4 and inter-context events overlap other explorers' work (multi-line operations, mailboxes) and the lead's
host-FIFO work, so they're listed but not prototyped here.

---

## 2. Ranked ideas

Ranked by flexibility gained per unit of area, then by novelty. "Area" is synthesis-only. Only idea 1 was
synthesised (with `lab/isa/area/synth.sh`, slow-corner library). The others are estimates from the spec's area
probe (a 4×16 register file is 4,841 µm², so about 75 µm² a bit with its muxes). For scale: the current
standard-cell area is about 155,700 µm² on `exp/isa-crc` and 167,200 µm² on `exp/isa-crc-final`, out of
916,214 µm² of die.

| # | Idea | Source of the idea | Protocols it unlocks or improves (trial gaps) | Area | Novelty |
|---:|---|---|---|---|---|
| **1** | **Edge-interval capture `TCAP rd` and unsigned compare `CMPU a, b`.** Hardware latches the cycles between the last two ticks, so firmware reads an edge's timing late, without busy-waiting for it. **Prototyped, section 3** | XMOS port timestamps, PRU eCAP | **S/PDIF** at any rate (measured below). Pulse-width and edge-interval protocols at unknown or drifting rates: DALI, CEC follower, LIN auto-baud, SENT receive, IR remotes, DShot, Joybus, WS2812 receive. Trial gaps: the fixed-constant sampling behind 2 of the 4 bug classes ("off-by-cycle loops", caught by clock sweeps). Also **`CMPU` gives the ISA its first less-than**, so range checks no longer need chains of equality tests | **4.0K µm²** if it reuses the idle period counter, 5.9K with its own (2 contexts). 2.4–3.5% of standard cells | **Medium-high.** Not in PIO. XMOS and PRU have it, but not in an interposer, where it can time a *live* bus (glitch and timing-violation detection on pass-through) |
| **2** | **Match → MODE reflex.** Build the reserved `ARM`/`MATCH`: a per-context (value, mask, bit position) compare on the shift register that, on a hit, switches a line's fabric source on the same cycle, with no instruction | can2040's match state machine, FlexIO match continuous, ChipWhisperer pattern triggers | **CAN** ACK injection or forcing an error on bit k of a chosen ID, inline. HDLC flag hunting, USB SYNC, Ethernet SFD, S/PDIF preambles, Bluetooth-style access codes. Removes instruction latency from interposing with **two contexts at half rate**. Trial gap: contexts that can't react to each other (HDLC, S/PDIF) | About 6–8K for one rule per context (estimate). The spec's full version is 30K | **High.** It's interposer-specific, and the survey found it only as a standalone tool trigger |
| **3** | **Timed `MODE`:** schedule a fabric change at "tick + n cycles" or at a `TCAP`-relative time, then keep running | XMOS timed output | Interposing at bit-exact positions while the context does other work, e.g. "invert the bit 3.5 bit times after this edge". Two-context full-duplex rewrite (HDLC, UART both ways) at full timing resolution | About 2–3K (a 16-bit compare and a pending `MODE` per context) | **High** together with #1 and #2 |
| **4** | **A logic cell per line:** a LUT3 over (partner, engine bit, neighbour line), plus a toggle flip-flop | CLB, Smart I/O, UDB PLD | Complementary or differential pairs from one engine bit (MIL-STD-1553 and ARINC 429 style, RS-485 driver enable), XOR-mask interposing (flip many bits with one `MODE`), NRZI and biphase toggling in the fabric, merging two buses. Trial gap: one instruction per line change, for line pairs | About 5–7K for 8 lines (estimate) | **Medium.** Uncommon in an interposer |
| **5** | **A fabric clock source:** a line toggles on its context's timer tick, with no instruction | FlexIO timer output | I2S/TDM, continuous SD or QSPI clocks, and **38 kHz IR carrier** generation while the engine shapes the envelope. Trial gap: SD's clock instructions | About 1–2K | Low. May overlap the per-line serdes explorer |
| **6** | **A per-line input glitch filter or majority vote,** N cycles, so edge ticks and `TCAP` ignore spikes | Engineering judgment, not sourced | Real-board robustness: the I2C bridge's slow rising edges, edge-clocked SPI targets, long 1-Wire and DALI lines | About 2–3K | Low |
| 7 | **Autopush/autopull,** framing FIFO words without an instruction | PIO | Saves a `PUSH`/`PULLW` per word. Trial gap: the host link, but the lead's FIFO work matters more | About 1K | Low. Copies PIO |
| 8 | **Wait on another context's event** | PIO internal IRQs, RP2350 cross-block IRQs | Protocol translation between contexts. Trial gap: contexts can't pass data | Small | Low. **Covered by the mailbox explorer** |

**Why #1 comes first:** it's the cheapest idea that changes *what kind* of protocol can be handled, rather than
how fast. It also makes #2 and #3 more useful. #2 is the most unique to SPLICE, but it costs more, and it needs
SPI and I2C proof before it's worth building (spec section 5.6 says the same).

---

## 3. Prototype: `TCAP` and `CMPU`

### What was added (model, assembler, firmware)

| Instruction | Encoding | Meaning |
|---|---|---|
| `TCAP rd` / `TCAP.t rd` | `11101011` r2 t1, in free space | `rd` = the number of cycles between the context's last two ticks, saturating at 0xFFFF. The hardware latches it at each tick. With `.t`, first wait for a tick, as for any instruction |
| `CMPU a, b` | `11101010` a3 b3, in free space | Unsigned: `Z` = (a == b), `F` = (a < b). Operands as for `CMP` |

No existing opcode moved. In the model, each context has a `since` counter and a `tint` latch
(`model/splice/model.py`, `timer()`). Ticks are those `TSRC` or the period timer already make, so with
`tsrc RX, both` every edge latches its interval. A tick that arrives while one is still pending sets `F`, as
before, so a program that falls behind is visible.

**Why this changes what firmware can do:** firmware no longer has to be waiting at an edge to know when it
came. It only has to read each interval before the *next* edge ends, so decoding overlaps the line's timing.
That's what makes a rate-agnostic receiver possible at about 8 cycles a half-bit.

### The demonstration: S/PDIF receive, one binary, any rate

`model/firmware/explore/spdif_rx_tcap.s` classifies every interval as short (1 state), long (2) or preamble
(3), against thresholds taken from the last preamble's 3-state interval: P/2, and P/2 + P/4 + P/16. They're
refreshed at every preamble, which is the clock recovery. At startup the longest of 96 intervals gives the
first P. The FIFO format, two words a subframe, and the ERR, PERR and SYNC pins match the trial version, and the
host still does the final XOR.

**Baseline:** `model/firmware/trial/spdif_rx.s`, unchanged, which samples at fixed delays tuned for 48 kHz.

**Method, the same for both** (`lab/explore/spdif_edge.py`): biphase-mark waveforms written from the coding
rules, as in `test/test_trial.py`, with timing rounded to the 20 ns grid. An ideal host empties the FIFO every
cycle. The pass rule: after lock, at least 30 consecutive subframes decode exactly, with ERR low and no FIFO
overflow. As a check on the harness, it reproduces the trial's RTL result for the baseline at 48 kHz, which was
−2% to +1%.

| | Trial firmware (current ISA) | `TCAP` + `CMPU` firmware |
|---|---|---|
| Program words | 143 | **116** |
| Sample rates decoded, nominal clock | **48 kHz only** (fails at 44.1 and 32) | **8 kHz to 60 kHz**, one binary (8, 16, 22.05, 30–60 kHz in 2 kHz steps, all pass) |
| Clock tolerance at 48 kHz | −2% to +1% | **−30% to +20%**, every step tried (wider wasn't tried) |
| Clock tolerance at 32 and 44.1 kHz | None (needs rebuilt constants) | ±10% at both, every step tried |
| Fastest working | 48 kHz: 8.14 cycles a state | 60 kHz: **6.5 cycles a state**. Fails at 62 kHz |
| Rate step mid-stream, 48 → 44.1 kHz | Never relocks: 0 of 50 subframes | **Relocks: 49 of 50** decode after the step |
| Inverted polarity | Pass | Pass |
| Parity error flagged (PERR) | Yes (trial test) | Yes: one subframe, about 520 cycles |
| Damaged preamble flagged (ERR) | Yes (trial test) | Yes |
| SYNC on block start | Yes | Yes |

Full grid (Y means pass), from `python3 lab/explore/spdif_edge.py`:

```
                 -10% -5% -3% -2% -1% +0% +1% +2% +3% +5% +10%
trial  32000 Hz   .    .   .   .   .   .   .   .   .   .   .
trial  44100 Hz   .    .   .   .   .   .   .   .   .   .   Y     (44.1k +10% is 48.5k)
trial  48000 Hz   .    .   .   Y   Y   Y   Y   .   .   .   .
tcap   32000 Hz   Y    Y   Y   Y   Y   Y   Y   Y   Y   Y   Y
tcap   44100 Hz   Y    Y   Y   Y   Y   Y   Y   Y   Y   Y   Y
tcap   48000 Hz   Y    Y   Y   Y   Y   Y   Y   Y   Y   Y   Y
tcap   64000 Hz   Y    .   .   .   .   .   .   .   .   .   .     (64k -10% is 57.6k)
```

**Verdict:** a clear flexibility win, measured. It goes from one rate at ±1–2% to any rate from 8 to 60 kHz,
with ±10–30% tolerance and relocking, in 27 fewer words, for about 4K µm² (2.4% of standard cells) if the period
counter is reused. The fastest rate rises 25%. It doesn't reach 96 kHz (4 cycles a state): the firmware spends
3–5 instructions an edge.

**Tests:** `model/tests/test_explore_tcap.py`. They check the encoding, unsigned compare, interval capture
exact to the cycle and read late, S/PDIF at 48, 44.1, 32 (+5%) and 48 (−10%) kHz, and a **negative control**:
the same harness must fail the trial firmware at 44.1 kHz and pass it at 48. `python -m pytest model/tests`:
19 passed.

**Area** (`lab/explore/tcap_sketch.v`, yosys with the slow-corner library, synthesis only):

| Variant | Cells | µm² | Flops |
|---|---:|---:|---:|
| Own counter per context (2 contexts) + latch + comparator | 307 | 5,913 | 64 |
| Reuses the period counter (idle when the tick is an edge) + latch + comparator | 237 | 3,981 | 32 |

Not included: decode, the extra input on the register write mux, and timing. The 16-bit compare sits in the
execute path, and the slow-corner slack on `exp/isa-crc-final` is 2.38 ns, so `CMPU` needs checking in RTL.

---

## 4. What this doesn't show

- **S/PDIF was already in the trial, and I designed `TCAP` while looking at it.** This measures an improvement
  on a known protocol. It isn't evidence of flexibility for unseen ones. The other protocols in idea 1's row
  are projections. A fresh blind trial, picked from `protocol-candidates.md` by someone independent, is the
  right test.
- **Model only.** No RTL, no differential test against RTL, no gate-level run. The model's interval latch is
  my spec. The RTL would need the same 2-flop synchroniser timing, which the model gets because ticks are
  already taken from synchronised lines.
- **An ideal host.** The FIFO is emptied every cycle in this harness. The real limit at 48 kHz is the host
  link (see `docs/adaptability.md`), which `TCAP` doesn't change. Both programs push 2 words a subframe.
- **The current ISA wasn't given its best shot.** A rate-agnostic receiver on the current ISA wasn't attempted.
  At about 8 cycles a state, a software counting loop (3 or more cycles a count) can't resolve the intervals,
  but a cleverer baseline might narrow the gap at 32 kHz.
- **Ideal electricals:** no jitter, noise or slow edges beyond the 20 ns grid.
- **Ideas 2–8 are untested**, and their areas are estimates.
