# formal/

Formal proofs, using Yosys's built-in SAT solver (temporal induction). `sby` isn't required.

```sh
cd formal
./run.sh    # a few minutes
```

Every case has a required result. A proof that should pass must close the induction, and a control
that should fail must produce a real counterexample. A tool error counts as neither.

| File | What's proved |
|---|---|
| `bridge_closed_loop.v` | The open-drain bridge, closed-loop with its synchronizers, output registers and both buses. Devices can do anything, and a bus may rise at any time up to a limit. It never drives both sides, never drives during holdoff, and once both devices let go it always releases and goes idle within a bound |

**What this doesn't cover:** real pad thresholds and analog rise behaviour. Proofs use specific rise and holdoff pairs, not every value, and real buses (up to 50 cycles of rise) are covered by simulation, not by proof.
