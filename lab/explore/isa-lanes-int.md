# LANES on the integration branch, and SD end to end with the XFER host

*Branch `explore/isa-lanes-int`, from `exp/integrate-1` (20e1f22) merged with `explore/isa-lanes-table` (797bbb1).
2026-11-09. Prototype only: nothing here is approved.*

**Question:** round 1 (lab/explore/isa.md) showed that LANES runs SD's data phase 3.1x faster in the engine,
but end to end an SD block read took the same 95,272 cycles as main because the trial's SPI host set the
pace. `exp/integrate-1` has the new host link (XFER, level-aware STATUS, 8-word FIFOs). Does LANES pay end
to end now?

**Answer: no.** With an XFER host at the fastest SPI clock the host interface allows (core/8), main's
`sd_host.s` and the LANES firmware take the same number of cycles for a whole CMD17 read and a whole CMD24
write. The link moves a 16-bit word every ~155 cycles at best. Main's engine needs 65 a word for a read
and LANES needs 21, so both wait on the link. To keep up with even main's read loop, the host would
need SCK ≥ core/3.4, and core/1.1 for LANES. Both are faster than the 2-flop synchronizers allow. Details
and numbers below.

RESULTS_PLACEHOLDER
