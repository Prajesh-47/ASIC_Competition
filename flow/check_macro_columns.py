#!/usr/bin/env python3
# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Check that a full-height power stripe on each macro supply column cannot short.

LVS treats the SRAM as a black box, and Magic reports stripes crossing the macro's
LEF obstructions as illegal overlaps. Neither says whether the real layout has
metal under a stripe. This reads the macro GDS: on each column in the LEF, all
Metal4 must stay within that column's width and carry that column's own supply.

Usage: check_macro_columns.py MACRO.lef MACRO.gds   (needs gdstk)
"""
import re
import sys

import gdstk

METAL4 = (50, 0)
NETS = {"VDD!": "VPWR", "VDDARRAY!": "VPWR", "VSS!": "VGND"}
TOLERANCE = 0.001


def lef_columns(path):
    """{(x0, x1): net} for each Metal4 power column in the LEF."""
    text = open(path, encoding="utf-8").read()
    columns = {}
    for pin, net in NETS.items():
        body = re.search(rf"PIN {re.escape(pin)}\s(.*?)END {re.escape(pin)}", text, re.S).group(1)
        for x0, _, x1, _ in re.findall(r"RECT ([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+)", body):
            key = (float(x0), float(x1))
            if columns.setdefault(key, net) != net:
                sys.exit(f"column {key} declared on both supplies")
    return columns


def check(lef, gds):
    columns = lef_columns(lef)
    top = gdstk.read_gds(gds).top_level()[0]
    layer, datatype = METAL4
    metal = gdstk.boolean(
        top.get_polygons(layer=layer, datatype=datatype), [], "or", precision=1e-3
    )
    labels = [
        (lab.origin, NETS[lab.text])
        for lab in top.get_labels(depth=None)
        if lab.layer == layer and lab.text in NETS
    ]
    errors = []
    for (x0, x1), net in sorted(columns.items()):
        on_column = [
            p for p in metal
            if p.bounding_box()[1][0] > x0 and p.bounding_box()[0][0] < x1
        ]
        if not on_column:
            errors.append(f"x={x0}: no metal on the column")
        for poly in on_column:
            (left, _), (right, _) = poly.bounding_box()
            if left < x0 - TOLERANCE or right > x1 + TOLERANCE:
                errors.append(f"x={x0}: metal spans {left:.3f}..{right:.3f}, wider than the column")
            for origin, label_net in labels:
                if label_net != net and poly.contain(origin):
                    errors.append(f"x={x0}: {net} column touches {label_net} metal")
    print(f"{top.name}: {len(columns)} columns, {len(metal)} Metal4 shapes, {len(errors)} errors")
    for error in errors:
        print(f"  {error}")
    return not errors


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(0 if check(sys.argv[1], sys.argv[2]) else 1)
