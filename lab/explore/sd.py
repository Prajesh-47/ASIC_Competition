# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""SD trial on the golden model: the trial's SdCard and checks, for any SD firmware. Usage, from lab/explore:
    python3 sd.py [firmware ...] [--gap N]
"""
import argparse

from harness import firmware, run, trial

T = trial


def feed_of(script):
    feed = []
    for typ, cmd, then, data in script:
        feed += [typ, cmd[0] << 8 | cmd[1], cmd[2] << 8 | cmd[3], cmd[4] << 8]
        if then is not None:
            feed.append(then)
        feed += data
    return feed


def sd(program, card, script, cycles, gap):
    prog = firmware(program, HALF=20)
    words, _, _ = run(prog, cycles, card.step, feed_of(script), host_gap=gap)
    return words, prog


def clocks(card):
    periods = T.sd_clock_periods(card.clock_edges)
    return round(50 / min(periods), 2), card.clock_edges[-1] - card.clock_edges[0]


def data_phase(program):
    """Cycles per 16-bit word in the read and write data phases: the span of SD clocks under 40 cycles apart."""
    out = {}
    block = [(i * 13 + 5) & 0xFF for i in range(512)]
    tail = T.crc_nibbles(T.line_crcs(T.block_nibbles(block)))
    data = [block[i] << 8 | block[i + 1] for i in range(0, 512, 2)]
    data += [tail[i] << 12 | tail[i + 1] << 8 | tail[i + 2] << 4 | tail[i + 3] for i in range(0, 16, 4)]
    for name, script in (("read", [(1, T.cmd_bytes(17, 0), 1, [])]), ("write", [(1, T.cmd_bytes(24, 0), 2, data)])):
        card = T.SdCard()
        sd(program, card, script, 400_000, 0)
        e = card.clock_edges
        fast = [i for i, x in enumerate(T.sd_clock_periods(e)) if x < 40]
        out[name] = round((e[fast[-1] + 1] - e[fast[0]]) / 260, 2)
    return out


def check_all(program, gap):
    """The five SD trial tests, as checks on the model. Returns (passed, failed, notes)."""
    ok, bad, notes = [], [], {}

    def expect(name, cond, why=""):
        (ok if cond else bad).append(name if cond else f"{name}: {why}")

    card = T.SdCard()
    w, prog = sd(program, card, [(0, T.cmd_bytes(0), None, []), (1, T.cmd_bytes(8, 0x1AA), 0, []),
                                 (1, T.cmd_bytes(55), 0, []), (2, T.cmd_bytes(41, 0x40FF8000), 0, []),
                                 (3, T.cmd_bytes(2), 0, []), (1, T.cmd_bytes(3), 0, [])], 80_000, gap)
    notes["words"] = len(prog.words)
    expect("init", card.raw and card.raw[0] == [0x40, 0, 0, 0, 0, 0x95]
           and card.commands == ["0", "8", "55", "A41", "2", "3"]
           and len(w) == 26 and w[3] == w[7] == w[11] == w[21] == w[25] == 0xA000, [hex(x) for x in w])

    card = T.SdCard()
    w, _ = sd(program, card, [(1, T.cmd_bytes(17, 0), 1, [])], 400_000, gap)
    nib, tail = T.sd_block_from_words(w[4:264])
    good = len(w) > 264 and nib == T.block_nibbles(card.block) and tail == T.crc_nibbles(T.line_crcs(nib))
    expect("read", good and w[264] == 0xA500, f"len {len(w)} last {[hex(x) for x in w[262:266]]}")
    if good:
        notes["read MHz"], notes["read block cycles"] = clocks(card)

    card = T.SdCard()
    block = [(i * 13 + 5) & 0xFF for i in range(512)]
    nibbles = T.block_nibbles(block)
    tail = T.crc_nibbles(T.line_crcs(nibbles))
    data = [block[i] << 8 | block[i + 1] for i in range(0, 512, 2)]
    data += [tail[i] << 12 | tail[i + 1] << 8 | tail[i + 2] << 4 | tail[i + 3] for i in range(0, 16, 4)]
    w, _ = sd(program, card, [(1, T.cmd_bytes(24, 0), 2, data)], 400_000, gap)
    expect("write", card.written == block and card.write_status == 0b010 and 0xA502 in w, [hex(x) for x in w[-4:]])
    if card.written == block:
        notes["write MHz"], notes["write block cycles"] = clocks(card)

    card = T.SdCard()
    card.bad_crc7 = True
    w, _ = sd(program, card, [(1, T.cmd_bytes(8, 0x1AA), 0, [])], 20_000, gap)
    c1 = 0xC700 in w
    card = T.SdCard()
    card.silent = {8}
    w, _ = sd(program, card, [(1, T.cmd_bytes(8, 0x1AA), None, []), (1, T.cmd_bytes(55), 0, [])], 40_000, gap)
    c2 = bool(w) and w[0] == 0xEE00 and 0xA000 in w
    card = T.SdCard()
    card.flip_read = 100
    w, _ = sd(program, card, [(1, T.cmd_bytes(17, 0), 1, [])], 400_000, gap)
    nib, tail = T.sd_block_from_words(w[4:264])
    c3 = tail != T.crc_nibbles(T.line_crcs(nib))
    c4 = "trial/" in program or (len(w) > 264 and w[264] == 0xC101)
    expect("corruption", c1 and c2 and c3 and c4, f"crc7 {c1} silence {c2} flip {c3} named {c4}")

    card = T.SdCard()
    card.stuck_low = 2
    w, _ = sd(program, card, [(1, T.cmd_bytes(17, 0), 1, [])], 400_000, gap)
    nib, tail = T.sd_block_from_words(w[4:264])
    r1 = tail != T.crc_nibbles(T.line_crcs(nib)) or nib != T.block_nibbles(card.block)
    card = T.SdCard()
    card.stuck_low = 2
    blk = [0xFF] * 512
    tl = T.crc_nibbles(T.line_crcs(T.block_nibbles(blk)))
    data = [0xFFFF] * 256 + [tl[i] << 12 | tl[i + 1] << 8 | tl[i + 2] << 4 | tl[i + 3] for i in range(0, 16, 4)]
    w, _ = sd(program, card, [(1, T.cmd_bytes(24, 0), 2, data)], 400_000, gap)
    expect("dat fault", r1 and card.write_status == 0b101 and 0xA505 in w, f"read {r1} write {card.write_status}")
    return ok, bad, notes


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("programs", nargs="*", default=["trial/sd_host.s", "sd_host.s"])
    ap.add_argument("--gap", type=int, default=0, help="host moves one word each way every N cycles")
    ap.add_argument("--phase", action="store_true", help="only cycles per word in the data phases")
    a = ap.parse_args()
    for p in a.programs:
        if a.phase:
            print(p, "data phase, cycles per word:", data_phase(p))
            continue
        ok, bad, notes = check_all(p, a.gap)
        print(f"{p} (host gap {a.gap}): {len(ok)}/{len(ok) + len(bad)} pass  {notes}")
        for b in bad:
            print("   FAIL", b)
