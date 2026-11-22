# Roadmap to submission

*Written 2026-11-14, after a review of every decision so far against what the competition judges. It replaces
the week-by-week schedule that used to be in `architecture.md` section 11, which the project outran: that
schedule had the first RTL in late November, and by 14 November the whole chip was hardened with two blind
trials behind it.*

The deadline is **2027-01-18**. That leaves nine weeks.

## What the competition judges, and where SPLICE stands on each

| What they look at | Where SPLICE stands | Verdict |
|---|---|---|
| **Can it be reprogrammed for protocols it wasn't designed for?** (the main criterion) | Two blind trials, 13 protocols, no change to the chip in either. Trial 2 had nothing blocked and nothing slower than the protocol asks | Strong, with one weakness: trial 2's *pool* was chosen by the designer. See "Evidence" below |
| Which protocols are shown | 23 protocol families, all checked by device models written from each protocol's own rules | Strong |
| What's unique | Inline alteration of live traffic, the open-drain bridge with a proof, four contexts that feed each other | Strong. A scan of 22 public entries' READMEs found none describing inline alteration |
| How it was designed and verified | Golden model compared every cycle, 100+ mutants, formal proofs with negative controls, gate-level tests in CI, a log of what AI wrote and how each piece was checked | Strong |
| **The write-up** (weighted heavily) | The docs still describe the plan in places, and some pages said things that stopped being true weeks ago | **The weakest part.** Fixed in part on 2026-11-14; the full pass is scheduled below |
| A chip worth fabricating | All checks pass. With the macros mirrored and hold repaired to 0.3 ns after routing, slow-corner setup slack is 1.59 ns (from 0.27) and hold slack 0.30 ns (from 0.08), gds run 43732018972 | Good. Both are past their targets (1 ns and 0.15 ns) |

## Decisions reviewed

**Kept, because the evidence still supports them:**

- Adaptability before speed. The organisers say they aren't looking for the fastest chip.
- Interposing as the differentiator, with flexibility leading the pitch.
- v2's use of the 8x4 die: four contexts, links, lanes, tables, the line coder, the quad host link. Each
  one answered a gap a trial found, and each was measured before it was adopted.
- Two SRAM macros, our own power step, and original work over reuse.
- Blind trials as the main evidence, and mutation testing as the check on the tests.

**Changed on 2026-11-14:**

1. **The feature set freezes on 2026-11-29.** Trial 2 found nothing the chip couldn't do. The gaps left are
   about how long the firmware is, not about what's possible. Meanwhile each new feature has spent timing
   margin (slow-corner setup slack went 1.15, 0.27, 0.09 ns over the last three). One last round, v2.1, takes
   the top gaps from trial 2 (below), and then the RTL only changes to fix bugs.
2. **Hold margin comes before setup margin.** A chip that misses setup still works with a slower clock, which
   the demo board can supply. A chip that misses hold doesn't work at any clock. The flow now repairs hold to
   0.2 ns after routing, and the SRAM macros are mirrored so their pins face the logic, which is worth 1.3 ns
   of setup slack on its own.
3. **A stranger must be able to write firmware in minutes.** Judges may well try. `python -m splice.run` now
   runs any program on the golden model with no simulator installed, and the assembler reports the mistakes
   trial 2 tripped over. A tutorial that builds a new protocol from a blank file is scheduled.
4. **The third blind trial runs on the frozen chip, with protocols picked by someone who hasn't seen the
   pool.** See "Evidence".
5. **Docs must never claim more than is true today.** Stale status notes were corrected. The spec gets a full
   pass from "plan" to "reference" after the freeze.

**Considered and not done:**

- *Hardware match rules.* The scout measured them: 10.8-15.2K µm² for a gain that mostly came from something
  cheaper. They stay out.
- *More contexts or more engines.* Nothing in either trial needed more than two contexts at once.
- *Chasing clock rate.* 50 MHz stays.

## Evidence: what the third trial has to fix

Trial 1's protocols were picked by a different AI model that hadn't seen the design: strong evidence, but it
ran on v1. Trial 2 ran on v2, but drew at random from a pool the designer wrote, which is weaker by the
project's own rules. Trial 3 closes the gap:

- It runs on the frozen v2.1 chip, with no RTL, ISA or assembler change allowed.
- The protocols are named by someone else, with no access to the pool or the design: the author, a colleague,
  or a model given only the competition's public description.
- It records time to first working frame per protocol, since "how hard was it" is the question a judge has.

**The picks are already made, and sealed.** On 2026-11-15 a different model (Claude Opus), given only the
competition's public description and the list of protocols already done, and told not to open any project
file, wrote five protocols and three alternates to a file outside this repository. Nobody working on the chip
has read it. Its SHA-256 is `25aac0e1b4306804c6071d3123f2878f1ef48e2163ac6ee1cfd35837f0a9f201`. The file is
opened, and added to the repository unchanged, only after the freeze tag exists, so the last feature round
can't be tuned to it.

## v2.1: the last feature round

From trial 2's ranked gaps (`lab/explore/trial2.md`). Each is one branch, measured on the trial firmware it
would shorten, with its gds cost, and adopted only if it pays. The author gave the go-ahead to work through these on 2026-11-14.

| Gap | Hurt | Candidate | Silicon? |
|---|---:|---|---|
| No compare or subtract against a 16-bit constant: every range check is 6-8 words | 3 of 4 | **Built** on `exp/v21-cmpk-sub`: `SUB`, `CMPUI` (9-bit constant, one word) and `CMPK` (16-bit constant in the next word). NEC receive 158 to 139 words, +4.0% area, setup 1.68 ns. Awaiting merge | Yes |
| Four registers; `TO` borrowed as a fifth | 2 of 4 | More registers per context, or `TO` as a named general register when no wait is timed | Yes |
| `TCAP` saturates at 1.31 ms | 2 of 4 | A prescaler field on the capture counter | Yes |
| `CALL` is one level | 2 of 4 | A second return address per context | Yes |
| No shift of `SR` by n | 2 of 4 | A shift-by-n instruction | Yes |
| Timing constants change with the number of contexts | 2 of 4 | Assembler: a built-in `NCTX` name and `delay` that divides by it | No |
| Name clashes when two programs share an image | 2 of 4 | **Done:** errors for a second label or `.equ`; later, local names per file | No |
| Manchester and biphase receive | (found by the explorer) | **Adopted** from `explore/isa-code-rx`: +0.2% area | Yes |

## Schedule

| Week | Dates | Work | Done when |
|---|---|---|---|
| 1 | Nov 14-22 | Merge v2 to `main`. Adopt the mirrored macros, the hold margin and the coder's receive side. v2.1 branches for the gaps above | `main` is the chip the docs describe; setup slack at least 1 ns and hold at least 0.15 ns |
| 2 | Nov 23-29 | Finish and measure v2.1. Full mutant run and trial suites on the result. **Freeze** | Tag `v2.1-freeze`; every job green |
| 3 | Nov 30-Dec 6 | Blind trial 3 on the frozen chip. The tutorial, written while doing it | Trial 3 results in `adaptability.md` |
| 4 | Dec 7-13 | Spec pass: `architecture.md` becomes a reference for v2.1, with one ISA page a firmware writer can work from | No "planned" or "not yet" left that isn't true |
| 5 | Dec 14-20 | Demos: UART man-in-the-middle, I2C NACK injection, protocol bridge through a link, capture to PulseView. Each as a script anyone can run | Four scripts, each with a waveform in the repo |
| 6 | Dec 21-27 | Verification write-up and the AI-use write-up, from `verification.md` and `ai-log.md` | Both read cleanly start to finish |
| 7 | Dec 28-Jan 3 | Signoff: gate-level run of the trial suites, timing at all corners, a last look at every disabled flow check | A signoff table in the README |
| 8 | Jan 4-10 | Slack for whatever the above turned up. Optional video | |
| 9 | Jan 11-18 | Final read-through, submit | Submitted |

## Risks that remain

1. **No silicon evidence for the SRAM on CMOS5L.** The program lives in one. If the macro fails, so does the
   chip. Worth one measured experiment before the freeze: a small flop-based boot store (32-64 words) the
   host can select, so the chip still runs short programs with the macro dead. It must not add delay to the
   fetch path, or it doesn't go in.
2. **Nothing has run on hardware.** The FPGA workflow exists but has never been run to a board. A run on any
   iCE40UP5K board would turn "simulated" into "seen on a scope" for the write-up.
3. **Pad electricals are unknown** on CMOS5L, for every entrant.
4. **Solo entry.** The schedule above has one week of slack, and the freeze date is what protects it.
