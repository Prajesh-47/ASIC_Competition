# AI-assisted work log

Which RTL, tests and analysis were AI-written, and how each was independently checked
(`docs/architecture.md` section 9.8). Newest first. "Independent" means the check does not rely
on the AI's own reasoning: a different tool, a hand calculation, or a third-party result.

| Date | Item | Written by | Independent check | Status |
|---|---|---|---|---|
| 2026-11-05 | Area probe: Verilog, yosys script, results in architecture.md section 7.1. Synthesis only, not in `src/`, not committed | Claude Opus 5.5 | Flop, latch and ICG counts match the probe's structure (2048 RAM bits; regs 64; FIFOs 2x(64+6)=140). Latch RAM area hand-summed from cell counts and liberty areas. Same script on the stub vs CI run 42034615786 (yosys 0.66): 150 cells / 2,221 µm² vs 146 / 2,185 µm², within 3% | Probe RTL was never simulated, so its numbers are area estimates only. The stub has no RAM, so RAM mapping is not validated against the flow |
| 2026-11-05 | SRAM research: architecture.md section 7.2 | Claude Opus 5.5 | Read directly from the pinned PDK tree, macro LEF and liberty files, TT shuttle repos, and GitHub job results. Not reproduced by hardening a macro in this repo | Precedent only until we harden one ourselves |
| 2026-11-05 | Placeholder READMEs, this log, `docs/overview.md` | Claude Opus 5.5 | Not applicable: no behaviour | Done |
| Before 2026-11-05 | `src/tt_um_prajesh47_splice.v` stub, `test/`, `info.yaml` pinout | **Author to fill in** | `test` workflow green; `gds` result pending | Provenance not known to this session |

## Rules for new entries

- Every AI-written RTL module or test gets a row before it is pushed.
- AI-written tests do not count as an independent check of AI-written RTL unless the expected
  values come from somewhere else, such as the golden model, a third-party bus model or a formal
  property reviewed by the author.
- Record checks that found a bug, including what the bug was.
