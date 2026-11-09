# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""LibreLane plugin for SPLICE. LibreLane imports modules named librelane_plugin_*."""
import glob
import json
import os
import re
import types

import librelane.steps.netgen as netgen
from librelane.steps import Step
from librelane.steps.odb import OdbpyStep
from librelane.steps.openroad import OpenROADStep

HERE = os.path.dirname(os.path.abspath(__file__))


@Step.factory.register()
class PowerColumns(OdbpyStep):
    id = "Splice.PowerColumns"
    name = "Turn SRAM Supply Columns Into Stripes"

    def get_script_path(self):
        return os.path.join(HERE, "flow", "splice_power_columns.py")


@Step.factory.register()
class CheckPowerGrid(OpenROADStep):
    """Recheck the power grid once the macro columns exist.

    OpenROAD.GeneratePDN sets design__power_grid_violation__count before
    Splice.PowerColumns runs, so its count only lists the macros' still-unpowered
    supply pins. Its parser also sums the length of each violation's text rather
    than counting violations (116 violations read as 8,670). This step replaces
    that metric with the number of violations on the finished grid.
    """

    id = "Splice.CheckPowerGrid"
    name = "Check Power Grid After Macro Columns"
    outputs = []

    def get_script_path(self):
        return os.path.join(HERE, "flow", "splice_check_power_grid.tcl")

    def run(self, state_in, **kwargs):
        views_updates, metrics_updates = super().run(state_in, **kwargs)
        reports = sorted(glob.glob(os.path.join(self.step_dir, "*-grid-errors.rpt")))
        if not reports:
            raise RuntimeError("Splice.CheckPowerGrid: no grid reports were written")
        total = 0
        for report in reports:
            net = os.path.basename(report).split("-", maxsplit=1)[0]
            with open(report, encoding="utf8") as f:
                count = sum(1 for line in f if line.startswith("violation type:"))
            metrics_updates[f"design__power_grid_violation__count__net:{net}"] = count
            total += count
        metrics_updates["design__power_grid_violation__count"] = total
        return views_updates, metrics_updates


# Netgen writes macro power pin names as "\VDD!", which is invalid JSON, and
# LibreLane's LVS step then crashes before the LVS result is checked. Double
# the backslash of any escape that JSON doesn't define. Matching whole escapes
# keeps a valid "\\" intact when a letter follows it.
ANY_ESCAPE = re.compile(r"\\(.)", re.S)


def fix_escape(match):
    return match.group(0) if match.group(1) in '"\\/bfnrtu' else "\\" + match.group(0)


def loads_tolerating_pin_names(text, **kwargs):
    return json.loads(ANY_ESCAPE.sub(fix_escape, text), **kwargs)


netgen.json = types.SimpleNamespace(
    loads=loads_tolerating_pin_names,
    load=json.load,
    dumps=json.dumps,
    dump=json.dump,
    JSONDecodeError=json.JSONDecodeError,
)
