# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Power the SRAM macros by turning their supply columns into Metal4 stripes.

Metal4 is the only vertical power layer a CMOS5L Tiny Tapeout project has, and
it is also the layer of the macro's power pins, so the macro cannot be strapped
from above. Stripes crossing a macro are removed, and every supply column the
macro declares in its LEF becomes a stripe of the same height as the rest of the
grid. A missing pin or via is an error, never a silent skip.
"""
import click
import odb
from reader import click_odb

LAYER = "Metal4"
SUPPLY_PINS = {"VPWR": ("VDD!", "VDDARRAY!"), "VGND": ("VSS!",)}
RAIL_VIAS = ("via1_2_2100_440", "via2_3_2100_440", "via3_4_2100_440")
# Remove stripes this close to a macro as well (um), for Metal4 spacing.
CLEARANCE_UM = 0.5


def is_supply_macro(inst):
    master = inst.getMaster()
    return master.isBlock() and master.findMTerm("VSS!") is not None


def on_layer(box):
    layer = box.getTechLayer()
    return layer is not None and layer.getName() == LAYER


def supply_columns(inst, pin_names):
    """Die-frame (x0, x1) of each Metal4 column on the given macro pins."""
    if inst.getOrient() != "R0":
        raise click.ClickException(f"{inst.getName()}: only R0 is supported")
    origin = inst.getBBox().xMin()
    columns = set()
    for name in pin_names:
        mterm = inst.getMaster().findMTerm(name)
        if mterm is None:
            raise click.ClickException(f"{inst.getName()}: no pin {name}")
        for mpin in mterm.getMPins():
            for box in mpin.getGeometry():
                if on_layer(box):
                    columns.add((origin + box.xMin(), origin + box.xMax()))
    if not columns:
        raise click.ClickException(f"{inst.getName()}: no {LAYER} on {pin_names}")
    return sorted(columns)


def rewire_net(block, tech, net_name, macros, keepouts):
    net = block.findNet(net_name)
    swire = net.getSWires()[0]
    bpin = net.getBTerms()[0].getBPins()[0]
    layer = tech.findLayer(LAYER)

    wires = list(swire.getWires())
    stripes = [b for b in wires if on_layer(b) and b.yMax() - b.yMin() > b.xMax() - b.xMin()]
    rails = [
        b for b in wires
        if b.getTechLayer() is not None and b.getTechLayer().getName() == "Metal1"
    ]
    ylo = min(b.yMin() for b in stripes)
    yhi = max(b.yMax() for b in stripes)

    def in_keepout(box):
        return any(box.xMax() > x0 and box.xMin() < x1 for x0, x1 in keepouts)

    removed = 0
    for box in wires:
        if box.getTechLayer() is None or on_layer(box):  # via stack or stripe
            if in_keepout(box):
                odb.dbSBox_destroy(box)
                removed += 1
    for box in list(bpin.getBoxes()):
        if on_layer(box) and in_keepout(box):
            odb.dbBox_destroy(box)

    vias = []
    for prefix in RAIL_VIAS:
        match = [v for v in block.getVias() if v.getName().startswith(prefix)]
        if not match:
            raise click.ClickException(f"no via master starting with {prefix}")
        vias.append(match[0])

    placed = 0
    for inst in macros:
        macro = inst.getBBox()
        for x0, x1 in supply_columns(inst, SUPPLY_PINS[net_name]):
            odb.dbSBox_create(swire, layer, x0, ylo, x1, yhi, "STRIPE")
            odb.dbBox_create(bpin, layer, x0, ylo, x1, yhi)
            placed += 1
            cx = (x0 + x1) // 2
            for rail in rails:
                over_macro = rail.yMax() > macro.yMin() and rail.yMin() < macro.yMax()
                if not over_macro and rail.xMin() <= cx <= rail.xMax():
                    ry = (rail.yMin() + rail.yMax()) // 2
                    for via in vias:
                        odb.dbSBox_create(swire, via, cx, ry, "STRIPE")
    print(f"[INFO] {net_name}: removed {removed} shapes, placed {placed} column stripes")


@click.command()
@click_odb
def power_columns(reader):
    block = reader.block
    macros = [i for i in block.getInsts() if is_supply_macro(i)]
    if not macros:
        raise click.ClickException("no SRAM macros found")
    margin = int(CLEARANCE_UM * block.getDefUnits())
    keepouts = [(i.getBBox().xMin() - margin, i.getBBox().xMax() + margin) for i in macros]
    for net_name in SUPPLY_PINS:
        rewire_net(block, reader.tech, net_name, macros, keepouts)


if __name__ == "__main__":
    power_columns()
