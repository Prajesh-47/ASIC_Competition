# ISA lab

Test bench for comparing ISA candidates. Results and the recommendation are in
[`docs/isa/exploration.md`](../../docs/isa/exploration.md). This is lab code: it isn't the golden model,
and `area/` isn't the engine.

| File | Purpose |
|---|---|
| `sim.py` | Cycle-exact chip model. One simulator runs the union of all candidate instructions |
| `isa_enc.py` | 16-bit encoding tables. Firmware that doesn't fit is rejected |
| `firmware.py` | Each workload written for each candidate |
| `checkers.py` | Protocol decoders that read only pin traces |
| `lab.py` | Runs everything and writes the results sheet |
| `analysis.py` | Ideal out-of-order limit and executed instruction pairs |
| `controls.py` | Negative controls: broken traces and firmware must be rejected |
| `fastpass.py` | One-flop pass-through experiment |
| `area/` | Throwaway synthesis sketches. Needs a filtered CMOS5L liberty file, see `synth.sh` |

Variants: V0 spec, V1 PIO-style, V2 deadline timer, V3 Loom, V4 Loom + rules, V5 two contexts,
V6 two engines, V7 Loom + rules + matchers, V3T Loom with the tick source used.

```sh
cd lab/isa
python3 controls.py                          # 12 controls, under a minute
python3 analysis.py                          # under a minute
python3 fastpass.py                          # under a minute
python3 -u lab.py ../../docs/isa/results.csv # about 10 minutes
```
