# model/

The golden model and assembler for ISA v1 (`docs/architecture.md` section 5).

| Path | Contents |
|---|---|
| `splice/isa.py` | The instruction table, used by both tools |
| `splice/asm.py` | Assembler |
| `splice/model.py` | Cycle-exact model. Tests compare it with the RTL on every output pin, every cycle |
| `firmware/` | Example programs |
| `tests/` | Assembler and model unit tests |

```sh
cd model
python3 -m splice.asm firmware/uart_tx.s -l -o uart_tx.hex -D BIT=434
python3 -m tests.test_asm
```

Syntax is described at the top of `splice/asm.py`.
