# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Drive the capture SRAM's write enable from a fixed buf_4, inserted after synthesis.

The route to cap_sram/A_WEN is long, and the router's estimate before detailed routing can't see the
macro end of it (EST-0026), so the resizer left the net on an o21ai_1 and it missed the pin's 0.595 ns
slew limit at the slow corner (gds 44918283875: 0.634 ns). Putting the buffer in the RTL fixed the slew
but changed what synthesis made of the rest of the chip, and the fetch path lost 2.25 ns (gds
45153091689). Inserting it here leaves synthesis exactly as on main. RSZ_DONT_TOUCH_LIST keeps the
resizer from shrinking or removing it.
"""
import click
import odb
from reader import click_odb

MACRO = "cap_sram"
PIN = "A_WEN"
MASTER = "sg13cmos5l_buf_4"
INST = "cap_wen_buf"
NET = "cap_wen_pin"


@click.command()
@click_odb
def wen_buffer(reader):
    block = reader.block
    macro = block.findInst(MACRO)
    if macro is None:
        raise click.ClickException(f"no instance {MACRO}")
    pin = macro.findITerm(PIN)
    if pin is None or pin.getNet() is None:
        raise click.ClickException(f"{MACRO}/{PIN} is missing or unconnected")
    master = reader.db.findMaster(MASTER)
    if master is None:
        raise click.ClickException(f"no cell {MASTER}")
    if block.findInst(INST) is not None or block.findNet(NET) is not None:
        raise click.ClickException(f"{INST} or {NET} already exists")

    source = pin.getNet()
    buf = odb.dbInst_create(block, master, INST)
    out = odb.dbNet_create(block, NET)
    out.setSigType("SIGNAL")
    pin.disconnect()
    buf.findITerm("A").connect(source)
    buf.findITerm("X").connect(out)
    pin.connect(out)
    print(f"{MASTER} {INST}: {source.getName()} -> {NET} -> {MACRO}/{PIN}")


if __name__ == "__main__":
    wen_buffer()
