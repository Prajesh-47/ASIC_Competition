# macro/

IHP SRAM macro views used by the SRAM harden experiment (branch `exp/two-sram-harden`).

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

- **Power:** the project's only PDN layer is Metal4, the same layer as the macro's power pins, so
  `odb_sram_stripes.py` moves the tile stripes onto the macro's supply columns. It runs as the
  `Project.ExtendPowerStripes` step from `librelane_plugin_sram_pdn.py`.
- **Reused code:** that step, the plugin and `src/pdn_cfg.tcl` are Apache-2.0 and come from
  [MarcosAsh/protocol-emulator](https://github.com/MarcosAsh/protocol-emulator), with earlier credits
  in the file headers. The only change is that the script reads each macro's own LEF.
- **Tie-offs:** `A_DLY` is tied high (the model halts simulation otherwise), `A_BM` is all ones, and
  the BIST inputs are tied low.

## Checks turned off

`src/config.json` sets `ERROR_ON_PDN_VIOLATIONS` and `ERROR_ON_ILLEGAL_OVERLAPS` to 0 and replaces
Magic DRC with KLayout DRC, as the reused design does. A green run therefore does not show those
checks passed: review the flow's reports before relying on it.
