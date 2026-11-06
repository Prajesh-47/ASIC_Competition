# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""Firmware for protocols the chip wasn't designed around. All of it targets Loom (V3), or Loom with
line-coding hardware (V8). Each program is judged in flex.py by a device model or decoder."""
from firmware import P
from sim import ENG, F1, HIZ, ODB


# ---------------------------------------------------------------- JTAG: read a 32-bit IDCODE
TCK, TMS, TDI, TDO = 4, 5, 6, 7


def jtag_idcode(half):
    p = P("V3")
    p.i("SET", line=TCK, v=0)
    p.i("SET", line=TDI, v=0)
    for line in (TCK, TMS, TDI):
        p.i("MODE", line=line, src=ENG)
    p.i("SIDE", line=TCK)
    p.i("TSET", p=half)
    p.i("SRI", v=0x5F)  # TMS, LSB first: 5 ones to reset, then Idle, Select, Capture, Shift
    p.i("BCI", n=9)
    p.label("tms")
    p.i("OUT", tick=True, side=0, line=TMS)
    p.i("NOP", tick=True, side=1)
    p.i("JMP", c="BCN", to="tms")
    p.i("BCI", n=16)
    p.label("lo")
    p.i("NOP", tick=True, side=0)
    p.i("INB", tick=True, side=1, line=TDO, to="lo")
    p.i("PUSH")
    p.i("BCI", n=15)
    p.label("hi")
    p.i("NOP", tick=True, side=0)
    p.i("INB", tick=True, side=1, line=TDO, to="hi")
    p.i("SET", tick=True, side=0, line=TMS, v=1)  # last bit leaves Shift-DR
    p.i("IN", tick=True, side=1, line=TDO)
    p.i("PUSH")
    p.i("HALT")
    return p.done()


# ---------------------------------------------------------------- SWD: read DPIDR, with turnaround
SWCLK, SWDIO = 4, 5


def swd_read(half):
    p = P("V3")
    p.i("SET", line=SWCLK, v=0)
    p.i("SET", line=SWDIO, v=1)
    p.i("MODE", line=SWCLK, src=ENG)
    p.i("MODE", line=SWDIO, src=ENG)
    p.i("SIDE", line=SWCLK)
    p.i("TSET", p=half)
    p.i("LDI", r=0, v=52)
    p.label("rst")
    p.i("NOP", tick=True, side=0)
    p.i("NOP", tick=True, side=1)
    p.i("DJNZ", r=0, to="rst")
    p.i("SET", tick=True, side=0, line=SWDIO, v=0)  # idle low frames the request's start bit
    p.i("NOP", tick=True, side=1)
    p.i("NOP", tick=True, side=0)
    p.i("NOP", tick=True, side=1)
    p.i("SRI", v=0xA5)  # start, DP, read, A=0, parity, stop, park
    p.i("BCI", n=8)
    p.label("rq")
    p.i("OUT", tick=True, side=0, line=SWDIO)
    p.i("NOP", tick=True, side=1)
    p.i("JMP", c="BCN", to="rq")
    p.i("NOP", tick=True, side=0)
    p.i("MODE", line=SWDIO, src=HIZ)  # release the shared data line
    p.i("NOP", tick=True, side=1)
    p.i("NOP", tick=True, side=0)
    p.i("NOP", tick=True, side=1)
    p.i("BCI", n=3)
    p.label("ack")
    p.i("IN", tick=True, side=0, line=SWDIO)
    p.i("NOP", tick=True, side=1)
    p.i("JMP", c="BCN", to="ack")
    p.i("PUSH")  # ACK, in the top 3 bits of SR
    p.i("BCI", n=16)
    p.label("d0")
    p.i("IN", tick=True, side=0, line=SWDIO)
    p.i("NOP", tick=True, side=1)
    p.i("JMP", c="BCN", to="d0")
    p.i("PUSH")
    p.i("BCI", n=16)
    p.label("d1")
    p.i("IN", tick=True, side=0, line=SWDIO)
    p.i("NOP", tick=True, side=1)
    p.i("JMP", c="BCN", to="d1")
    p.i("PUSH")
    p.i("BCI", n=1)
    p.i("IN", tick=True, side=0, line=SWDIO)  # parity
    p.i("PUSH")
    p.i("NOP", tick=True, side=1)
    p.i("HALT")
    return p.done()


# ---------------------------------------------------------------- PS/2: device-clocked receive
def ps2_rx():
    p = P("V3")
    p.i("TSRC", line=0, want=0)  # the device owns the clock
    p.label("top")
    p.i("BCI", n=11)
    p.label("bit")
    p.i("INB", tick=True, line=1, to="bit")
    p.i("PUSH")
    p.i("JMP", c="AL", to="top")
    return p.done()


# ---------------------------------------------------------------- SPI target, any of the 4 modes
S_SCK, S_CS, S_MOSI, S_MISO = 0, 1, 2, 4


def spi_target(mode):
    cpol, cpha = (mode >> 1) & 1, mode & 1
    p = P("V3")
    p.i("MSB", v=1)
    p.i("MODE", line=S_MISO, src=ENG)
    p.i("TSRC", line=S_SCK, want=None)  # every edge is a tick
    p.label("top")
    p.i("PULLW", n=8)
    p.i("WAIT", line=S_CS, c="LO")
    p.i("TSRC", line=S_SCK, want=None)  # forget edges from before the transfer
    if cpha == 0:  # first data bit is out before the first edge; sample on odd edges
        p.i("OUT", line=S_MISO, ns=1)
        p.label("bit")
        p.i("IN", tick=True, line=S_MOSI)
        p.i("OUT", tick=True, line=S_MISO, ns=1)
        p.i("JMP", c="BCN", to="bit")
    else:  # drive on odd edges, sample on even ones
        p.label("bit")
        p.i("OUT", tick=True, line=S_MISO, ns=1)
        p.i("INB", tick=True, line=S_MOSI, to="bit")
    p.i("PUSH")
    p.i("JMP", c="AL", to="top")
    return p.done()


# ---------------------------------------------------------------- I2C target
T_SCL, T_SDA = 4, 5


def i2c_target(addr):
    p = P("V3")
    p.i("MSB", v=1)
    p.i("MODE", line=T_SDA, src=ENG)
    p.i("SET", line=T_SDA, v=1)
    p.i("LDI", r=1, v=0xFF)
    p.label("idle")
    p.i("WAIT", line=T_SDA, c="FALL")
    p.i("PROBE", line=T_SCL)
    p.i("JMP", c="NZ", to="idle")  # SDA fell while SCL was low: not a START
    p.i("TSRC", line=T_SCL, want=1)
    p.i("BCI", n=8)
    p.label("adr")
    p.i("INB", tick=True, line=T_SDA, to="adr")
    p.i("MOV", d=0, s="SR")
    p.i("AND", d=0, s=1)
    p.i("CMPI", a=0, v=addr << 1)
    p.i("JMP", c="NZ", to="idle")
    p.i("CALL", to="ack")
    p.i("BCI", n=8)
    p.label("dat")
    p.i("INB", tick=True, line=T_SDA, to="dat")
    p.i("PUSH")
    p.i("CALL", to="ack")
    p.i("JMP", c="AL", to="idle")
    p.label("ack")
    p.i("WAIT", line=T_SCL, c="FALL")
    p.i("SET", line=T_SDA, v=0)
    p.i("WAIT", line=T_SCL, c="RISE")
    p.i("WAIT", line=T_SCL, c="FALL")
    p.i("SET", line=T_SDA, v=1)
    p.i("TSRC", line=T_SCL, want=1)  # forget the ACK clock's own edge
    p.i("RET")
    return p.done()


# ---------------------------------------------------------------- I2C interposing: force a NACK
SCL_A, SDA_A, SCL_B, SDA_B = 0, 1, 4, 5


def i2c_nack(bit_index):
    """Bridge both lines. Hide the target's ACK on bit number `bit_index`, counted from START."""
    p = P("V3")
    for line in (SCL_A, SDA_A, SCL_B, SDA_B):
        p.i("MODE", line=line, src=ODB)
    p.i("LDI", r=0, v=bit_index)
    p.i("TSRC", line=SCL_A, want=0)  # falling SCL on the controller's side
    p.label("cnt")
    p.i("NOP", tick=True)
    p.i("DJNZ", r=0, to="cnt")
    p.i("MODE", line=SDA_A, src=F1)  # stop bridging SDA to the controller for one clock
    p.i("NOP", tick=True)
    p.i("MODE", line=SDA_A, src=ODB)
    p.i("HALT")
    return p.done()


# ---------------------------------------------------------------- Manchester transmit
def manchester_soft(half):
    """Software only: each bit takes two timed drives plus loop control."""
    p = P("V3")
    p.i("MODE", line=4, src=ENG)
    p.i("TSET", p=half)
    p.label("top")
    p.i("PULLW", n=16)
    p.label("bit")
    p.i("TSTB")
    p.i("OUT", line=15)
    p.i("JMP", c="Z", to="one")
    p.i("SET", tick=True, line=4, v=0)
    p.i("SET", tick=True, line=4, v=1)
    p.i("JMP", c="BCN", to="bit")
    p.i("JMP", c="AL", to="top")
    p.label("one")
    p.i("SET", tick=True, line=4, v=1)
    p.i("SET", tick=True, line=4, v=0)
    p.i("JMP", c="BCN", to="bit")
    p.i("JMP", c="AL", to="top")
    return p.done()


def manchester_hw(half):
    p = P("V8")
    p.i("MODE", line=4, src=ENG)
    p.i("TSET", p=half)
    p.i("CODE", manchester=True)
    p.label("top")
    p.i("PULLW", n=16)
    p.label("bit")
    p.i("OUTB", tick=True, line=4, to="bit")
    p.i("JMP", c="AL", to="top")
    return p.done()


# ---------------------------------------------------------------- USB low-speed data packet, transmit
DP, DM = 4, 5  # J at low speed is D- high


def usb_tx_soft(bit):
    p = P("V3")
    p.i("SET", line=DM, v=1)
    p.i("SET", line=DP, v=0)
    p.i("MODE", line=DM, src=ENG)
    p.i("MODE", line=DP, src=ENG)
    p.i("SIDE", line=DP)
    p.i("TSET", p=bit)
    p.ldi(1, 0xA001)
    p.i("LDI", r=2, v=0)
    p.i("LDI", r=3, v=1)  # R3: current D- level
    p.i("PULLW", n=16)
    p.i("CALL", to="send")  # SYNC and PID
    p.ldi(0, 0xFFFF)  # CRC covers only what comes next
    p.i("PULLW", n=16)
    p.i("CALL", to="send")
    p.ldi(1, 0xFFFF)
    p.i("XOR", d=0, s=1)
    p.i("MOV", d="SR", s=0)
    p.i("BCI", n=16)
    p.ldi(1, 0)  # no more CRC feedback
    p.i("CALL", to="send")
    p.i("SET", tick=True, line=DM, v=0, side=0)  # end of packet: SE0 for 2 bits, then J
    p.i("NOP", tick=True)
    p.i("SET", tick=True, line=DM, v=1, side=0)
    p.i("HALT")
    p.label("send")
    p.i("TSTB")
    p.i("OUT", line=15)
    p.i("JMP", c="Z", to="one")
    p.i("LDI", r=2, v=0)
    p.i("CMPI", a=3, v=0)
    p.i("JMP", c="Z", to="t1")
    p.i("LDI", r=3, v=0)
    p.i("SET", tick=True, line=DM, v=0, side=1)
    p.i("JMP", c="AL", to="c0")
    p.label("t1")
    p.i("LDI", r=3, v=1)
    p.i("SET", tick=True, line=DM, v=1, side=0)
    p.label("c0")
    p.i("SHR", r=0)
    p.i("JMP", c="NF", to="chk")
    p.i("XOR", d=0, s=1)
    p.i("JMP", c="AL", to="chk")
    p.label("one")
    p.i("ADD", r=2, v=1)
    p.i("NOP", tick=True)
    p.i("SHR", r=0)
    p.i("JMP", c="F", to="chk")
    p.i("XOR", d=0, s=1)
    p.label("chk")
    p.i("CMPI", a=2, v=6)
    p.i("JMP", c="NZ", to="nx")
    p.i("LDI", r=2, v=0)  # stuff a 0: one more toggle
    p.i("CMPI", a=3, v=0)
    p.i("JMP", c="Z", to="s1")
    p.i("LDI", r=3, v=0)
    p.i("SET", tick=True, line=DM, v=0, side=1)
    p.i("JMP", c="AL", to="nx")
    p.label("s1")
    p.i("LDI", r=3, v=1)
    p.i("SET", tick=True, line=DM, v=1, side=0)
    p.label("nx")
    p.i("JMP", c="BCN", to="send")
    p.i("RET")
    return p.done()


def usb_tx_hw(bit):
    p = P("V8")
    p.i("SET", line=DM, v=1)
    p.i("SET", line=DP, v=0)
    p.i("MODE", line=DM, src=ENG)
    p.i("MODE", line=DP, src=ENG)
    p.i("SIDE", line=DP)
    p.i("TSET", p=bit)
    p.i("CODE", nrzi=True, level=1, stuff_n=6, stuff_ones=True, pair_line=DP, crc_on=False)
    p.i("PULLW", n=16)
    p.label("a")
    p.i("OUTB", tick=True, line=DM, to="a")
    p.ldi(0, 0xFFFF)
    p.ldi(1, 0xA001)
    p.i("CODE2", crc=0xFFFF, poly=0xA001, crc_on=True)
    p.i("PULLW", n=16)
    p.label("b")
    p.i("OUTB", tick=True, line=DM, to="b")
    p.i("CRCSR", xor=0xFFFF)
    p.i("BCI", n=16)
    p.label("c")
    p.i("OUTB", tick=True, line=DM, to="c")
    p.i("CODE", nrzi=False, stuff_n=0, pair_line=None)
    p.i("SET", tick=True, line=DM, v=0, side=0)
    p.i("NOP", tick=True)
    p.i("SET", tick=True, line=DM, v=1, side=0)
    p.i("HALT")
    return p.done()


# ---------------------------------------------------------------- CAN data frame, wired-AND bus
CAN_TX, CAN_RX = 4, 4  # one pin: the line reads back what the bus resolves to


def can_tx_soft(bit):
    """Software CRC-15, bit stuffing and arbitration. One subroutine sends every field."""
    p = P("V3")
    p.i("MSB", v=1)
    p.i("SET", line=CAN_TX, v=1)
    p.i("MODE", line=CAN_TX, src=ENG)
    p.i("TSET", p=bit)
    p.ldi(1, 0x8B32)  # polynomial 0x4599, left aligned
    p.i("LDI", r=0, v=0)
    p.i("LDI", r=2, v=0)
    p.i("LDI", r=3, v=1)
    p.i("PULLW", n=16)
    p.i("CALL", to="send")
    p.i("PULLW", n=11)
    p.i("CALL", to="send")
    p.i("MOV", d="SR", s=0)
    p.i("BCI", n=15)
    p.i("CALL", to="send")
    p.i("SET", tick=True, line=CAN_TX, v=1)  # CRC delimiter, then ACK and EOF are recessive
    p.i("LDI", r=2, v=9)
    p.label("eof")
    p.i("NOP", tick=True)
    p.i("DJNZ", r=2, to="eof")
    p.i("HALT")
    p.label("lost")
    p.i("SET", line=CAN_TX, v=1)
    p.i("PUSH")  # tells the host arbitration was lost
    p.i("HALT")
    p.label("send")
    p.i("TSTB")
    p.i("OUT", tick=True, line=CAN_TX)
    p.i("JMP", c="Z", to="one")
    # A dominant 0 was sent
    p.i("CMPI", a=3, v=0)
    p.i("JMP", c="Z", to="s0")
    p.i("LDI", r=3, v=0)
    p.i("LDI", r=2, v=0)
    p.label("s0")
    p.i("ADD", r=2, v=1)
    p.i("SHL", r=0)
    p.i("JMP", c="NF", to="stf")
    p.i("XOR", d=0, s=1)
    p.i("JMP", c="AL", to="stf")
    p.label("one")
    # A recessive 1 was sent: check the bus at the sample point
    p.i("CMPI", a=3, v=1)
    p.i("JMP", c="Z", to="s1")
    p.i("LDI", r=3, v=1)
    p.i("LDI", r=2, v=0)
    p.label("s1")
    p.i("ADD", r=2, v=1)
    p.i("SHL", r=0)
    p.i("JMP", c="F", to="smp")
    p.i("XOR", d=0, s=1)
    p.label("smp")
    p.i("PROBE", line=CAN_RX)
    p.i("JMP", c="NZ", to="lost")
    p.label("stf")
    p.i("CMPI", a=2, v=5)
    p.i("JMP", c="NZ", to="nx")
    p.i("LDI", r=2, v=1)
    p.i("CMPI", a=3, v=0)
    p.i("JMP", c="Z", to="i1")
    p.i("LDI", r=3, v=0)
    p.i("SET", tick=True, line=CAN_TX, v=0)
    p.i("JMP", c="AL", to="nx")
    p.label("i1")
    p.i("LDI", r=3, v=1)
    p.i("SET", tick=True, line=CAN_TX, v=1)
    p.label("nx")
    p.i("JMP", c="BCN", to="send")
    p.i("RET")
    return p.done()


def can_tx_hw(bit):
    p = P("V8")
    p.i("MSB", v=1)
    p.i("SET", line=CAN_TX, v=1)
    p.i("MODE", line=CAN_TX, src=ENG)
    p.i("TSET", p=bit)
    p.ldi(1, 0x4599)
    p.ldi(2, bit * 3 // 4)
    p.i("CODE", stuff_n=5, stuff_ones=False, crc=0, poly=0x4599, crc_on=True, crc_top=14, arb_at=bit * 3 // 4)
    p.i("PULLW", n=16)
    p.label("a")
    p.i("OUTB", tick=True, line=CAN_TX, to="a")
    p.i("PULLW", n=11)
    p.label("b")
    p.i("OUTB", tick=True, line=CAN_TX, to="b")
    p.i("JMP", c="F", to="lost")
    p.i("CRCSR", xor=0, keep=False, shift=1)
    p.i("BCI", n=15)
    p.label("c")
    p.i("OUTB", tick=True, line=CAN_TX, to="c")
    p.i("CODE", stuff_n=0, crc_on=False, arb_at=0)
    p.i("SET", tick=True, line=CAN_TX, v=1)
    p.i("LDI", r=2, v=9)
    p.label("eof")
    p.i("NOP", tick=True)
    p.i("DJNZ", r=2, to="eof")
    p.label("lost")
    p.i("HALT")
    return p.done()
