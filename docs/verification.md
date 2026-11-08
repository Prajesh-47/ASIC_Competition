# SPLICE verification

How the design is checked, what each check found, and what remains unchecked. The commands are at the end.

## Approach

The RTL and most tests were written with AI assistance (`docs/ai-log.md`), so a test passing is weak evidence on
its own: the same mistake could be in both. The checks are built so that doesn't decide the result:

- **Expected results come from elsewhere.** Protocol tests judge waveforms with device models written from each
  protocol's rules, not from the RTL. CRCs are computed separately from the firmware.
- **Tests are tested.** Deliberately broken RTL must make the suite fail, and formal cases that should fail must
  produce a counterexample.
- **Anything that might pass vacuously has a control.** For example, CAN receive with its clock recovery removed
  must fail.

## The checks

| Check | What it does | Where |
|---|---|---|
| Golden model | A separately written, cycle-exact Python model of the ISA and line fabric | `model/splice/model.py` |
| Differential | 80 random programs, one and two contexts. Every output pin each cycle, and all architectural state every 16 cycles, model against RTL | `test/test.py` |
| Coverage | 46 bins: every jump condition taken and not, every wait outcome, line mode, tick source and `.t` form, plus every instruction. A test fails if any is empty | `test/test.py` |
| Protocol tests | Programs loaded over SPI, against device models | `test/test_protocols.py` |
| Interposing | Bit flip inside live UART, forced I2C NACK through the bridge | `test/test.py`, `test/test_protocols.py` |
| Pass-through identity | Random traffic, including one-cycle pulses, through all pairs both ways. Output must equal input at exactly the latency in the spec (3 cycles, or 2 on a fast line) | `test/test.py` |
| Capture | Edges reconstructed to the cycle, ring mode, triggers, gaps across repeat entries | `test/test_capture.py` |
| Host tools | Board agent's SPI replayed onto RTL at SCK = core/8; CLI and serial protocol against stand-ins | `test/test_host.py`, `host/tests/` |
| Assembler | Encoding round trips, label sizing and the opcode prefix code | `model/tests/` |
| Formal | Temporal induction with Yosys. See below | `formal/` |
| Mutation | Each mutant breaks the RTL in one way, and the suite must fail. See below | `test/mutate.sh` |
| Gate level | The same cocotb tests on the post-route netlist in CI, with fewer random runs | `gl_test` job |
| Timing | CI fails on negative setup or hold slack, or any violation, at any corner. The gds job alone passed a build 0.57 ns short | `timing` job, `flow/check_timing.py` |

### Protocols tested

UART (transmit, receive), SPI (controller, target modes 0 to 3), I2C (controller, target), JTAG, SWD, PS/2, WS2812,
1-Wire, CAN (transmit with arbitration, receive with clock recovery, CRC and ACK), low-speed USB (transmit, receive).
All run on the same netlist with only a program change. UART receive and SPI controller also run together.

### Formal results

| Case | Required | What it shows |
|---|---|---|
| `bridge_rise5_hold12`, `rise10_hold20`, `rise15_hold30` | Proved | With any device behaviour and any rise up to the limit, the bridge never drives both sides or drives during holdoff, and always releases within a bound |
| `fifo_safety` | Proved | For any push, pop and clear: the count stays in range, full and empty are exact, and words leave unchanged and in order |
| `holdoff_too_short`, `release_bound_too_tight` | Counterexample | Controls. The proofs above can fail |

The proofs use specific rise and holdoff pairs, not every value. Real buses of up to 50 cycles of rise are covered
by simulation only. The proofs cover logic, not pad thresholds or analog behaviour.

### Mutation results

Run in a clean copy of the repository. In the last full run, 41 of 43 mutants were caught. The 2 survivors were
real test gaps. They were fixed, as listed below, and rerunning them confirmed both are now caught, along with 2
mutants added since. A full rerun of all 45 on the rewritten engine (branch `exp/timing-rtl`) then caught every
one, 2 of them because they made a test run forever, which `mutate.sh` now counts as caught after 20 minutes.

## What the checks found

Real problems, and which check found them. This is the best evidence that the checks work.

| Problem | Found by | Fix |
|---|---|---|
| Reading N FIFO words consumed N+1. Earlier tests read until empty, and empty reads as 0, which hid it | Writing CAN receive, whose host reads word by word | A word now leaves when the host starts clocking it, with a directed test and a mutant |
| FIFO pointer stuck at X in gate-level simulation. Synthesis had produced `!(x \| !x)`, which is 0 on silicon | Gate-level tests, traced flop by flop | Asynchronous reset on the pointers |
| Stale simulation values from a function reading state behind its arguments | Directed RAND test | Explicit arguments, and the differential now compares internal state |
| Missed-tick `F` timing differed between RTL and model, because the spec didn't define it | Differential, seed 2010 | Spec defines it, and both follow |
| Capture post-trigger count wrong in both RTL and model. A differential can't see an error both share | Directed capture test | Fixed in both |
| Assembler moved labels when `ldi` needed two words, so jumps went wrong | Firmware tests | Fixed, with a guard that raises if labels move |
| Mutants survived for return addresses shared between contexts, and for capture repeat timing | Mutation testing | Directed test, and an exact gap check in place of a tolerance |
| **Setup timing fails at the slow corner** | Reading the post-route STA report. The precheck doesn't flag it | **Open.** See below |

## Known gaps

- **Timing.** At 50 MHz the last measured build fails setup by 6.08 ns at the slow corner (1.08 V, 125 °C), on 180
  paths, all from the program SRAM. It passes at typical (+3.73 ns) and fast (+8.57 ns). Hold passes everywhere.
- **Slew, cap and fanout.** That build reports 19, 26 and 85 violations.
- **No silicon or pad data.** CMOS5L has neither yet, so pad delays and thresholds are unknowns in everything above.
- **No board run.** The host CLI and RP2350 agent have only run against RTL replay and stand-ins.
- **The model doesn't run protocol firmware.** The differential uses random programs, and protocol programs run on
  RTL only.
- **Not formally proved:** instruction cycle counts and pass-through identity. Both have simulation evidence only.
- **Local gate-level runs need patched cell models**, because Icarus 12 doesn't drive `$setuphold` delayed nets. CI
  uses Icarus 13 with the standard models, and CI's result is the one that counts.

## Running everything

```sh
cd test && make && python -m cocotb_tools.check_results results.xml    # RTL suite, about 3 minutes
cd formal && ./run.sh                                                  # proofs, about 1 minute
cd model && python3 -m tests.test_asm
cd host && python3 -m tests.test_host
cd test && bash mutate.sh                                              # all mutants, slow. Use a clean copy
```
