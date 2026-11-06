# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Negative controls for flex.py: deliberately wrong firmware must fail, or the study means nothing."""
import copy

import flex as F
import flex_fw as ff

results = []


def control(name, passed):
    results.append((name, passed))
    print(("PASS " if passed else "FAIL ") + name)


def mutate(builder_name, edit):
    """Run with a builder whose output is changed by `edit`. Returns a restore function."""
    original = getattr(ff, builder_name)

    def patched(*a, **k):
        prog = copy.deepcopy(original(*a, **k))
        edit(prog)
        return prog
    setattr(ff, builder_name, patched)
    return lambda: setattr(ff, builder_name, original)


def find(prog, op, **want):
    return next(i for i in prog if i.op == op and all(i.a.get(k) == v for k, v in want.items()))


# Each control first confirms the unmodified program passes, so a failure below means the mutation.
control("JTAG baseline passes", F.jtag_case(25)[1])
undo = mutate("jtag_idcode", lambda p: find(p, "SRI").a.update(v=0x1F))
control("JTAG rejects a wrong TMS path", not F.jtag_case(25)[1])
undo()

control("SWD baseline passes", F.swd_case(25)[1])
undo = mutate("swd_read", lambda p: find(p, "SRI").a.update(v=0x85))
control("SWD target ignores a bad request parity", not F.swd_case(25)[1])
undo()
undo = mutate("swd_read", lambda p: find(p, "MODE", src=ff.HIZ).a.update(src=ff.ENG))
control("SWD fails if the host never releases the line", not F.swd_case(25)[1])
undo()

control("PS/2 baseline passes", F.ps2_case(4000)[1])
undo = mutate("ps2_rx", lambda p: find(p, "TSRC").a.update(want=1))
control("PS/2 rejects sampling on the wrong edge", not F.ps2_case(4000)[1])
undo()

for mode in range(4):
    control(f"SPI mode {mode} baseline passes", F.spi_target_case(mode, 25)[1])
original_spi = ff.spi_target
ff.spi_target = lambda m: original_spi(m ^ 1)
for mode in range(4):
    control(f"SPI mode {mode} rejects the wrong clock phase firmware", not F.spi_target_case(mode, 25)[1])
ff.spi_target = original_spi

control("I2C target baseline passes", F.i2c_target_case(125, 40)[1])
original_t = ff.i2c_target
ff.i2c_target = lambda addr: original_t(0x51)
control("I2C target ignores another address", not F.i2c_target_case(125, 40)[1])
ff.i2c_target = original_t


def drop_set0(prog):
    for ins in prog:
        if ins.op == "SET" and ins.a.get("v") == 0 and ins.a.get("line") == ff.T_SDA:
            ins.a["v"] = 1


undo = mutate("i2c_target", drop_set0)
control("I2C controller sees no ACK if the target never pulls SDA", not F.i2c_target_case(125, 40)[1])
undo()

control("USB baseline passes", F.usb_case("soft", 33)[1])
undo = mutate("usb_tx_soft", lambda p: find(p, "CMPI", a=2, v=6).a.update(v=7))
control("USB rejects stuffing after 7 ones", not F.usb_case("soft", 33)[1])
undo()


def wrong_poly(prog):
    for ins in prog:
        if ins.op == "LDI" and ins.a.get("hi") == 0xA0:
            ins.a["hi"] = 0xB0
            return


undo = mutate("usb_tx_soft", wrong_poly)
control("USB rejects a wrong CRC polynomial", not F.usb_case("soft", 33)[1])
undo()
control("USB hardware baseline passes", F.usb_case("hw", 33)[1])
undo = mutate("usb_tx_hw", lambda p: find(p, "CODE2").a.update(poly=0xB001))
control("USB hardware rejects a wrong CRC polynomial", not F.usb_case("hw", 33)[1])
undo()

control("CAN baseline passes", F.can_case("soft", 100)[1])
undo = mutate("can_tx_soft", lambda p: find(p, "CMPI", a=2, v=5).a.update(v=6))
control("CAN rejects stuffing after 6 bits", not F.can_case("soft", 100)[1])
undo()


def can_poly(prog):
    for ins in prog:
        if ins.op == "LDI" and ins.a.get("hi") == 0x8B:
            ins.a["hi"] = 0x8A
            return


undo = mutate("can_tx_soft", can_poly)
control("CAN rejects a wrong CRC polynomial", not F.can_case("soft", 100)[1])
undo()
control("CAN arbitration baseline backs off", F.can_case("soft", 100, 2)[1])


def ignore_arb(prog):
    for ins in prog:
        if ins.op == "JMP" and ins.a.get("c") == "NZ" and prog[prog.index(ins) - 1].op == "PROBE":
            ins.op, ins.a = "NOP", {}
            return


undo = mutate("can_tx_soft", ignore_arb)
control("CAN fails if firmware ignores lost arbitration", not F.can_case("soft", 100, 2)[1])
undo()
undo = mutate("can_tx_hw", lambda p: find(p, "CODE", crc_on=True).a.update(arb_at=0))
control("CAN hardware fails with arbitration switched off", not F.can_case("hw", 100, 2)[1])
undo()

control("Manchester baseline passes", F.manchester_case("hw", 20)[1])
undo = mutate("manchester_hw", lambda p: find(p, "CODE").a.update(manchester=False))
control("Manchester rejects plain NRZ", not F.manchester_case("hw", 20)[1])
undo()

control("Bridge baseline passes", F.i2c_interpose_case(125, 50, 108)[1])
control("Bridge fails with no holdoff and a slow bus", not F.i2c_interpose_case(125, 50, 0)[1])
control("NACK baseline passes", F.i2c_interpose_case(125, 50, 108, nack_bit=27)[1])
control("NACK check rejects forcing the wrong byte", not F.i2c_interpose_case(125, 50, 108, nack_bit=18)[1])
undo = mutate("i2c_nack", lambda p: find(p, "MODE", src=ff.F1).a.update(src=ff.ODB))
control("NACK check rejects no forcing at all", not F.i2c_interpose_case(125, 50, 108, nack_bit=27)[1])
undo()

print(f"{sum(p for _, p in results)}/{len(results)} controls behaved correctly")
raise SystemExit(0 if all(p for _, p in results) else 1)
