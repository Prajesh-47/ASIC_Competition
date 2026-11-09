# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Model-level trial harness for ISA exploration.

Runs firmware on the golden model (model/splice) against the trial's own device models and decoders, imported
unchanged from test/test_trial.py (cocotb is stubbed out, since only the plain-Python parts are used). RTL
doesn't have the candidate instructions, so baseline and candidate both run here, on the same harness.

The host is modelled two ways: gap 0 pushes and pops FIFO words the cycle they're possible, which measures
the engine alone; host_gap=N moves at most one word each way every N cycles, as a stand-in for the SPI link.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, os.path.join(ROOT, "model"))


def _stub_cocotb():
    if "cocotb" in sys.modules:
        return
    cocotb = types.ModuleType("cocotb")
    cocotb.test = lambda *a, **k: (lambda f: f)
    cocotb.start_soon = lambda *a, **k: None
    trig = types.ModuleType("cocotb.triggers")
    trig.Edge = trig.First = trig.Timer = trig.ClockCycles = trig.FallingEdge = object
    utils = types.ModuleType("cocotb.utils")
    utils.get_sim_time = lambda *a: 0
    clock = types.ModuleType("cocotb.clock")
    clock.Clock = object
    sys.modules.update({"cocotb": cocotb, "cocotb.triggers": trig, "cocotb.utils": utils, "cocotb.clock": clock})
    test = types.ModuleType("test")
    for name in ("firmware", "load_program", "reset", "start", "stop", "write_reg", "fifo_read", "fifo_write",
                 "spi_xfer"):
        setattr(test, name, None)
    sys.modules["test"] = test


_stub_cocotb()
sys.path.insert(0, os.path.join(ROOT, "test"))
import test_trial as trial  # noqa: E402

from splice.asm import assemble  # noqa: E402
from splice.model import Splice  # noqa: E402


def firmware(name, **defines):
    path = name if os.path.isabs(name) or os.path.exists(name) else os.path.join(ROOT, "model", "firmware", name)
    with open(path, encoding="utf-8") as f:
        return assemble(f.read(), defines)


def run(prog, cycles, stim=None, feed=(), host_gap=0, two=False, until=None, relay=False):
    """Run prog for `cycles`. stim(t, out, oe) gives uio_in, or (uio_in, ui_in). Returns (words, trace, model).

    relay=True makes the host send every word it reads straight back, the only way contexts can pass data
    without the mailbox."""
    m = Splice()
    m.load(prog.words)
    m.ctx1_pc = prog.ctx1
    m.two = two
    feed = list(feed)
    m.start()
    words, trace = [], []
    uio, ui = 0xFF, 0xFF
    last_in = last_out = -10**9
    for t in range(cycles):
        if feed and len(m.h2e) < m.FIFO_DEPTH and t - last_in >= host_gap:
            m.host_push(feed.pop(0))
            last_in = t
        if m.e2h and t - last_out >= host_gap:
            words.append(m.host_pop())
            last_out = t
            if relay:
                feed.append(words[-1])
        p = m.step(ui, uio)
        if stim:
            levels = stim(t, p["out"], p["oe"])
            if isinstance(levels, tuple):
                uio, ui = levels
            else:
                uio = levels
        trace.append((p["out"], p["oe"], p["uo"]))
        if until and until(t, words):
            break
    while m.e2h:
        words.append(m.host_pop())
    return words, trace, m
