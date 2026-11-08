#!/usr/bin/env python3
"""Fail if a build misses setup or hold timing at any corner.

The gds workflow goes green even when the flow reports setup violations, so this
reads the flow's metrics.csv (from the tt_submission artifact) and exits 1 on any
negative worst slack or nonzero violation count. It also exits 1 if the timing
metrics are missing, so a renamed file or metric can't pass silently.

Usage: check_timing.py path/to/metrics.csv
Writes a table to $GITHUB_STEP_SUMMARY when that is set.
"""

import csv
import os
import re
import sys

# timing__setup__ws, timing__hold_vio__count__corner:nom_slow_1p08V_125C, ...
METRIC = re.compile(r"^timing__(setup|hold)(__ws|_vio__count)(?:__corner:(.+))?$")


def main(path):
    rows = {}  # (check, corner) -> {"ws": float, "vio": int}
    with open(path, newline="") as f:
        for name, value, *_ in csv.reader(f):
            m = METRIC.match(name)
            if not m:
                continue
            check, kind, corner = m.group(1), m.group(2), m.group(3) or "all corners"
            entry = rows.setdefault((check, corner), {})
            if kind == "__ws":
                entry["ws"] = float(value)
            else:
                entry["vio"] = int(float(value))

    problems = []
    for check in ("setup", "hold"):
        if not any(c == check and "ws" in e for (c, _), e in rows.items()):
            problems.append(f"no {check} slack metric found in {path}")

    lines = ["| Check | Corner | Worst slack (ns) | Violations | Result |", "|---|---|---|---|---|"]
    for (check, corner), e in sorted(rows.items()):
        ws, vio = e.get("ws"), e.get("vio")
        bad = (ws is not None and ws < 0) or (vio is not None and vio > 0)
        if bad:
            problems.append(f"{check} at {corner}: worst slack {ws} ns, {vio} violations")
        ws_text = "n/a" if ws is None else f"{ws:+.3f}"
        vio_text = "n/a" if vio is None else str(vio)
        lines.append(f"| {check} | {corner} | {ws_text} | {vio_text} | {'FAIL' if bad else 'ok'} |")

    report = "\n".join(["## Timing", "", *lines, ""])
    if problems:
        report += "\n**Timing check failed:**\n\n" + "\n".join(f"- {p}" for p in problems) + "\n"
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(report)
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
