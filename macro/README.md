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

`src/config.json` sets `ERROR_ON_PDN_VIOLATIONS` to 0 because the flow's power check runs inside
`GeneratePDN`, before the macro columns exist. A green run therefore does not show the power grid
is connected: read the later IR-drop step's report.
