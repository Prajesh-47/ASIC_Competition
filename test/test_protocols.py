# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# Protocols beyond UART, SPI and I2C, on the real design: each program is loaded over SPI and judged
# by a device model or decoder written from that protocol's rules. Only top-level ports are used, so
# these also run on the gate-level netlist.

import cocotb

from test import capture, fifo_read, fifo_write, firmware, load_program, reset, start, stop, write_reg  # noqa: F401


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
