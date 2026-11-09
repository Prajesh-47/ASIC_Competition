# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Exploration opcodes TCAP and CMPU (branch explore/scout-edge-timing). Not approved, not in RTL."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lab", "explore"))

from splice.asm import assemble  # noqa: E402
from splice.isa import decode  # noqa: E402
from splice.model import Splice  # noqa: E402

import spdif_edge  # noqa: E402


def test_assembles():
    assert decode(assemble("cmpu r1, sr").words[0]) == ("CMPU", {"a": 1, "b": 4})
    assert decode(assemble("tcap.t r3").words[0]) == ("TCAP", {"r": 3, "tick": 1})


def test_cmpu_is_unsigned():
    m = Splice()
    m.load(assemble("ldi r0, 0x8000\nldi r1, 5\ncmpu r1, r0\nhalt").words)
    m.start()
    for _ in range(12):
        m.step()
    assert m.ctx[0].F == 1 and m.ctx[0].Z == 0


def test_tcap_reads_edge_intervals_to_the_cycle():
    """Edges 37 then 11 cycles apart. The program reads the second interval late, after a delay."""
    m = Splice()
    m.load(assemble("tsrc 0, both\ntcap.t r0\ntcap.t r1\ndly 20\ntcap.t r2\nhalt").words)
    m.start()
    edges, level = {10, 47, 58}, 1
    for t in range(200):
        if t in edges:
            level ^= 1
        m.step(uio=0xFE | level)
    assert m.ctx[0].R[1:3] == [37, 11]


def test_spdif_one_binary_at_several_rates():
    for fs, off in ((48000, 0.0), (44100, 0.0), (32000, 0.05), (48000, -0.10)):
        assert spdif_edge.trial_one("tcap", fs, 1 + off)[0], (fs, off)


def test_trial_firmware_is_48k_only():
    """Negative control: the same harness must fail the trial firmware at 44.1 kHz and pass it at 48."""
    assert spdif_edge.trial_one("trial", 48000)[0]
    assert not spdif_edge.trial_one("trial", 44100)[0]
