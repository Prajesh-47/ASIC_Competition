# Protocol candidates for a future blind trial

*2026-11-09, from the scout survey (`scout.md`). None of these have been tried on SPLICE.*

> **This list must not be used as the trial list.** I work on SPLICE, so I picked these, which makes them
> designer-chosen, the weaker kind of evidence by the rules in `docs/adaptability.md`. Use it only as a pool, or
> as a check on coverage. The real trial list has to be **re-picked by someone independent** who hasn't seen the
> design, as the first trial's was. If they choose some of these, fine, but the choice must be theirs.

Already tried, so excluded: UART, SPI (all modes), I2C (including the bridge), JTAG, SWD, PS/2, WS2812,
1-Wire, CAN, USB low speed, generic Manchester, HDLC, DALI, S/PDIF, SD 4-bit, 100BASE-TX, MDIO, LIN, SENT and
HDMI-CEC.

| # | Protocol | Why it tests flexibility | Main difficulty for SPLICE | Source |
|---:|---|---|---|---|
| 1 | **Bidirectional DShot (ESC telemetry)** | Pulse-width coding, an **inverted idle** that means "reply please", and a **half-duplex turnaround on one wire** about 30 µs later. The reply is **GCR-coded** at 5/4 of the command rate | Timing recovery at an unusual ratio, and a turnaround. DShot600 has a 1.67 µs bit | [Betaflight](https://betaflight.com/docs/wiki/guides/current/Dshot), [BDShot write-up](https://symonb.github.io/docs/drone/ESC/ESC_prot_impl_2_2), [PX4](https://docs.px4.io/v1.18/en/peripherals/dshot) |
| 2 | **MIL-STD-1553B** | Manchester II at 1 Mbit/s with a **3-bit-time sync that is deliberately invalid Manchester**, 16 data bits and parity. Command/response turnaround on a shared bus | Non-Manchester sync detection, a fast response window, and a two-wire bipolar drive | [RF Cafe](https://www.rfcafe.com/references/electrical/ew-radar-handbook/mil-std-1553-1773-data-bus.htm) |
| 3 | **ARINC 429** | **Three-level bipolar return-to-zero** on two wires, 100 or 12.5–14.5 kbit/s, 32-bit words with the label sent first in reversed order, and a 4-bit-time null gap | Three levels from two lines, mixed bit order, self-clocking RZ | [Abaco](https://www.abaco.com/news/what-are-some-arinc-429-electrical-characteristics/n2097), [1553couplers](https://www.1553couplers.com/about-arinc-429) |
| 4 | **Wiegand (26-bit)** | **Two data lines and no clock:** a 0 is a pulse on D0, a 1 a pulse on D1. 20–100 µs pulses, 200 µs–20 ms apart, with leading even and trailing odd parity | A multi-wire encoding where *which* line moves is the data. Wide timing tolerance | [Wikipedia](https://en.wikipedia.org/wiki/Wiegand_interface), [Telaeris](https://telaeris.com/down-and-dirty-with-wiegand) |
| 5 | **NEC infrared, transmit and receive** | Pulse-distance coding: a 562.5 µs mark, then a space of 562.5 µs for a 0 or 1.6875 ms for a 1. A 9 ms + 4.5 ms leader and a **repeat code**. Transmit needs a **38 kHz carrier** inside each mark | Carrier generation while timing the envelope, and long-interval decode | [SB-Projects](https://www.sbprojects.net/knowledge/ir/nec.php), [Altium](https://techdocs.altium.com/display/FPGA/NEC%20Infrared%20Transmission%20Protocol) |
| 6 | **NMRA DCC (model railway)** | A 1 is two 58 µs halves, a 0 two halves of at least 95 µs that may be **stretched** up to 12 ms in total, with a receiver window of 52–64 µs for a 1 half | Classifying half-bit durations, including stretched zeros | [NMRA S-9.1](https://nmra-ja.readthedocs.io/en/latest/S-9.1-electrical-standards.html) |
| 7 | **AVR UPDI (programming and debug)** | **Single-wire, half-duplex** UART frames (8E2). The target **auto-bauds on a 0x55 SYNCH before every instruction**. BREAK is 12 low bits and resets the link. A programmable **guard time** sits at every direction change | Auto-baud per instruction, direction changes on one wire, BREAK detection | [Microchip UPDI](https://onlinedocs.microchip.com/oxy/GUID-BD668D46-A7E4-40CA-8B1D-4EB24BAA33C3-en-US-4/GUID-87D1FF13-EAB7-4B0E-8791-8297DE6A98D6.html), [SYNCH](https://onlinedocs.microchip.com/pr/GUID-40910A7E-D42A-4D4C-B9C2-D8614E3BC666-en-US-3/GUID-9FB12263-83CB-462B-8D05-9A4AA73CE7BE.html), [Direction change](https://onlinedocs.microchip.com/pr/GUID-40910A7E-D42A-4D4C-B9C2-D8614E3BC666-en-US-3/GUID-6D7FAA03-83E7-497A-8D0A-5D76848B28BC.html) |
| 8 | **BiSS-C (encoder interface)** | The master clocks MA, and the encoder answers on SLO. The master must **measure the cable's round-trip delay every frame**, from the 2nd rising MA edge to the falling edge of the ACK bit, and compensate. Inverted CRC-6 | Measuring and correcting delay rather than recovering a clock: a new kind of timing problem | [TI TIDU794](https://ti.com/lit/pdf/tidu794), [Renishaw](https://renishaw.com/resourcecentre/en/download/resolute-fortis-biss-c--131420), [encoder.com](https://www.encoder.com/biss-encoders) |
| 9 | **Nintendo Joybus (N64/GameCube controller)** | Open-drain on one wire, 4 µs bit cells where **a 1 is a 1 µs low and a 0 a 3 µs low**. Different stop bits from console (3 µs) and controller (4 µs). The controller answers immediately | Fast half-duplex turnaround and pulse-width decode | [n64brew](https://n64brew.dev/wiki/Joybus_Protocol?oldid=4327), [consoleprotocols](https://www.sites.google.com/site/consoleprotocols/home/nintendo-joy-bus-documentation) |
| 10 | **USB Power Delivery on CC (BMC)** | **Biphase-mark** line code, a preamble for receiver sync, SOP ordered sets, **4b5b** payload, CRC-32 and EOP | Chains three codings (BMC, 4b5b, CRC-32). The bit rate is often quoted as 300 kbit/s, but no source read here confirmed it | [STM32 UCPD](https://stm32.dirba.io/rm0440-stm32g4/46-usb-type-c-usb-power-delivery-interface-ucpd/), [Wikipedia 4B5B](https://en.wikipedia.org/wiki/4B5B) |
| 11 | **Quad SPI flash, Fast Read Quad I/O (0xEB)** | Command on 1 line, address and mode on 4, then **dummy cycles where the host must release IO0–IO3** before the flash drives them | A multi-line **bus turnaround**, and a device-specific dummy count | [Intel](https://intel.com/content/www/us/en/docs/programmable/683271/current/understanding-dummy-clock-cycles-for.html), [Nordic DevZone](https://devzone.nordicsemi.com/f/nordic-q-a/115845/problem-with-qspi-s25fl064l-flash-memory-and-0xeb-command) |
| 12 | **I2S or TDM audio** | Continuous bit clock plus a word-select line, with data on a third line, so the engine must never pause the clock | Three lines changing on the same clock, and a clock that never stops | Not looked up in this survey. Use the Philips I2S specification |

**Coverage of the requested axes:**
- **Timing recovery:** 1, 2, 5, 6, 7, 9, 10.
- **Multi-wire:** 3, 4, 8, 11, 12.
- **Odd encodings:** 1 (GCR), 2 (invalid-Manchester sync), 3 (three-level RZ), 4 (line-select), 10 (BMC plus 4b5b).
- **Half-duplex turnarounds:** 1, 2, 7, 9, 11.
- **Expected out of reach**, useful as a known limit like 100BASE-TX: none for certain. Fast Joybus replies,
  DShot1200 and quad SPI above about 8 MHz are the likeliest to miss their rate.
