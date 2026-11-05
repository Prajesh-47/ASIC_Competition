# SPLICE — Programmable Bus Interposer ASIC
**Architecture spec v0.1** · Jane Street Protocol Emulator ASIC Competition · Tiny Tapeout IHP CMOS5L, 6x4 tiles · Deadline Jan 18, 2027

> Working name: SPLICE. Rename freely.

---

## 1. Pitch (one paragraph for the judges)

Every protocol emulator can *drive* a bus. SPLICE also sits **inline** on a live bus between two chips and can **watch, emulate, or alter** traffic in real time — all from firmware on a cycle-exact timing engine. One ISA gives you a bit-banging peripheral, a timestamped bus capture, and a man-in-the-middle / fault-injection tool for UART, SPI and I2C. That's exactly the hardware-debugging and reverse-engineering workflow the competition brief describes, in one chip.

**Three modes, one engine:**

| Mode | What it does | Real use |
|---|---|---|
| **Emulate** | Engine drives/reads pins directly (baseline bit-banging) | Talk to any UART/SPI/I2C device |
| **Observe** | Transparent pass-through A↔B + timestamped edge capture | Sniff traffic between two chips without cutting traces |
| **Interpose** | Engine controls each line's pass-through mux cycle-exactly | Flip/drop/inject bits; rewrite bytes; fuzz a device's error handling |

---

## 2. Top-level block diagram

```
            ui_in[2:0]                    uo_out[0]
 host  ──► SPI slave ──► Host ctrl ◄──────► MISO
           (load prog, regs, FIFOs, capture readout)
                 │
                 ▼
        ┌────────────────────┐     ┌──────────────┐
        │  Timing engine     │◄───►│ Program SRAM │  256x16
        │  (16-bit ISA,      │     └──────────────┘
        │   cycle-exact)     │     ┌──────────────┐
        └──┬───────────┬─────┘     │ Capture SRAM │  512x16
           │           │           └──────▲───────┘
           │           │                  │
   ENG_OUT │     MODE  │            ┌─────┴──────┐
           ▼           ▼            │ Capture    │◄── synced line states
        ┌──────────────────────┐    │ unit (Δt)  │
        │ Line fabric ×4 pairs │────┴────────────┘
        │  per-line src mux    │
        │  + open-drain bridge │
        └──┬────────────────┬──┘
     uio[3:0] (A side)   uio[7:4] (B side)
```

---

## 3. Pin map (fixed by Tiny Tapeout: 8 in, 8 out, 8 bidir, clk, rst_n)

| Pin | Function |
|---|---|
| `ui[0]` | Host SPI SCK |
| `ui[1]` | Host SPI MOSI |
| `ui[2]` | Host SPI CS_n |
| `ui[3]` | TRIG_IN (external trigger → engine / capture) |
| `ui[7:4]` | AUX_IN[3:0] — engine-readable inputs (lines L8–L11) |
| `uo[0]` | Host SPI MISO |
| `uo[1]` | IRQ (engine/host event) |
| `uo[2]` | TRIG_OUT (fires on match / injection — scope trigger) |
| `uo[3]` | ACTIVE / heartbeat |
| `uo[7:4]` | AUX_OUT[3:0] — engine-drivable push-pull outputs (e.g. extra SPI CS in emulate mode) |
| `uio[3:0]` | Lines L0–L3 = **A side** of pairs 0–3 |
| `uio[7:4]` | Lines L4–L7 = **B side** of pairs 0–3 (partner of Lx is Lx^4) |

Protocol fits (interpose): UART = 2 pairs (TX, RX) · I2C = 2 pairs (SCL, SDA) · SPI = 4 pairs (SCK, MOSI, MISO, CS) — uses all.
Emulate mode: all 8 uio are plain GPIO + 4 AUX outs.

**Electrical limits (TT spec):** output ≤ 33 MHz, input ≤ 66 MHz, 4 mA drive, pads not 5 V tolerant, ~20 ns worst pad round-trip. I2C needs external pull-ups on **both** A and B sides.

---

## 4. Line fabric (the novel part)

Every line Lx (0–7) has a config register:

```
SRC[2:0] : ENG   – driven by engine ENG_OUT[x]
           PASS  – copy partner line (synced)
           INV   – inverted partner
           F0/F1 – force 0 / 1
           HIZ   – not driven (input only)
           ODB   – open-drain bridge with partner (I2C)
DRV      : 0 = push-pull, 1 = open-drain (drive low or hi-Z)
WATCH    : include in capture
```

The engine changes SRC with `MODE` instructions **on an exact cycle**. That one mechanism gives all interposition:

- **Cut-through fault injection:** line in PASS; engine waits for the start bit, delays to bit k, switches to INV for one bit time, back to PASS → single bit flip, zero added latency beyond the fabric.
- **Drop / glitch:** F0/F1 for N cycles.
- **Store-and-forward rewrite:** line in ENG; engine `IN`s a byte from A, modifies it (ALU / compare), `OUT`s it on B. Adds one frame of latency (fine for UART).

### 4.1 Open-drain bridge (ODB) — the hard problem
Bidirectional I2C pass-through: either side may pull low; SPLICE must mirror it to the other side without latching itself up (when we drive B low we also *read* B low).

Per-pair FSM: `IDLE → A_HOLDS` (A low first: drive B low) / `B_HOLDS` (mirror) → on originator release: stop driving, enter `HOLDOFF` for N cycles (≥ sync latency + pad delay) ignoring the driven side, → `IDLE`. Both low simultaneously → pick A, record arbitration flag.

**Must be formally proven:** no deadlock, never drives both sides from each other, release within bounded cycles. Commercial chips (I2C buffers/repeaters) exist only for this problem — solving it cleanly is a headline.

### 4.2 Pass-through latency (estimate — verify in gate-level sim)
2-FF synchronizer + output register = 3 cycles @ 50 MHz = 60 ns, + ~20 ns pad ≈ **~80 ns**.
→ comfortable for UART ≤ 1 Mbaud, I2C 100/400 kHz, SPI interposed ~2–4 MHz (MISO round-trip is the limit). An optional unsynchronized combinational path for observe-only mode is a stretch item.

---

## 5. Timing engine

- Single issue, **every instruction exactly 1 cycle + its delay field** (no stalls, no hazards) → timing is provable.
- Target clock **50 MHz** (TT clock ceiling ~66 MHz; confirm with STA at ss corner).
- State: `PC[8:0]`, `R0–R3` (16-bit), `SR` (16-bit shift reg) + `BC` (bit count), `TO` (wait-timeout reg), flags `Z`, `F` (timeout).
- 4-deep 16-bit FIFOs host→engine (`PULL`) and engine→host (`PUSH`).

### 5.1 ISA draft (16-bit, opcode = [15:12])

| Op | Mnemonic | Fields | Meaning |
|---|---|---|---|
| 0 | `CTL` | sub | `NOP, HALT, IRQ, PUSH, PULL, CAPON, CAPOFF, CAPMARK, TRIG` |
| 1 | `SET` | line[3:0], v, dly[6:0] | Drive ENG_OUT[line]=v, then wait dly |
| 2 | `MODE` | line[2:0], src[2:0], dly[5:0] | Set line source mux — **injection primitive** |
| 3 | `WAIT` | line[3:0], cond[1:0] (lo/hi/rise/fall) | Stall until cond or TO expiry (sets F) |
| 4 | `DLY` | imm[11:0] | Delay imm cycles |
| 5 | `DLYR` | Rn | Delay Rn cycles (baud from register) |
| 6 | `IN` | line[3:0], dly[7:0] | SR ← {SR, line}; BC-- |
| 7 | `OUT` | line[3:0], dly[7:0] | line ← SR bit; shift; BC-- |
| 8 | `LDI` | Rn, hi/lo, imm8 | Load immediate byte |
| 9 | `ALU` | op, Rd, Rs/SR/BC/TO | ADD SUB AND OR XOR CMP MOV |
| A | `JMP` | cond[2:0], addr[8:0] | always / Z / NZ / F / !F / BC==0 / … |
| B | `DJNZ` | Rn, addr | Loop |
| C | `CRC` | poly sel | One CRC step on SR (stretch: USB/CAN) |
| D–F | — | — | Reserved |

Line index 0–7 = uio, 8–11 = AUX_IN (read) / AUX_OUT (write). Bit order (LSB/MSB-first) is a config bit.

Sizes to sanity-check: UART TX ≈ 10–15 instr, UART RX ≈ 15, SPI master ≈ 15, I2C master ≈ 60–80, UART bit-flip injector ≈ 10, auto-baud ≈ 20.

---

## 6. Capture unit
- Watches up to 4 lines (WATCH bits); on any change writes a 16-bit word: `[15:12] line states | [11:0] Δt` (ticks of a programmable prescaler). Δt overflow → repeat-state entry with Δt = 0xFFF.
- Ring buffer in the capture region of SRAM; start/stop via `CAPON/CAPOFF`, `TRIG_IN`, or line pattern match; pre-trigger depth configurable.
- Host reads capture over SPI; host tool converts to **sigrok/PulseView** format so users get protocol decoders for free.

## 7. Memory
- **Decision (approved 2026-11-05, pending a harden of both macros on a branch):** two separate single-port SRAM macros: `RM_IHPSG13_1P_256x16` for program and `RM_IHPSG13_1P_512x16` for capture. See section 7.3.
- **Risk:** shuttle acceptance is unconfirmed, and no CMOS5L silicon result was found (section 7.2).
- Fallback: latch RAM — 128×16 program + 64×16 capture. Architecture must work at either size (address widths parameterised).

### 7.1 Area probe (yosys 0.33, typ corner, synthesis only)
Budget: the 6x4 die is 1289.28 × 710.64 µm = 916,214 µm² (`tile_sizes.yaml`). At the template's 60% density that is about 550,000 µm² of placeable cells, or about 23 µm² per cell at the 24K-cell guideline. Compare **area**, because memory cells are much larger than average.

| Block | Cells | Mapped µm² | % of die | % of 550K |
|---|---:|---:|---:|---:|
| 4×16 registers | 186 | 4,841 | 0.5 | 0.9 |
| Two 4×16 FIFOs | 478 | 11,863 | 1.3 | 2.2 |
| 128×16 flop RAM, enable muxes | 7,859 | 170,256 | 18.6 | 31 |
| 128×16 flop RAM, one ICG per word | 5,091 | 145,943 | 15.9 | 27 |
| 128×16 latch RAM, one ICG per word | 3,081 | 95,075 | 10.4 | 17 |
| 64×16 flop RAM, enable muxes | 4,046 | 86,549 | 9.4 | 16 |
| 64×16 latch RAM, one ICG per word | 1,563 | 48,013 | 5.2 | 8.7 |

Requested probe (registers + FIFOs + 128×16 RAM): flop 8,542 cells / 186,998 µm²; flop+ICG 5,743 / 162,596; latch 3,743 / 111,764.
Full fallback (128×16 + 64×16 + registers + FIFOs, sum of blocks): latch 5,308 cells / 159,792 µm² (29% of 550K); flop 12,569 cells / 273,509 µm² (50% of 550K).

Not included: CTS buffers, repair buffers, a second read port or arbitration. Flop counts include one tie cell per unreset flop (the lib has no reset-less flop; CI's stub netlist also shows one tie per flop). Not place-and-routed; latch RAM timing and CTS through 128 ICGs are unverified.

Calibration: the same script on the stub gives 150 cells / 2,221 µm², against 146 / 2,185 µm² from CI's yosys 0.66. The stub has no RAM, so this does not validate the RAM mapping.

### 7.2 SRAM on CMOS5L: evidence
- **PDK:** `libs.ref/sg13cmos5l_sram` is a symlink to the SG13G2 macros at the revision CI pins (`2bbec755`). The CMOS5L stack is M1–M4 + TopMetal1. Macro pins and obstructions use M1–M4 only, with power on M4.
- **Flow:** a public 6x4 CMOS5L project hardens three `1P_512x16` macros with gds, precheck and SDF gate-level jobs green. It relies on a custom PDN step and changes to the template's "do not change" settings.
- **Shuttle:** no macro found on CMOS5L shuttle TTIHP 0.4. Macros are found on SG13G2 shuttles 26a and 26b.
- **Silicon:** one user report of a `1024x8` test on SG13G2 (TTIHP 0.2). No CMOS5L silicon result found.
- **Open:** acceptance for the CMOS5L shuttle, asked of the organisers.

| Macro | Size (µm) | µm² | % of die | µm²/bit |
|---|---|---:|---:|---:|
| `1P_64x16_c2` | 236.8 × 64.36 | 15,240 | 1.7 | 14.9 |
| `1P_256x16_c2_bm_bist` | 236.8 × 118.78 | 28,127 | 3.1 | 6.9 |
| `1P_512x16_c2_bm_bist` | 236.8 × 191.34 | 45,309 | 4.9 | 5.5 |
| `2P_256x16_c2_bm_bist` | 419.95 × 136.97 | 57,521 | 6.3 | 14.0 |
| `2P_512x16_c2_bm_bist` | 402.61 × 219.77 | 88,482 | 9.7 | 10.8 |

Macro halos and PDN keep-outs are not included. Latch RAM for comparison: about 46 µm²/bit including the read mux.
`1P_512x16` `A_DOUT` clock-to-output, worst liberty table entry: 3.93 ns typ, 6.56 ns slow, leaving about 13 ns at the slow corner for decode and next-PC at 50 MHz.

### 7.3 Decision: two macros, not one unified macro
A single-port macro cannot serve an instruction fetch and a capture write in the same cycle. Separate macros mean the two never collide: **program `1P_256x16` + capture `1P_512x16` = 73,436 µm² (8.0% of die)**. Keep address widths parameterised so the alternatives stay available.

Options considered (memory area only, before halos):

| Option | µm² | Words (program/capture) | Outcome |
|---|---:|---|---|
| Two macros, 256 + 512 | 73,436 | 256/512 | **Chosen** |
| Two macros, 512 + 512 | 90,618 | 512/512 | Backup if firmware outgrows 256 words |
| Two macros, 256 + 256 | 56,254 | 256/256 | Only if area gets tight |
| One 1-port 512, fetch has priority, small capture buffer | ~51,000 | 512 shared | Runner-up: engine timing stays exact, but capture can overflow in tight loops and needs more proofs |
| One 2-port 512 | 88,482 | 512 shared | Rejected: more area than the chosen option for fewer words |
| Latch RAM | 143,088 | 128/64 | Fallback if macros are refused or cannot be hardened |
| Flop RAM | 256,805 | 128/64 | Rejected: area |
| External memory chip | small | large | Rejected: breaks exact timing, no spare pins, adds a required external part |

**Reverse the decision if:** macros are not accepted or the harden cannot be made to pass (latch fallback); firmware does not fit in 256 words (512 + 512).

## 8. Host interface (SPI slave, mode 0)
Commands: `WR_MEM addr, n, data…` · `RD_MEM` · `WR_REG` · `RD_REG` · `START` · `STOP` · `STEP` · `STATUS` · `FIFO_RD/WR`.
All host-domain signals cross into the core clock via synchronizers (SCK is ≤ core/4).

Host software (Python): assembler, loader, CLI (`splice load/run/capture/decode`), sigrok export. Runs against RTL sim (cocotb) **and** the real board (RP2040 on TT demo board as SPI host).

---

## 9. Verification plan (a scored axis — make it a headline)
1. **Golden model:** cycle-exact Python ISA + line-fabric simulator.
2. **Differential testing:** random constrained programs run on RTL (cocotb/Icarus or Verilator) and model; compare pin traces cycle-by-cycle.
3. **Protocol tests:** firmware UART/SPI/I2C vs independent cocotb bus models (cocotbext-uart / -spi; I2C model written ourselves if needed).
4. **Formal (SymbiYosys):** ODB no-deadlock & bounded release; every instruction's cycle count exact; FIFO/SPI safety; PASS mode == identity (with fixed latency).
5. **Identity fuzz:** random traffic through PASS must emerge bit-identical, shifted by exactly the specified latency.
6. **Gate-level sim** from CI netlist; STA at ss corner.
7. **Coverage report** (ISA opcodes × conditions, fabric modes × transitions) published in the README.
8. **AI-assisted verification log:** document where AI generated tests/RTL and how each was independently checked — judges asked about this.

## 10. Demos for the submission
1. **UART man-in-the-middle:** MCU ↔ sensor; SPLICE rewrites a reading in flight.
2. **I2C fault injection:** flip an ACK → NACK on the 3rd transaction; show the MCU driver's error path (or its bug).
3. **Auto-baud sniff:** unknown UART → detected rate + decoded bytes.
4. **SPI flash read capture** → PulseView decode.

---

## 11. Schedule (solo, 20+ h/week)

| Week | Dates | Deliverable | Gate |
|---|---|---|---|
| 1 | Nov 9–15 | Repo from cmos5l template; CI green; **SRAM-on-CMOS5L experiment**; area of a stub core | Know memory size |
| 2 | Nov 16–22 | Python golden model + assembler; ISA frozen v1 | Model runs UART TX |
| 3 | Nov 23–29 | Engine RTL + SPI host IF; differential test harness | UART/SPI master pass |
| 4 | Nov 30–Dec 6 | I2C master firmware; formal on engine timing; **first full harden** | Valid emulator entry ✅ |
| 5 | Dec 7–13 | Line fabric (PASS/INV/F0/F1/ENG) + identity fuzz | Observe pass-through |
| 6 | Dec 14–20 | Capture unit + sigrok export; auto-baud firmware | Observe mode ✅ |
| 7 | Dec 21–27 | UART injection + store-and-forward rewrite demos | Interpose (UART) ✅ |
| 8 | Dec 28–Jan 3 | ODB bridge + formal proof; I2C pass-through + injection | Interpose (I2C) |
| 9 | Jan 4–10 | Area/timing closure, GL sim, coverage | Freeze RTL |
| 10 | Jan 11–18 | README, datasheet, demo writeups/video, submit | Submitted |

**Cut lines if behind:** drop CRC op → drop ODB (keep I2C observe-only via plain inputs) → shrink capture. Never cut verification or the week-4 valid entry.

## 12. Open questions / risks
- [ ] SRAM macro availability & PDN on CMOS5L (week 1): flow precedent found, shuttle acceptance and our own harden still open (section 7.2)
- [x] Unified single-port memory conflicts with fixed cycle counts: resolved with two macros (section 7.3)
- [ ] Does 6x4 fit engine + fabric + capture? (synth early, week 1–3)
- [ ] 8x4 approval from Jane Street (would relax area)
- [ ] Exact pass-through latency through TT's pin mux (GL sim + reading TT mux docs)
- [ ] ODB holdoff length vs bus capacitance/rise time
- [ ] Use engine count = 1. A second engine (full-duplex rewrite) only if area allows.
