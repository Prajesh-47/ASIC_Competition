# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
"""UART in, SPI out, through the mailbox, with no host. Usage, from lab/explore: python3 translate.py

Sends bytes back to back on the UART and decodes MOSI on SCK's rising edges. Reports the fastest UART bit time
that still relays every byte, and the latency from the UART stop bit's middle to the first SCK rise.
"""
from harness import firmware, run

BYTES = [0x00, 0xFF, 0xA5, 0x3C, 0x81, 0x7E, 0x55, 0xC3, 0x12, 0xED]


def uart_wave(data, bit, idle=50):
    levels = [1] * idle
    for b in data:
        levels += [0] * bit + [(b >> k) & 1 for k in range(8) for _ in range(bit)] + [1] * bit
    return levels + [1] * (40 * bit)


def relay(bit, half, program="fw/uart_to_spi.s", gap=0):
    prog = firmware(program, BIT=bit, HALF=half)
    wave = uart_wave(BYTES, bit)

    def stim(t, out, oe):
        return 0xFE | (wave[t] if t < len(wave) else 1)

    words, trace, _ = run(prog, len(wave), stim, two=True, relay=True, host_gap=gap)
    got, cur, prev, rises = [], [], 0, []
    for t, (out, oe, _) in enumerate(trace):
        sck = (out >> 4) & 1
        if sck and not prev:
            cur.append((out >> 5) & 1)
            rises.append(t)
            if len(cur) == 8:
                got.append(sum(b << (7 - k) for k, b in enumerate(cur)))
                cur = []
        prev = sck
    last_sample = 50 + 8 * bit + bit // 2  # the middle of the first byte's data bit 7
    latency = rises[0] - last_sample if rises else None
    return got == BYTES, got, latency, len(prog.words), words


def fastest(half, program, gap):
    best = None
    for bit in range(400, 3, -1):
        ok, got, lat, n, host = relay(bit, half, program, gap)
        if not ok:
            if best is not None:
                break
            continue
        best = (bit, lat, n, host)
    return best


if __name__ == "__main__":
    # Baseline: the same two programs with no ROUTE, so the host must read each byte and write it back.
    # gap 0 is a perfect host; 366 cycles a word is what the RTL SD test's SPI host managed (95,273 / 260).
    cases = [("fw/uart_to_spi.s", 0), ("two_protocols.s", 0), ("two_protocols.s", 366)]
    for program, gap in cases:
        for half in (10, 2):
            best = fastest(half, program, gap)
            if best is None:
                print(f"{program}, host gap {gap}, SPI half {half}: never relays every byte")
                continue
            bit, lat, n, host = best
            print(f"{program}, host gap {gap}, SPI half {half}: every byte relayed down to a UART bit of {bit} "
                  f"cycles ({50 / bit:.2f} Mbaud); last data sample to first SCK rise {lat} cycles; {n} words; "
                  f"host read {len(host)} words")
