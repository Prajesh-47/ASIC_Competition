# macro/

IHP SRAM macro views used by the SRAM harden experiment.

| Macro | Use | Size (µm) |
|---|---|---|
| `RM_IHPSG13_1P_256x16_c2_bm_bist` | Program memory | 236.8 × 118.78 |
| `RM_IHPSG13_1P_512x16_c2_bm_bist` | Capture memory | 236.8 × 191.34 |

## Where the files come from

Unmodified copies from [IHP-Open-PDK](https://github.com/IHP-GmbH/IHP-Open-PDK) (Apache-2.0) at
`2bbec755dc67ca3db0261c3d6163e15735d66710`, the revision `tt-gds-action@ihp-cmos5l` pins, under
`ihp-sg13g2/libs.ref/sg13g2_sram/`. `ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram` is a symlink to that
directory. `test/models/` holds the matching simulation models.

## How they are integrated

- **Power:** the project's only vertical power layer is Metal4, the same layer as the macro's power
  pins, so `flow/splice_power_columns.py` turns every supply column in the macro's LEF into a
  stripe. It runs as the `Splice.PowerColumns` step from `librelane_plugin_splice.py`.
- **Tie-offs:** `A_DLY` is tied high (the model halts simulation otherwise), `A_BM` is all ones, and
  the BIST inputs are tied low.

- **Floorplan rule:** rows stop 10 µm short of a macro, so the rows in the channel between the two
  macros must still reach a stripe of each supply. With the program macro at x=377.76, the capture
  macro at x=678.24 makes those rows end at x=668.24, past the VGND stripe at 666.98. Moving it left
  of this makes the PDN generator add a partial strap, and `flow/splice_power_columns.py` then stops
  with an error.

## What was learned from other designs

This code is our own, but other public entries showed what was needed, and those ideas are credited
here:

- [urish/ttihp-sram-test](https://github.com/urish/ttihp-sram-test) showed the macro entries in
  `src/config.json`, the `PDN_MACRO_CONNECTIONS` format, and the tie-offs.
- [MarcosAsh/protocol-emulator](https://github.com/MarcosAsh/protocol-emulator) and
  [kdp1965/ihp-um-janestreet-prism](https://github.com/kdp1965/ihp-um-janestreet-prism) showed that
  Metal4 stripes must be redrawn on the macro's columns, which odb calls do that, and the Netgen JSON
  bug that `librelane_plugin_splice.py` works around. An earlier experiment reused their script
  directly (branch `exp/two-sram-harden`, used as the baseline).

## Checks turned off

A green run doesn't show these checks passed, so each one is justified here. Evidence is from CI runs
42045714767 (baseline) and 42050382525 (`exp/own-power-strict`, with Magic DRC and the overlap error
back on).

| Setting | Why it's off | Evidence |
|---|---|---|
| `ERROR_ON_PDN_VIOLATIONS` = 0 | The flow's power check runs inside `GeneratePDN`, before the macro columns exist | The later IR-drop step reports all shapes connected on VPWR and VGND. Read that report: the final `design__power_grid_violation__count` metric is stale |
| `ERROR_ON_ILLEGAL_OVERLAPS` = 0 | Magic reports stripes crossing M4 obstructions in the macro's LEF | All are in one band on the VDD columns (macro y ≈ 39–45 µm). `flow/check_macro_columns.py` shows the real GDS has no conflicting Metal4 on any of the 40 columns of either macro |
| `RUN_MAGIC_DRC` = 0, KLayout DRC on | Magic flags the foundry macro's internals. The cause wasn't investigated further | All 87,223 errors are inside the macro footprints, 0 outside. KLayout DRC, which precheck also runs, reports 0 |

Bridging `VDD!` and `VDDARRAY!` with one stripe ties them to the same supply. Every design found so
far does this, but no IHP document confirming it was found. **Open:** LVS treats the macro as a black
box, so only the GDS check above covers shorts inside the macro's footprint.
