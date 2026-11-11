# SPLICE — Programmable Bus Interposer ASIC
**Architecture spec v0.1** · Jane Street Protocol Emulator ASIC Competition · Tiny Tapeout IHP CMOS5L, 8x4 tiles · Deadline Jan 18, 2027

> Working name: SPLICE. Rename freely.

---

## 1. Pitch (one paragraph for the judges)

SPLICE is a protocol emulator that you reprogram in firmware, for protocols its designers never planned for. A small engine with exact, known instruction timing does the work, so UART, SPI, I2C, JTAG, CAN or a protocol nobody has written yet is a program you load over SPI, in either the controller or the target role. That same engine controls the pass-through path of every bus line, which gives SPLICE something an ordinary emulator can't do: **it can sit inline on a live bus between two chips and alter traffic as it passes.** It can flip one chosen bit, force an I2C NACK on the third transaction, or rewrite a byte with no added frame of delay, in both directions at once. Bidirectional I2C goes through an open-drain bridge whose release and freedom from deadlock are formally proven. Every result is repeatable, because each change lands on an exact cycle.

> **Status of these claims (2026-11-06):** this is the target for the finished chip. So far it is backed by a cycle-exact model and synthesis sketches, not RTL or silicon, and the bridge proof doesn't exist yet (planned for week 8). Anything still untrue at submission has to come out of this paragraph.
>
> **Why this order:** the organisers say flexibility is the main thing they look at, followed by which protocols are shown, what's unique, and how it was designed and verified. So the pitch proves flexibility first and uses interposing as the differentiator. A keyword scan of 22 public entrants' READMEs found provable timing, on-chip logic analysers and protocol learning already in use, but no inline traffic alteration and no open-drain bridge. It's a README scan, so it can't rule out private work or unstated features.

### What the organisers look for
From their update email: there is no fixed weighting, and **flexibility is the main thing: can the chip be reprogrammed for protocols it wasn't designed for?** Beyond that, they look at which protocols are demonstrated, anything unique the architecture makes possible, and how it was designed and verified. Around four designs are planned for tape-out.

**Required at submission:** public repo with RTL and tests, passing GDS and precheck from the Tiny Tapeout flow, architecture and instruction set documentation, assembler or toolchain with example programs, a verification write-up, and a description of how AI tools were used. A video is optional.

---

## 2. Top-level block diagram

```
            ui_in[2:0]                    uo_out[0]
 host  ──► SPI slave ──► Host ctrl ◄──────► MISO
           (load prog, regs, FIFOs, capture readout)
                 │
                 ▼
        ┌────────────────────┐     ┌──────────────┐
        │  Timing engine     │◄───►│ Program SRAM │  512x16
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
| `ui[7:4]` | AUX_IN[3:0] — engine-readable inputs (lines L8–L11). In quad host mode AUX_IN[2:0] are host IO1–3 |
| `uo[0]` | Host SPI MISO |
| `uo[1]` | IRQ (engine/host event) |
| `uo[2]` | TRIG_OUT (fires on match / injection — scope trigger) |
| `uo[3]` | ACTIVE / heartbeat |
| `uo[7:4]` | AUX_OUT[3:0] — engine-drivable push-pull outputs (e.g. extra SPI CS in emulate mode). In quad host mode AUX_OUT[2:0] are host IO1–3 |
| `uio[3:0]` | Lines L0–L3 = **A side** of pairs 0–3 |
| `uio[7:4]` | Lines L4–L7 = **B side** of pairs 0–3 (partner of Lx is Lx^4) |

Protocol fits (interpose): UART = 2 pairs (TX, RX) · I2C = 2 pairs (SCL, SDA) · SPI = 4 pairs (SCK, MOSI, MISO, CS) — uses all.
Emulate mode: all 8 uio are plain GPIO + 4 AUX outs.

**Electrical limits (TT spec, provisional: the organisers say there are no characterized CMOS5L pad numbers yet):** output ≤ 33 MHz, input ≤ 66 MHz, 4 mA drive, pads not 5 V tolerant, ~20 ns worst pad round-trip. I2C needs external pull-ups on **both** A and B sides.

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
FAST     : PASS and INV tap the first synchronizer flop (off by default)
```

**FAST (decided 2026-11-06):** in the model it raises inline SPI from 4.17 to 6.25 MHz and cuts pin-to-pin latency from 60 to 40 ns. Costs: a line passes through one flop instead of two, so metastability can reach the output pin, and timed flips gain 1 cycle of skew that firmware must correct. It's per line and off by default, so only a line where someone turns it on carries the risk. The risk hasn't been measured.

The engine changes SRC with `MODE` instructions **on an exact cycle**. That one mechanism gives all interposition:

- **Cut-through fault injection:** line in PASS; engine waits for the start bit, delays to bit k, switches to INV for one bit time, back to PASS → single bit flip, zero added latency beyond the fabric.
- **Drop / glitch:** F0/F1 for N cycles.
- **Store-and-forward rewrite:** line in ENG; engine `IN`s a byte from A, modifies it (ALU / compare), `OUT`s it on B. Adds one frame of latency (fine for UART).

### 4.1 Open-drain bridge (ODB) — the hard problem
Bidirectional I2C pass-through: either side may pull low; SPLICE must mirror it to the other side without latching itself up (when we drive B low we also *read* B low).

Per-pair FSM: `IDLE → A_HOLDS` (A low first: drive B low) / `B_HOLDS` (mirror) → on originator release: stop driving, enter `HOLDOFF` for N cycles (≥ sync latency + pad delay) ignoring the driven side, → `IDLE`. Both low simultaneously → pick A, record arbitration flag.

**Holdoff is a per-pair register (decided 2026-11-06).** In the model, too short and the bridge reads its own slowly rising line as a new low; too long and it misses a real edge at 400 kHz. No fixed value covers both a 1000 ns rise at 100 kHz (needs 64+ cycles) and 400 kHz (48 or fewer). See [`isa/flexibility.md`](isa/flexibility.md). Commercial buffers avoid this with an analog offset that our digital pads can't use.

**Must be formally proven:** no deadlock, never drives both sides from each other, release within bounded cycles. Commercial chips (I2C buffers/repeaters) exist only for this problem — solving it cleanly is a headline.

### 4.2 Pass-through latency (estimate — verify in gate-level sim)
2-FF synchronizer + output register = 3 cycles @ 50 MHz = 60 ns, + ~20 ns pad ≈ **~80 ns**.
→ comfortable for UART ≤ 1 Mbaud, I2C 100/400 kHz, SPI interposed ~2–4 MHz (MISO round-trip is the limit). An optional unsynchronized combinational path for observe-only mode is a stretch item.

---

## 5. Timing engine and ISA v1

**ISA v1, approved 2026-11-06, open to change if needed.** The evidence is in [`isa/exploration.md`](isa/exploration.md) and [`isa/flexibility.md`](isa/flexibility.md). The opcode map below is generated by `lab/isa/isa_v1.py`, which proves it is a valid prefix code that fits 16 bits, and checks 20 study programs against the field widths. No RTL exists yet, so all of this is proven in the model only.

### 5.1 Timing rules
- **One instruction is 1 cycle plus its delay field.** Only explicit wait instructions can stall: `WAIT`, `WAITT`, `PULLW`, and any instruction with the tick qualifier.
- **Taken jumps cost nothing extra.** The macro's read data is decoded in the same cycle. If this misses timing in RTL, the fallback is a fixed 1-cycle penalty.
- **Pin effects land on the clock edge ending the instruction's first cycle.** Inputs pass a 2-flop synchronizer, so `IN`, `WAIT` and `PROBE` see a pin 2 cycles late. These latencies are fixed and published.
- **First instruction runs a fixed 1 cycle after `START`.** The macro holds its output during delays.
- **Missed tick:** if a tick fires while the last one is still pending, the context's `M` flag is set, so a late program is visible and doesn't silently produce a wrong waveform. `M` changes on the following cycle and beats `JMP M` clearing it in the same cycle, so a miss can't be lost. `JMP M` reads and clears it. Until the adaptability trial this set `F`, which also means a full FIFO and a WAIT timeout, so a timer left running made both look true and firmware had to stop it first.

### 5.2 State
Each context has its own `PC`, `R0`–`R3` (16-bit), `SR` (16-bit), `BC`, `TO`, flags `Z`, `F` and `M`, CRC polynomial and last shifted bit, link register, bit timer, tick source, side-set line, bit order and edge memory. Registers are 4 only: CAN and USB use all of them.

**Scheduling is set at `START` (v2, approved 2026-11-10 for 8x4):** 1, 2, 3 or 4 contexts take turns, one cycle each, so with `n` contexts each runs at 1/`n` rate. All four contexts are full: each has every register above and its own timer, which keep running every cycle. They share the program macro, and the fetch in each cycle is for the next slot's context. Host register 0 holds the count less one (writing 1 still means two), and registers 16-19 hold the start addresses of contexts 2 and 3.

**Links between contexts:** two 4-word link FIFOs. Each context's `PULL`/`PULLW` and `PUSH` are routed, by host registers 20 and 21 (2 bits per context: host, link 0 or link 1), so one context's output is another's input with no host in between. Programs don't change: a UART receiver and an SPI controller written for the host become a bridge by routing alone. A full link sets `F` on `PUSH`, like the host FIFO. Register 22 reads the link levels and the halted flags.

### 5.3 Bit timer and tick
The qualifier `.t` on an instruction means: wait for the next tick, then execute. A tick comes from one of two sources:
- **The period timer,** 16 bits, so up to 65,535 cycles (about 760 baud at 50 MHz). `TSET` sets it from a 13-bit constant, up to 8,191 cycles, and `TSETR` from any register operand, so a measured bit time can be applied once. `TPHASE` restarts its phase from a preset (one, a half, or one and a half periods) or from `R1`.
- **An input edge,** set by `TSRC`: rising, falling or both. This is how firmware follows an external clock.

### 5.4 Instructions

| Group | Instructions | Notes |
|---|---|---|
| Pins | `SET`, `MODE`, `OUT`, `IN`, `PROBE`, `WAIT` | `MODE` is the **injection primitive**. `OUT` with no-shift (`ns`) lets `IN` do the shift, for full duplex on one register |
| **Fused bit loops** | **`OUTB`, `INB`** | **One bit, shift, and loop back while bits remain.** This is where most of the code saving comes from |
| Timing | `WAITT`, `TSET`, `TSETR`, `TPHASE`, `TSRC`, `DLY`, `DLYR`, `TCAP` | `TSETR` takes the period from a register. Added after the trial. `TCAP rd` reads the cycles between the context's last two ticks, saturating at 0xFFFF, latched by hardware at every tick. With `TSRC line, both` that is the length of each line interval, so firmware can measure an edge after it has passed instead of waiting at it. `TCAP.t` reads the interval that ended at the tick it waited for |
| FIFO | `PULLW`, `PULL`, `PUSH` | `PULLW` stalls until data arrives and loads `BC`. `PULL` doesn't stall, and sets `F` when empty |
| Setup | `BCI`, `SRI`, `MSB`, `SIDE` | `SIDE` picks the line for side-set: a second line changing on the same edge |
| **Lanes** | `LANES w` | `OUT`, `IN`, `OUTB` and `INB` move 1, 2, 4 or 8 lines at once: the lane group is the lane-aligned block of lines containing the named one, line base+k being bit k, and `SR` shifts by the width. `BC` counts groups. SD 4-bit, QSPI, eMMC 8-bit and parallel buses. Per context; `LANES 1` is the v1 behaviour |
| **Line coder** | `CODE` | Per context, at `LANES 1` (v2). Options: `nrzi`; `ones n` (a 0 after n ones: USB 6, HDLC 5) or `equal n` (the complement after n equal bits: CAN 5); `man` (Manchester, IEEE half-bits `!b, b`, or `man thomas` for `b, !b`); `diff` (also drive line ^ 1 with the complement: USB D+/D-, RS-485); `lvl` (NRZI receive's starting level); `sync` (clock recovery: an edge on the `TSRC` line re-phases the period timer to half a period, so ticks land mid-bit). `OUT` and `OUTB` code what they send, and `IN` and `INB` decode and destuff what they read. A stuff bit, or Manchester's first half-bit, repeats the instruction at its next tick without shifting or counting, so `BC`, loops and `CRC` see data bits only. A wrong stuff bit on receive sets `F`. Manchester is transmit only, and doesn't combine with NRZI or stuffing. `code off` clears it |
| Tables | `LDP rd, rs`, `JMPR s` | `LDP` reads the program word at the address in `rs` into `rd`, taking 2 of the context's slots: the word is fetched in place of that context's next instruction fetch. `JMPR` jumps to a register or operand. Together: 4B/5B and other code tables, and jump tables for protocol state machines. `.word` puts data in the program |
| Data | `LDI`, `MOV`, `ADD`, `ADDR`, `XOR`, `AND`, `CMP`, `CMPU`, `CMPI`, `SHR`, `SHL`, `RAND`, `POLY`, `CRC`, `CRCW` | `ADDR` adds a register operand, and sets `Z` but not `F`, like `ADD`. `CRC r, msb` or `lsb` is one CRC step on `Rr`, on the bit last shifted by `OUT`, `OUTB`, `IN` or `INB`, with the polynomial `POLY` set: left with a left-aligned polynomial, or right with a reflected one. It sets `Z` but not `F`. With lanes, `CRC r, dir, lane` steps on one lane's bit, and `CRCW r, dir, lane` steps one lane's 4 bits of `SR` (2 at 8 lanes) in one cycle, for per-line CRCs like SD's four CRC-16s. Shifts put the bit shifted out in `F`. `RAND` is one LFSR step, for seeded and repeatable fuzzing. `CMPU a, b` is unsigned: `Z` when equal, `F` when a < b |
| Control | `JMP`, `DJNZ`, `CALL`, `RET`, `TSTB`, `NOP`, `HALT` | `CALL` is one level. `TSTB` sets `Z` from the next output bit. `JMP` conditions: always, Z, NZ, F, !F, BC=0, BC≠0, M (missed tick; reads and clears). After `PROBE`, `Z` means the line is high, so the assembler takes `jmp hi` and `jmp lo` for `z` and `nz`, and warns on `z` or `nz` straight after a `PROBE` |
| Chip | `IRQ`, `TRIG`, `CAPON`, `CAPOFF`, `CAPMARK` | |

**Not yet run by any study program:** `IRQ`, `TRIG`, `CAPON`, `CAPOFF`, `CAPMARK`, `CMP`, `DLYR`, `PULL` and `TPHASE`. The golden model must test these first.

**Firmware rules found by testing:** set a line's value before `MODE` takes it, or it glitches at startup. Re-arming after a flip needs a frame holdoff, or a data edge reads as a new start bit.

### 5.5 Opcode map

| Opcode | Instruction | Fields (bits) |
|---|---|---|
| `000` | `TSET` | p 13 |
| `0010` | `JMP` | c 3, to 9 |
| `0011` | `CALL` | to 9 |
| `0100` | `DLY` | n 12 |
| `0101` | `LDI` | r 2, hl 1, v 8 |
| `0110` | `SRI` | v 8, sh 4 |
| `01110` | `SET` | line 4, v 1, tick 1, side 2, dly 3 |
| `01111` | `MODE` | line 3, src 3, tick 1, dly 4 |
| `10000` | `OUT` | line 4, ns 1, tick 1, side 2, dly 3 |
| `10001` | `IN` | line 4, tick 1, side 2, dly 4 |
| `10010` | `OUTB` | line 4, tick 1, side 2, rel 4 |
| `10011` | `INB` | line 4, tick 1, side 2, rel 4 |
| `10100` | `DJNZ` | r 2, to 9 |
| `10101` | `(reserved) ARM` | rule 1, trig 3, c 1, line 3, src 3 |
| `10110` | `(reserved) MATCH` | rule 1, trig 3, line 3, src 4 |
| `101110` | `WAIT` | line 4, c 2, stamp 1 |
| `101111` | `ADD` | r 2, v 8 |
| `110000` | `CMPI` | a 2, v 8 |
| `110001` | `CODE` | nrzi 1, lvl 1, kind 1, n 3, man 1, pol 1, diff 1, sync 1 |
| `110010` | `(reserved) CODE2` | mode 4, n 3, pair 3 |
| `11001100` | `(reserved) CRCSR` | inv 1, keep 1, shift 1 |
| `11001101` | `AND` | d 2, s 3 |
| `11001110` | `BCI` | n 5 |
| `11001111` | `CAPMARK` | None |
| `11010000` | `CAPOFF` | None |
| `11010001` | `CAPON` | None |
| `11010010` | `CMP` | a 3, b 3 |
| `11010011` | `DLYR` | r 2 |
| `11010100` | `HALT` | None |
| `11010101` | `IRQ` | None |
| `11010110` | `MOV` | d 3, s 3 |
| `11010111` | `MSB` | v 1 |
| `11011000` | `NOP` | tick 1, side 2 |
| `11011001` | `PROBE` | line 4, tick 1 |
| `11011010` | `PULL` | None |
| `11011011` | `PULLW` | n 5 |
| `11011100` | `PUSH` | None |
| `11011101` | `RET` | None |
| `11011110` | `SIDE` | line 4 |
| `11011111` | `TPHASE` | mode 2 |
| `11100000` | `TRIG` | None |
| `11100001` | `TSRC` | line 4, want 2 |
| `11100010` | `TSTB` | None |
| `11100011` | `WAITT` | None |
| `11100100` | `XOR` | d 2, s 3 |
| `1110010100` | `RAND` | r 2 |
| `1110010101` | `SHL` | r 2 |
| `1110010110` | `SHR` | r 2 |
| `11100110` | `ADDR` | d 2, s 3 |
| `11100111` | `TSETR` | s 3 |
| `11101000` | `POLY` | s 3 |
| `11101001` | `CRC` | r 2, dir 1, word 1, lane 3 |
| `11101010` | `CMPU` | a 3, b 3 |
| `11101011` | `TCAP` | r 2, tick 1 |
| `11101101` | `LDP` | d 2, s 2 |
| `11101110` | `JMPR` | s 3 |
| `11101111` | `LANES` | w 2 |

Field names: `line` 0–7 is a bus line and 8–11 is AUX; `side` is enable and value; `rel` branches back up to 15 words; `to` is an absolute address, 9 bits, for the 512-word program macro (v2; v1 had 256 words); `hl` selects `LDI`'s high or low byte; `sh` is `SRI`'s shift. The `stamp` bit in `WAIT` is held for the capture unit. `ADDR`, `TSETR`, `POLY` and `CRC` were added after v1 in free codes, so no opcode moved. The assembler picks `ADDR` and `TSETR` when the operand is a register: `add r0, r1`, `tset r1`. `LDP`, `JMPR` and `LANES`, and `CRC`'s `word` and `lane` fields, came from `explore/isa-lanes-int` (lab/explore/isa.md) and were approved for v2 on 2026-11-10; every program that doesn't use them behaves as before. That branch's `ROUTE` mailbox (`11101100`) was dropped, since the link FIFOs do the same job for any pair of contexts; its code is free again. About 6.7% of the encoding space is free.

**Reserved encodings:** `ARM` and `MATCH` for hardware match rules, `CODE2` and `CRCSR` (left over from the line-coder study; `CODE` itself is built in v2).

### 5.6 Optional hardware
| Feature | Status | Why | Area (sketch) |
|---|---|---|---|
| **Line coder (`CODE`):** NRZI, bit stuffing, Manchester, differential pair, clock recovery | **Built in v2** (CRC is the separate `CRC`/`CRCW`; arbitration stays in firmware) | USB LS transmit 29 words against 59 and down to 6 cycles a bit against 14; receive 27 words against 57, 12 cycles a bit against 33. **Both run at USB's rate in one of four contexts, where the software versions can't keep up** (`test_protocols.py`, the same wire checkers as the software versions) | Measured at gds |
| **Hardware match rules** | **Week-7 stretch** | Engine-free rules on live traffic. Unique among the entrants I surveyed, but only tested on UART so far, so they must also work on SPI and I2C | About 30K µm² |

## 6. Capture unit
- Watches 4 lines, chosen by host registers. On any change it writes one 16-bit word: `[15:12]` line states, `[11:0]` Δt, in ticks of a programmable prescaler. The first entry of a run records the starting state with Δt = 0.
- **Reserved Δt values:** `0xFFF` means nothing changed for `0xFFD` ticks, and counting continues. `0xFFE` is a mark from the engine (`CAPMARK`), which takes no time.
- **Start and stop:** `CAPON`/`CAPOFF`, host register 10, or `TRIG_IN`'s rising edge.
- **Without ring mode,** capture stops when the 512 words are full.
- **Ring mode** wraps and keeps the newest entries. A trigger (`TRIG_IN`, or `CAPMARK` if enabled) then stops capture a set number of entries later, which gives pre-trigger history.
- `STOP` ends capture but keeps results readable. The next `START` clears them. The host can only read capture memory while capture isn't active.
- `host/splice_host/capture.py` turns entries into timed edges and writes VCD, which PulseView opens, so users get its protocol decoders for free.
- **Not built:** line-pattern match start.

## 7. Memory
- **Decision (approved and hardened 2026-11-05; program memory grown to 512 words for v2 on 2026-11-10):** two separate single-port SRAM macros, both `RM_IHPSG13_1P_512x16`: one for program and one for capture. See section 7.3. Four contexts need room for several firmwares at once: SD with lanes alone takes 246 words.
- **Accepted:** the organisers' update email says IHP SRAM macros may be used, including more than one, if they fit and pass precheck. CMOS5L has no silicon back yet, so macro behaviour on this process is unproven.
- Fallback: latch RAM — 128×16 program + 64×16 capture. Architecture must work at either size (address widths parameterised).

### 7.1 Area probe (yosys 0.33, typ corner, synthesis only)
Budget (written for 6x4): the 6x4 die is 1289.28 × 710.64 µm = 916,214 µm² (`tile_sizes.yaml`). At the template's 60% density that is about 550,000 µm² of placeable cells, or about 23 µm² per cell at the 24K-cell guideline. The design now targets 8x4, 1724.16 × 710.64 µm = 1,225,257 µm², about 735,000 µm² placeable at 60%; the table below keeps its 6x4 percentages. Compare **area**, because memory cells are much larger than average.

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
- **Our harden:** both macros pass gds, precheck and gate-level tests with our own power step (CI run 42076994258, stub plus self-test). See `macro/README.md`, including 15 slew and capacitance violations at macro pins still to clear.
- **Acceptance:** confirmed by the organisers' update email. An independent walkthrough ([IHP SRAM macro on a CMOS5L tile](https://kdp1965.github.io/ihp-um-janestreet-prism/ihp_sram_macro.html)) reports the same Metal4 obstruction bar and the same absence of real metal under it that `flow/check_macro_columns.py` found.

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
| Two macros, 256 + 512 | 73,436 | 256/512 | Chosen for v1 |
| Two macros, 512 + 512 | 90,618 | 512/512 | **Chosen for v2** (8x4, four contexts) |
| Two macros, 256 + 256 | 56,254 | 256/256 | Only if area gets tight |
| One 1-port 512, fetch has priority, small capture buffer | ~51,000 | 512 shared | Runner-up: engine timing stays exact, but capture can overflow in tight loops and needs more proofs |
| One 2-port 512 | 88,482 | 512 shared | Rejected: more area than the chosen option for fewer words |
| Latch RAM | 143,088 | 128/64 | Fallback if macros are refused or cannot be hardened |
| Flop RAM | 256,805 | 128/64 | Rejected: area |
| External memory chip | small | large | Rejected: breaks exact timing, no spare pins, adds a required external part |

**Reverse the decision if:** macros are not accepted or the harden cannot be made to pass (latch fallback); firmware does not fit in 256 words (512 + 512).

## 8. Host interface (SPI slave, mode 0)
SCK is at most core/8, oversampled with 2-flop synchronizers, so the whole chip is one clock domain. Bytes are MSB first, and 16-bit words are big-endian.

| Byte | Command | Then | Notes |
|---|---|---|---|
| `0x01` / `0x02` | Write / read program memory | `aH aL` then words | Stopped only. Auto-increments |
| `0x03` / `0x04` | Write / read capture memory | `aH aL` then words | Stopped only |
| `0x05` / `0x06` | Write / read register | `reg` then value | See below |
| `0x10` / `0x11` | `START` / `STOP` | | `START` keeps FIFO words pushed beforehand. `STOP` releases every line |
| `0x12` | Status | 1 to 3 bytes | `{0, 0, irq, e2h_empty, h2e_full, halted[1:0], running}`, then the engine-to-host level and the host-to-engine free space in words. A host that reads one byte sees no change |
| `0x20` / `0x21` | Write / read FIFO | words | Writing a full FIFO drops the word. Reading an empty FIFO returns 0. A word leaves the FIFO when the host starts clocking it, so ending a read after N words consumes exactly N |
| `0x22` | Exchange (`XFER`) | `nw`, pad, then word slots | Full duplex. During `nw` and the pad, SPLICE sends the level and free space, both taken when the command byte ends. Each slot then pushes the host's word for the first `nw` slots, if there's room, and pops a word for the first `level` slots. Later slots read as 0 without popping, so a stored 0 is data and a word that arrives during the burst waits for the next XFER. Ending inside a slot pushes nothing and, as with `0x21`, spends that slot's engine word |

**Quad mode (v2, register 23 bit 0):** every command above, with 4 bits each way on every SCK edge, so a byte takes 2 SCK periods instead of 8. IO0 is MOSI in and MISO out; IO1-3 are AUX_IN0-2 in and AUX_OUT0-2 out. The high nibble goes first, its bit 3 on IO3. Inputs and outputs are separate pins, so it stays full duplex with no turnaround, and the same core/8 SCK limit applies. It takes effect from the next CS low; writing register 23 as 0 in quad mode leaves it. While it's on, AUX_OUT0-2 belong to the host and AUX_OUT3 stays the engine's; firmware can still read AUX_IN0-2, but they carry host data. The board agent's `q 1` request switches SPLICE and itself together (`AgentLink.quad()`).

Each FIFO holds 8 words (`SPLICE_FIFO_LOG2`, a build define; tests read the depth from STATUS after reset). With XFER, full-duplex HDLC at 1 Mbit/s each way keeps up down to SCK = core/20 with 8 words, against core/8 for the STATUS, write, read loop at any depth (`test/test_hostlink.py`).

Registers: 0 contexts less one (bits 1:0), 1 open-drain lines, 2 fast pass-through lines, 3 bridge holdoff, 4 and 5 context 1's start address, 6 and 7 capture's 4 watched lines, 8 and 9 capture prescaler, 10 capture control (write bits 2:0 mode, bit 7 start, bit 6 stop; read `{active, wrapped, triggered, 0, 0, mode}`), 11 and 12 entries kept after a trigger, 13 and 14 capture write pointer (read only), 16-19 contexts 2 and 3's start addresses, 20 and 21 each context's PULL and PUSH routing, 22 link and halted flags (read only), 23 quad host mode. **`STEP` isn't built yet.**

Host software (Python): assembler, loader, CLI (`python3 -m splice_host`: setup, load, run, stop, status, reg, push, pop, capture), VCD export for PulseView. The library runs against RTL sim (cocotb) and, through a small MicroPython agent on the ETR demo board's RP2350, the real board. The agent's SPI is bit-banged because `ui[2:0]` and `uo[0]` aren't on a hardware SPI pin set. It is checked against RTL by replaying its pin activity, and hasn't run on a board.

---

## 9. Verification plan (a scored axis — make it a headline)
1. **Golden model:** cycle-exact Python ISA + line-fabric simulator.
2. **Differential testing:** random constrained programs run on RTL (cocotb/Icarus or Verilator) and model; compare pin traces cycle-by-cycle.
3. **Protocol tests:** firmware UART/SPI/I2C vs independent cocotb bus models (cocotbext-uart / -spi; I2C model written ourselves if needed).
4. **Formal (Yosys temporal induction, no `sby`):** ODB no-deadlock and bounded release, and FIFO safety, are proved. Instruction cycle counts and PASS identity have simulation evidence only so far. Results and gaps are in [`verification.md`](verification.md).
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
| 2 | Nov 16–22 | Python golden model + assembler, from ISA v1 (approved Nov 6) | Model runs UART TX |
| 3 | Nov 23–29 | Engine RTL + SPI host IF; differential test harness | UART/SPI master pass |
| 4 | Nov 30–Dec 6 | I2C master firmware; formal on engine timing; **first full harden** | Valid emulator entry ✅ |
| 5 | Dec 7–13 | Line fabric (PASS/INV/F0/F1/ENG) + identity fuzz | Observe pass-through |
| 6 | Dec 14–20 | Capture unit + sigrok export; auto-baud firmware; line coder | Observe mode ✅ |
| 7 | Dec 21–27 | UART injection + rewrite demos; stretch: match rules | Interpose (UART) ✅ |
| 8 | Dec 28–Jan 3 | ODB bridge + formal proof; I2C pass-through + injection | Interpose (I2C) |
| 9 | Jan 4–10 | Area/timing closure, GL sim, coverage | Freeze RTL |
| 10 | Jan 11–18 | README, datasheet, demo writeups/video, submit | Submitted |

**Cut lines if behind:** match rules (stretch) → line coder (software works for USB and CAN) → capture features, then the 512×16 macro to 256×16 → last resort, the open-drain bridge (keep I2C observe-only via plain inputs). Never cut verification, the week-4 valid entry, or UART interposing, because interposing is what sets SPLICE apart.

## 12. Open questions / risks
- [x] SRAM macro integration on CMOS5L: hardened (section 7.2)
- [x] Macro acceptance on the CMOS5L shuttle: confirmed by the organisers
- [ ] Pad characterisation: none exists yet for CMOS5L, so treat the electrical limits in section 3 as provisional
- [ ] Program reload: a deselected design powers down and loses its state, so the host must reload after selection
- [ ] Slew and capacitance violations at macro pins: clear in the real design (`macro/README.md`)
- [x] Unified single-port memory conflicts with fixed cycle counts: resolved with two macros (section 7.3)
- [x] Does 6x4 fit engine + fabric + capture? Yes: every build through exp/integrate-1 closed on 6x4
- [x] 8x4 approval: confirmed final by the organisers on 2026-11-09; the die is 1724.16 × 710.64 µm (+33.7%)
- [ ] Exact pass-through latency through TT's pin mux (GL sim + reading TT mux docs)
- [ ] ODB holdoff window on real buses: the register is decided (section 4.1), real thresholds are untested
- [ ] USB and CAN receive, clock recovery and two protocols at once: not yet tried on ISA v1
- [ ] Use engine count = 1. A second engine (full-duplex rewrite) only if area allows.
