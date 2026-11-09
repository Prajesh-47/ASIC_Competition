# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# Protocols beyond UART, SPI and I2C, on the real design: each program is loaded over SPI and judged
# by a device model or decoder written from that protocol's rules. Only top-level ports are used, so
# these also run on the gate-level netlist.

import os

import cocotb
from cocotb.triggers import ClockCycles

from test import HERE, assemble, capture, fifo_read, fifo_write, firmware, load_program, reset, spi_xfer, start, stop, write_reg  # noqa: F401


def level(out, oe, line, idle=1):
    return (out >> line) & 1 if (oe >> line) & 1 else idle


class Bus:
    """Open-drain line with a pull-up: falls at once, rises after `rise` cycles."""

    def __init__(self, rise=0):
        self.rise, self.level, self.rem = rise, 1, 0

    def step(self, pulled_low):
        if pulled_low:
            self.level, self.rem = 0, self.rise
        elif self.rem > 0:
            self.rem -= 1
        else:
            self.level = 1
        return self.level


TAP = {  # IEEE 1149.1: state -> (next if TMS=0, next if TMS=1)
    "TLR": ("RTI", "TLR"), "RTI": ("RTI", "SDS"), "SDS": ("CDR", "SIS"), "CDR": ("SDR", "E1D"),
    "SDR": ("SDR", "E1D"), "E1D": ("PDR", "UDR"), "PDR": ("PDR", "E2D"), "E2D": ("SDR", "UDR"),
    "UDR": ("RTI", "SDS"), "SIS": ("CIR", "TLR"), "CIR": ("SIR", "E1I"), "SIR": ("SIR", "E1I"),
    "E1I": ("PIR", "UIR"), "PIR": ("PIR", "E2I"), "E2I": ("SIR", "UIR"), "UIR": ("RTI", "SDS"),
}


@cocotb.test()
async def test_jtag_reads_idcode(dut):
    await reset(dut)
    half, idcode = 10, 0x4BA00477
    await load_program(dut, firmware("jtag_idcode.s", HALF=half).words)
    await start(dut)
    st = {"tap": "RTI", "ptck": 0, "sr": 0, "tdo": 1}

    def device(t, out, oe):
        tck, tms = level(out, oe, 4, 0), level(out, oe, 5)
        if tck and not st["ptck"]:
            if st["tap"] == "CDR":
                st["sr"] = idcode
            elif st["tap"] == "SDR":
                st["sr"] >>= 1
            st["tap"] = TAP[st["tap"]][tms]
        if st["ptck"] and not tck and st["tap"] == "SDR":
            st["tdo"] = st["sr"] & 1
        st["ptck"] = tck
        return 0x7F | (st["tdo"] << 7)

    await capture(dut, 2 * half * 50, device)
    words = await fifo_read(dut, 2)
    assert words[0] | words[1] << 16 == idcode, f"{[hex(w) for w in words]}"
    assert st["tap"] == "E1D", st["tap"]
    await stop(dut)


@cocotb.test()
async def test_swd_reads_dpidr(dut):
    await reset(dut)
    half, dpidr = 10, 0x0BB11477
    parity = bin(dpidr).count("1") % 2
    await load_program(dut, firmware("swd_read.s", HALF=half).words)
    await start(dut)
    word = 0b001 | (dpidr << 3) | (parity << 35)
    st = {"pclk": 0, "ones": 0, "phase": "reset", "req": [], "cnt": 0, "drive": None, "good": False}

    def target(t, out, oe):
        clk = level(out, oe, 4, 0)
        host = (out >> 5) & 1 if (oe >> 5) & 1 else None
        line = host if host is not None else (st["drive"] if st["drive"] is not None else 1)
        if clk and not st["pclk"]:
            if st["phase"] == "reset":
                st["ones"] = st["ones"] + 1 if line else 0
                if st["ones"] >= 50:
                    st["phase"] = "armed"
            elif st["phase"] == "armed" and line == 0:
                st["phase"] = "idle"
            elif st["phase"] == "idle" and line == 1:
                st["phase"], st["req"] = "req", [1]
            elif st["phase"] == "req":
                st["req"].append(line)
                if len(st["req"]) == 8:
                    r = st["req"]
                    st["good"] = r[0] == 1 and r[6] == 0 and r[7] == 1 and r[2] == 1 and (r[1] + r[2] + r[3] + r[4]) % 2 == r[5]
                    st["phase"] = "trn"
            elif st["phase"] == "trn":
                st["phase"], st["cnt"] = "send", 0
            elif st["phase"] == "send":
                if st["good"] and st["cnt"] < 36:
                    st["drive"] = (word >> st["cnt"]) & 1
                    st["cnt"] += 1
                else:
                    st["drive"] = None
        st["pclk"] = clk
        return 0xDF | (line << 5)

    await capture(dut, 2 * half * 112, target)
    assert st["good"], "the target rejected the request"
    got = await fifo_read(dut, 4)
    assert got[0] >> 13 == 0b001, f"ACK {got[0]:04x}"
    assert got[1] | got[2] << 16 == dpidr, f"{[hex(w) for w in got]}"
    assert got[3] >> 15 == parity
    await stop(dut)
    dut.uio_in.value = 0xFF


@cocotb.test()
async def test_ps2_device_clocked_receive(dut):
    await reset(dut)
    await load_program(dut, firmware("ps2_rx.s").words)
    await start(dut)
    bit = 40
    data = [0x1C, 0xF0, 0x5A]
    frames = []
    for byte in data:
        odd = 1 - bin(byte).count("1") % 2
        frames.append([0] + [(byte >> k) & 1 for k in range(8)] + [odd, 1])
    span = 14 * bit

    def device(t, out, oe):
        f, pos = t // span, t % span
        if f >= len(frames) or pos // bit >= 11:
            return 0xFF
        clock = 0 if pos % bit >= bit // 2 else 1
        return 0xFC | clock | (frames[f][pos // bit] << 1)

    await capture(dut, span * len(frames) + 20, device)
    got = []
    for word in await fifo_read(dut, 3):
        value = (word >> 5) & 0x7FF
        assert value & 1 == 0 and (value >> 10) & 1 == 1, f"bad framing {value:011b}"
        assert bin((value >> 1) & 0x1FF).count("1") % 2 == 1, "bad parity"
        got.append((value >> 1) & 0xFF)
    assert got == data, f"{got}"
    await stop(dut)
    dut.uio_in.value = 0xFF


async def spi_target_run(dut, mode, half=12):
    source = "spi_target_cpha1.s" if mode & 1 else "spi_target.s"
    await stop(dut)
    await load_program(dut, firmware(source).words)
    tx_t, tx_m = [0xA5, 0x3C], [0x96, 0x0F]
    await fifo_write(dut, [b << 8 for b in tx_t])
    await start(dut)
    cpol, cpha = (mode >> 1) & 1, mode & 1
    first, nbits = 60, 16
    got = {"v": 0, "n": 0}

    def master(t, out, oe):
        rel = t - first
        cs = 0 if -30 <= rel < nbits * 2 * half + 30 else 1
        edge = rel // half if rel >= 0 else -1
        active = 0 <= edge < nbits * 2
        phase = edge % 2 if active else 0
        sck = cpol ^ (1 if active and phase == 0 else 0)
        late = rel - 2  # a real master changes MOSI just after its clock edge
        ledge = late // half if late >= 0 else -1
        lbit = min(max(ledge, 0) // 2, nbits - 1)
        idx = lbit + 1 if cpha == 0 and ledge >= 0 and ledge % 2 == 1 else lbit
        idx = min(idx, nbits - 1)
        mosi = (tx_m[idx // 8] >> (7 - idx % 8)) & 1
        sample = active and rel % half == 0 and ((phase == 0) if cpha == 0 else (phase == 1))
        if sample and got["n"] < nbits:
            got["v"] = (got["v"] << 1) | level(out, oe, 4)
            got["n"] += 1
        return 0xF8 | sck | (cs << 1) | (mosi << 2)

    await capture(dut, first + nbits * 2 * half + 80, master)
    assert got["n"] == nbits and [got["v"] >> 8, got["v"] & 0xFF] == tx_t, f"mode {mode}: master read {got['v']:04x}"
    words = await fifo_read(dut, 2)
    assert [w & 0xFF for w in words] == tx_m, f"mode {mode}: target read {[hex(w) for w in words]}"
    await stop(dut)
    dut.uio_in.value = 0xFF


@cocotb.test()
async def test_spi_target_all_modes(dut):
    await reset(dut)
    for mode in range(4):
        await spi_target_run(dut, mode)


def i2c_timeline(bytes_):
    q = [(1, 1), (1, 1), (1, 0), (0, 0)]
    for byte in bytes_:
        for k in range(7, -1, -1):
            d = (byte >> k) & 1
            q += [(0, d), (1, d), (1, d), (0, d)]
        q += [(0, 1), (1, 1), (1, 1), (0, 1)]
    return q + [(0, 0), (1, 0), (1, 1), (1, 1), (1, 1)]


@cocotb.test()
async def test_i2c_target(dut):
    await reset(dut)
    await write_reg(dut, 1, 0x30)
    await load_program(dut, firmware("i2c_target.s").words)
    await start(dut)
    quarter = 31  # 400 kHz
    sequence = [0x50 << 1, 0xA5]
    plan = i2c_timeline(sequence)
    ack_at = [4 + 36 * b + 33 for b in range(len(sequence))]
    scl, sda = Bus(10), Bus(10)
    acks = []

    def controller(t, out, oe):
        i = t // quarter
        ds, dd = plan[i] if i < len(plan) else (1, 1)
        if t % quarter == quarter - 1 and i in ack_at:
            acks.append(sda.level)
        target_low = (oe >> 5) & 1 and not (out >> 5) & 1
        return 0xCF | (scl.step(ds == 0) << 4) | (sda.step(dd == 0 or target_low) << 5)

    await capture(dut, quarter * len(plan) + 40, controller)
    assert acks == [0, 0], f"{acks}"
    words = await fifo_read(dut, 1)
    assert words[0] & 0xFF == 0xA5, hex(words[0])
    await stop(dut)
    await write_reg(dut, 1, 0)
    dut.uio_in.value = 0xFF


@cocotb.test()
async def test_ws2812_pulse_widths(dut):
    """Written after the instruction list was fixed: classified by the datasheet's windows."""
    await reset(dut)
    await load_program(dut, firmware("ws2812.s").words)
    grb = [0x1F, 0xA0, 0x35, 0xC2]
    await fifo_write(dut, [grb[0] << 8 | grb[1], grb[2] << 8 | grb[3]])
    await start(dut)
    trace = await capture(dut, 62 * 40)
    levels = [level(o, e, 4, 0) for o, e, _ in trace]
    edges = [i for i in range(1, len(levels)) if levels[i] != levels[i - 1]]
    bits = []
    rises = [e for e in edges if levels[e] == 1]
    for r in rises:
        width = (next(e for e in edges if e > r) - r) * 20
        assert 250 <= width <= 550 or 650 <= width <= 950, f"pulse of {width} ns"
        bits.append(1 if width > 600 else 0)
    assert all(650 <= (b - a) * 20 <= 1850 for a, b in zip(rises, rises[1:])), "bit period out of range"
    assert [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, 32, 8)] == grb, f"{bits}"
    await stop(dut)


@cocotb.test()
async def test_onewire_read(dut):
    """Written after the instruction list was fixed, with a device timed from the 1-Wire rules."""
    await reset(dut)
    await write_reg(dut, 1, 0x10)
    await load_program(dut, firmware("onewire.s").words)
    await start(dut)
    us = 50
    line = Bus(10)
    dev = {"prev": 1, "low": 0, "state": "idle", "at": None, "hold": 0, "bits": [], "cmd": None, "sent": 0,
           "on": 0, "off": 0, "reset": False}
    reply = 0x5A

    def device(t, out, oe):
        lv = line.level
        if dev["prev"] == 1 and lv == 0:
            dev["low"] = 0
            if dev["state"] == "cmd":
                dev["at"] = t + 30 * us
            elif dev["state"] == "reply":
                bit = (reply >> dev["sent"]) & 1
                dev["sent"] += 1
                if not bit:
                    dev["hold"] = t + 30 * us
        if lv == 0:
            dev["low"] += 1
        if dev["prev"] == 0 and lv == 1 and dev["low"] >= 400 * us:
            dev.update(reset=True, state="pres", on=t + 15 * us, off=t + 75 * us)
        if dev["state"] == "pres" and t >= dev["off"]:
            dev["state"] = "cmd"
        if dev["state"] == "cmd" and dev["at"] == t:
            dev["bits"].append(lv)
            dev["at"] = None
            if len(dev["bits"]) == 8:
                dev["cmd"] = sum(b << i for i, b in enumerate(dev["bits"]))
                dev["state"] = "reply"
        dev["prev"] = lv
        pull = (dev["state"] == "pres" and dev["on"] <= t < dev["off"]) or t < dev["hold"]
        chip_low = (oe >> 4) & 1 and not (out >> 4) & 1
        return 0xEF | (line.step(chip_low or pull) << 4)

    await capture(dut, 2300 * us, device)
    assert dev["reset"], "no reset pulse"
    assert dev["cmd"] == 0xCC, f"{dev['cmd']}"
    assert (await fifo_read(dut, 1))[0] >> 8 == reply
    await stop(dut)
    await write_reg(dut, 1, 0)
    dut.uio_in.value = 0xFF


def can_frame_bits(cid=0x2A5, data=0xA7):
    bits = [0] + [(cid >> k) & 1 for k in range(10, -1, -1)] + [0, 0, 0, 0, 0, 0, 1]
    return bits + [(data >> k) & 1 for k in range(7, -1, -1)]


def can_crc(bits):
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


async def can_run(dut, compete_at=None, bit=100):
    await stop(dut)
    await load_program(dut, firmware("can_tx.s", BIT=bit).words)
    frame = can_frame_bits()
    await fifo_write(dut, [sum(b << (15 - i) for i, b in enumerate(frame[:16])),
                           sum(b << (15 - i) for i, b in enumerate(frame[16:]))])
    await start(dut)
    log, drove = [], []
    st = {"sof": None}

    def bus(t, out, oe):
        own = level(out, oe, 4)
        drove.append(own)
        if own == 0 and st["sof"] is None:
            st["sof"] = t
        other = 1
        if compete_at is not None and st["sof"] is not None and (t - st["sof"]) // bit == compete_at:
            other = 0
        value = own & other
        log.append(value)
        return 0xEF | (value << 4)

    await capture(dut, 80 * bit, bus)
    return frame, log, drove, st["sof"]


@cocotb.test()
async def test_can_frame_with_stuffing_and_crc(dut):
    await reset(dut)
    bit = 100
    frame, log, _, sof = await can_run(dut, bit=bit)
    assert sof is not None
    t, out, run_val, run_len = sof + bit // 2, [], None, 0
    while t < len(log) and len(out) < 42:
        v = log[t]
        if run_len == 5:
            assert v != run_val, "missing stuff bit"
            run_val, run_len = v, 1
        else:
            out.append(v)
            run_len = run_len + 1 if v == run_val else 1
            run_val = v
        t += bit
    assert out[:27] == frame, f"{out[:27]}"
    assert sum(b << (14 - i) for i, b in enumerate(out[27:42])) == can_crc(frame), "bad CRC"
    await stop(dut)
    dut.uio_in.value = 0xFF


@cocotb.test()
async def test_can_backs_off_when_arbitration_is_lost(dut):
    await reset(dut)
    bit = 100
    _, _, drove, sof = await can_run(dut, compete_at=2, bit=bit)
    assert sof is not None
    assert all(v == 1 for v in drove[sof + 3 * bit + 2:]), "kept driving after losing arbitration"
    assert (await fifo_read(dut, 1))[0] is not None
    await stop(dut)
    dut.uio_in.value = 0xFF


@cocotb.test()
async def test_usb_low_speed_packet(dut):
    await reset(dut)
    bit = 33
    await load_program(dut, firmware("usb_ls_tx.s", BIT=bit).words)
    sync, pid, data = 0x80, 0xC3, [0xFF, 0x3F]  # this data forces stuffed bits
    await fifo_write(dut, [sync | pid << 8, data[0] | data[1] << 8])
    await start(dut)
    trace = await capture(dut, 60 * bit)
    pair = [(level(o, e, 5, 1), level(o, e, 4, 0)) for o, e, _ in trace]  # (D-, D+)
    first = next(t for t in range(1, len(pair)) if pair[t] != pair[t - 1] and pair[t] == (0, 1))
    raw, prev, ones, t, se0, stuffed = [], (1, 0), 0, first + bit // 2, 0, 0
    while t < len(pair):
        now = pair[t]
        if now == (0, 0):
            se0 += 1
            if se0 >= 2:
                break
            t += bit
            continue
        value = 1 if now == prev else 0
        prev = now
        if ones == 6:
            assert value == 0, "stuffing error"
            ones = 0
            stuffed += 1
        else:
            raw.append(value)
            ones = ones + 1 if value else 0
        t += bit
    assert se0 >= 2, "no end of packet"
    assert stuffed >= 1, "this data should have forced a stuffed bit"
    assert len(raw) == 48, f"{len(raw)} bits"
    byte = lambda i: sum(raw[i * 8 + k] << k for k in range(8))
    assert [byte(0), byte(1), byte(2), byte(3)] == [sync, pid] + data
    crc = 0xFFFF  # the residual rule from the USB specification
    for b in raw[16:]:
        fb = b ^ (crc >> 15)
        crc = (crc << 1) & 0xFFFF
        if fb:
            crc ^= 0x8005
    assert crc == 0x800D, f"CRC residual {crc:04x}"
    await stop(dut)


# ------------------------------------------------------------------ Interposing on a live I2C bus
class Target:
    """Decodes what it receives, ACKs every byte, and can stretch SCL once after the first ACK."""

    def __init__(self, stretch=0):
        self.pscl, self.psda, self.bits, self.bytes, self.ack = 1, 1, [], [], False
        self.stretch, self.hold, self.started, self.stretched = stretch, 0, False, False

    def update(self, scl, sda):
        if scl and self.pscl and self.psda and not sda:
            self.started, self.bits, self.ack = True, [], False
        if self.started:
            if scl and not self.pscl and not self.ack:
                self.bits.append(sda)
                if len(self.bits) == 8:
                    self.bytes.append(int("".join(map(str, self.bits)), 2))
            if not scl and self.pscl:
                if self.ack:
                    self.ack, self.bits = False, []
                    if self.stretch and not self.stretched:
                        self.hold, self.stretched = self.stretch, True
                elif len(self.bits) == 8:
                    self.ack = True
        if self.hold:
            self.hold -= 1
        self.pscl, self.psda = scl, sda
        return (0 if self.hold else 1), (0 if self.ack else 1)


async def i2c_through_bridge(dut, source, quarter, rise, holdoff, stretch=0):
    """Controller on side A, target on side B, SPLICE between them. Returns what each one saw."""
    await stop(dut)
    await write_reg(dut, 3, holdoff)
    await load_program(dut, firmware(source).words)
    await start(dut)
    sent = [0xA4, 0x5C, 0x3A, 0x77]
    plan = i2c_timeline(sent)
    ack_at = [4 + 36 * b + 33 for b in range(len(sent))]
    a_scl, a_sda, b_scl, b_sda = Bus(rise), Bus(rise), Bus(rise), Bus(rise)
    target = Target(stretch)
    ctl = {"i": 0, "cnt": 0, "acks": [], "start": 40}

    def buses(t, out, oe):
        drive_scl, drive_sda = 1, 1
        if t >= ctl["start"] and ctl["i"] < len(plan):
            drive_scl, drive_sda = plan[ctl["i"]]
            if not (drive_scl == 1 and a_scl.level == 0):  # a held SCL stops the controller's clock
                ctl["cnt"] += 1
                if ctl["cnt"] >= quarter:
                    ctl["cnt"] = 0
                    if ctl["i"] in ack_at:
                        ctl["acks"].append(a_sda.level)
                    ctl["i"] += 1
        low = lambda x: (oe >> x) & 1 and not (out >> x) & 1
        t_scl, t_sda = target.update(b_scl.level, b_sda.level)
        la = a_scl.step(drive_scl == 0 or low(0))
        lb = a_sda.step(drive_sda == 0 or low(1))
        lc = b_scl.step(t_scl == 0 or low(4))
        ld = b_sda.step(t_sda == 0 or low(5))
        return 0xCC | la | (lb << 1) | (lc << 4) | (ld << 5)

    await capture(dut, 400 + (len(plan) + 8) * quarter + stretch + 20 * rise, buses)
    idle = (a_scl.level, a_sda.level, b_scl.level, b_sda.level)
    dut.uio_in.value = 0xFF
    await stop(dut)
    return sent, target.bytes, ctl["acks"], idle, ctl["i"] >= len(plan)


CASES = ((125, 50, 108, "100 kHz, 1000 ns rise"), (31, 15, 38, "400 kHz, 300 ns rise"))


@cocotb.test()
async def test_i2c_bridge_passes_traffic_both_ways(dut):
    """Bytes reach the target and ACKs reach the controller, with clock stretching too."""
    await reset(dut)
    for quarter, rise, holdoff, name in CASES:
        for stretch in (0, 400):
            sent, got, acks, idle, done = await i2c_through_bridge(dut, "i2c_bridge.s", quarter, rise, holdoff, stretch)
            assert done, f"{name}: controller stuck"
            assert got == sent, f"{name}: target saw {got}"
            assert acks == [0, 0, 0, 0], f"{name}: {acks}"
            assert idle == (1, 1, 1, 1), f"{name}: bus stuck low {idle}"


@cocotb.test()
async def test_i2c_force_nack_on_third_byte(dut):
    """The controller sees an error on the third byte. The target saw nothing unusual."""
    await reset(dut)
    for quarter, rise, holdoff, name in CASES:
        sent, got, acks, idle, done = await i2c_through_bridge(dut, "i2c_nack.s", quarter, rise, holdoff)
        assert done and got == sent, f"{name}: {got}"
        assert acks == [0, 0, 1, 0], f"{name}: {acks}"
        assert idle == (1, 1, 1, 1), f"{name}: {idle}"


@cocotb.test()
async def test_bridge_fails_with_too_short_a_holdoff(dut):
    """A negative control, and the reason holdoff is a register: a slow bus needs a long holdoff."""
    await reset(dut)
    sent, got, acks, idle, done = await i2c_through_bridge(dut, "i2c_bridge.s", 125, 50, 2)
    assert not (done and got == sent and acks == [0, 0, 0, 0] and idle == (1, 1, 1, 1)), "should have failed"
    await reset(dut)


def can_stuff(bits):
    """Bit stuffing as the CAN spec has it: after five equal bits, insert the opposite."""
    out, last, run = [], None, 0
    for b in bits:
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        if run == 5:
            last, run = 1 - b, 1
            out.append(last)
    return out


def can_dlc_frame(cid, data):
    bits = [0] + [(cid >> k) & 1 for k in range(10, -1, -1)] + [0, 0, 0]
    bits += [(len(data) >> k) & 1 for k in range(3, -1, -1)]
    return bits + [(byte >> k) & 1 for byte in data for k in range(7, -1, -1)]


def can_wire(frame, crc=None):
    """Bits on the wire up to and including the ACK slot and its delimiter, left recessive for the receiver."""
    crc = can_crc(frame) if crc is None else crc
    return can_stuff(frame + [(crc >> k) & 1 for k in range(14, -1, -1)]) + [1, 1, 1]


async def can_receive(dut, wires, bit=100, ppm=0, gap_bits=12, words_of=None):
    """Plays frames onto L4 with a sender clock off by ppm, reading the FIFO throughout as a host must,
    because it holds 4 words and long frames need more. Returns the ACK slots' levels and the words."""
    await stop(dut)
    await load_program(dut, words_of or firmware("can_rx.s", BIT=bit).words)
    await write_reg(dut, 1, 0x10)
    await start(dut)
    period = bit * (1_000_000 + ppm) / 1_000_000
    edges, t, acks = [], 15 * bit, []
    for wire in wires:
        for i, level_ in enumerate(wire):
            edges.append((round(t), level_))
            if i == len(wire) - 2:
                acks.append(round(t + period / 2))
            t += period
        edges.append((round(t), 1))
        t += gap_bits * period
    seen, i, now = {}, 0, 1
    watch = set(acks)

    def bus(t, out, oe):
        nonlocal i, now
        while i < len(edges) and edges[i][0] <= t:
            now = edges[i][1]
            i += 1
        value = now & level(out, oe, 4)
        if t in watch:
            seen[t] = value
        return 0xEF | (value << 4)

    words, done = [], []

    async def reader():
        # Finishes its transaction before stopping: cancelling mid-read would lose the popped word
        while True:
            empty = (await spi_xfer(dut, [0x12, 0]))[1] & 0x10
            if not empty:
                words.extend(await fifo_read(dut, 1))
            elif done:
                return

    task = cocotb.start_soon(reader())
    await capture(dut, round(t) + 2 * bit, bus)
    done.append(True)
    await task
    return seen, acks, words


def can_bits_of(words, count):
    return [(w >> (15 - i)) & 1 for w in words for i in range(16)][:count]


@cocotb.test()
async def test_can_receive_checks_crc_and_acks(dut):
    """Every data length, a run-heavy ID, and sender clocks off by the 1.58% CAN allows."""
    await reset(dut)
    for cid, data, ppm in ((0x2A5, [0xA7], 0), (0x7FF, [0x00, 0xFF, 0x00], 15800), (0x000, [0xFF] * 8, -15800),
                           (0x155, [], 15800), (0x123, list(range(0x10, 0x18)), -15800)):
        frame = can_dlc_frame(cid, data)
        seen, acks, words = await can_receive(dut, [can_wire(frame)], ppm=ppm)
        assert seen[acks[0]] == 0, f"no ACK for id {cid:#x}, clock {ppm} ppm"
        total = len(frame) + 15
        count = total // 16 + 1
        assert len(words) == count + 1, f"{len(words)} words for id {cid:#x}"
        sent = frame + [(can_crc(frame) >> k) & 1 for k in range(14, -1, -1)]
        assert can_bits_of(words[:count], total) == sent, f"wrong bits for id {cid:#x}, clock {ppm} ppm"
        assert words[count] == 0, f"status {words[count]} for a good frame"


@cocotb.test()
async def test_can_receive_rejects_bad_frames(dut):
    """A wrong CRC gets no ACK and status 1. Six equal bits are a stuff error, status 2."""
    await reset(dut)
    frame = can_dlc_frame(0x2A5, [0xA7])
    seen, acks, words = await can_receive(dut, [can_wire(frame, crc=can_crc(frame) ^ 1)])
    assert seen[acks[0]] == 1, "ACKed a frame with a bad CRC"
    count = (len(frame) + 15) // 16 + 1
    assert words[count] == 1

    wire = can_wire(frame)
    at = wire.index(0, 1)
    broken = wire[:at] + [0] * 6 + [1] * 20
    _, _, words = await can_receive(dut, [broken])
    assert words and words[-1] == 2, f"no stuff error reported: {[hex(w) for w in words]}"


@cocotb.test()
async def test_can_receive_back_to_back_frames(dut):
    """Two frames with the minimum gap: the receiver must be ready again in time."""
    await reset(dut)
    first, second = can_dlc_frame(0x111, [0x5A]), can_dlc_frame(0x222, [0xC3, 0x3C])
    seen, acks, words = await can_receive(dut, [can_wire(first), can_wire(second)], gap_bits=10)
    assert seen[acks[0]] == 0 and seen[acks[1]] == 0
    n1, n2 = (len(first) + 15) // 16 + 1, (len(second) + 15) // 16 + 1
    assert len(words) == n1 + n2 + 2
    assert words[n1] == 0 and words[n1 + 1 + n2] == 0
    assert can_bits_of(words[n1 + 1:n1 + 1 + n2], len(second)) == second


@cocotb.test()
async def test_can_receive_needs_its_clock_recovery(dut):
    """Control: the same program without the resync must fail on a slow sender. It proves the others mean something."""
    await reset(dut)
    with open(os.path.join(HERE, "..", "model", "firmware", "can_rx.s"), encoding="utf-8") as f:
        source = f.read()
    marker = "        jmp     f, done\n        tphase  half\n"
    assert marker in source
    frame = can_dlc_frame(0x123, list(range(0x10, 0x18)))
    seen, acks, _ = await can_receive(dut, [can_wire(frame)], ppm=-15800,
                                      words_of=assemble(source.replace(marker, ""), {"BIT": 100}).words)
    assert seen[acks[0]] == 1, "still ACKed with no clock recovery, so the test can't tell the difference"


def usb_wire(payload):
    """(D+, D-) per bit time for SYNC, payload bytes and EOP: LSB first, stuffed after six ones, NRZI, low speed."""
    bits = [0, 0, 0, 0, 0, 0, 0, 1] + [(byte >> k) & 1 for byte in payload for k in range(8)]
    stuffed, run = [], 0
    for b in bits:
        stuffed.append(b)
        run = run + 1 if b else 0
        if run == 6:
            stuffed.append(0)
            run = 0
    wire, j = [], True
    for b in stuffed:
        if not b:
            j = not j
        wire.append((0, 1) if j else (1, 0))
    return wire + [(0, 0), (0, 0), (0, 1)], bits


def usb_unpack(words):
    """Bits from the FIFO words: every word is 16 bits, oldest lowest, except the last, which ends in its marker."""
    bits = [(w >> i) & 1 for w in words[:-1] for i in range(16)]
    last = words[-1]
    assert last, "no marker in the last word"
    low = (last & -last).bit_length() - 1
    return bits + [(last >> i) & 1 for i in range(low + 1, 16)]


async def usb_receive(dut, payloads, bit=100 / 3, ppm=0):
    await stop(dut)
    await load_program(dut, firmware("usb_ls_rx.s").words)
    await start(dut)
    period = bit * (1_000_000 + ppm) / 1_000_000
    results = []
    await ClockCycles(dut.clk, 40)
    for payload in payloads:
        wire, bits = usb_wire(payload)
        edges = [(round(40 + i * period), lv) for i, lv in enumerate(wire)]
        words, done = [], []

        async def reader():
            while True:
                empty = (await spi_xfer(dut, [0x12, 0]))[1] & 0x10
                if not empty:
                    words.extend(await fifo_read(dut, 1))
                elif done:
                    return

        now, i = (0, 1), 0

        def bus(t, out, oe):
            nonlocal now, i
            while i < len(edges) and edges[i][0] <= t:
                now = edges[i][1]
                i += 1
            return 0xCF | now[0] << 4 | now[1] << 5

        task = cocotb.start_soon(reader())
        await capture(dut, edges[-1][0] + 200, bus)
        assert int(str(dut.uo_out.value)[-2]) == 1, "IRQ should say the packet is over"
        done.append(True)
        await task
        results.append((usb_unpack(words), bits))
    return results


@cocotb.test()
async def test_usb_ls_receive(dut):
    """NRZI and destuffing, with long runs of ones that force stuff bits, and clocks off by the 1.5% USB allows."""
    await reset(dut)
    packets = [[0xC3, 0x12, 0x34], [0x4B] + [0xFF] * 8 + [0x00, 0x00], [0xD2], [0x4B, 0xFE, 0x7F, 0xFF, 0x01]]
    for ppm in (0, 15000, -15000):
        for got, sent in await usb_receive(dut, packets, ppm=ppm):
            assert got == sent, f"at {ppm} ppm: sent {sent}, got {got}"


@cocotb.test()
async def test_two_protocols_at_once(dut):
    """UART receive and SPI controller from one program, both running, each judged by its own device model."""
    await reset(dut)
    await two_protocols(dut, firmware("two_protocols.s", BIT=40, HALF=10))


@cocotb.test()
async def test_program_above_word_256(dut):
    """The same two protocols with all their code from word 300, which a 256-word program memory can't
    hold: word 0 jumps there, and context 1 starts there, so both contexts fetch only above word 256."""
    await reset(dut)
    with open(os.path.join(HERE, "..", "model", "firmware", "two_protocols.s"), encoding="utf-8") as f:
        source = f.read()
    pad = "        jmp     high\n" + "        nop\n" * 299 + "high:\n"
    prog = assemble(pad + source, {"BIT": 40, "HALF": 10})
    assert prog.labels["top"] >= 300 and prog.ctx1 >= 300 and len(prog.words) > 310, \
        f"{len(prog.words)} words, top {prog.labels['top']}, ctx1 {prog.ctx1}"
    await two_protocols(dut, prog)


async def two_protocols(dut, prog):
    bit, half = 40, 10
    await load_program(dut, prog.words)
    await write_reg(dut, 0, 1)
    await write_reg(dut, 4, prog.ctx1 >> 8)
    await write_reg(dut, 5, prog.ctx1 & 0xFF)
    uart_sent, spi_sent = [0x55, 0xA3, 0x0F, 0xE1], [0x96, 0x3C, 0xFF, 0x01]
    await fifo_write(dut, [b << 8 for b in spi_sent])
    await start(dut)

    wave = [1] * (2 * bit)
    for byte in uart_sent:
        wave += sum(([b] * bit for b in [0] + [(byte >> k) & 1 for k in range(8)] + [1]), []) + [1] * 7
    st = {"psck": 0, "mosi": [], "rises": []}

    def bus(t, out, oe):
        sck = level(out, oe, 4, idle=0)
        if sck and not st["psck"]:
            st["mosi"].append(level(out, oe, 5))
            st["rises"].append(t)
        st["psck"] = sck
        rx = wave[t] if t < len(wave) else 1
        return 0xFE | rx

    await capture(dut, len(wave) + 2 * bit, bus)
    bytes_out = [sum(b << (7 - i) for i, b in enumerate(st["mosi"][j:j + 8])) for j in range(0, len(st["mosi"]), 8)]
    assert bytes_out == spi_sent, f"SPI sent {[hex(b) for b in bytes_out]}"
    words = await fifo_read(dut, len(uart_sent))
    assert [w >> 8 for w in words] == uart_sent, f"UART got {[hex(w) for w in words]}"

    # Half rate may delay a tick by one slot, and no more
    gaps = [b - a for a, b in zip(st["rises"], st["rises"][1:])]
    within_byte = [g for i, g in enumerate(gaps) if (i + 1) % 8]
    assert all(abs(g - 2 * half) <= 1 for g in within_byte), f"SCK period off: {sorted(set(within_byte))}"
    await stop(dut)
    await write_reg(dut, 0, 0)
