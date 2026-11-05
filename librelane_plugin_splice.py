# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""LibreLane plugin for SPLICE. LibreLane imports modules named librelane_plugin_*."""
import json
import os
import re
import types

import librelane.steps.netgen as netgen
from librelane.steps import Step
from librelane.steps.odb import OdbpyStep

HERE = os.path.dirname(os.path.abspath(__file__))


@Step.factory.register()
class PowerColumns(OdbpyStep):
    id = "Splice.PowerColumns"
    name = "Turn SRAM Supply Columns Into Stripes"

    def get_script_path(self):
        return os.path.join(HERE, "flow", "splice_power_columns.py")


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
