; SPI target, MSB first, any mode. Every SCK edge is a tick, so polarity needs no code.
; CPHA 0: MODE=0 or 2. Use spi_target_cpha1.s for CPHA 1.
.equ SCK, 0
.equ CS, 1
.equ MOSI, 2
.equ MISO, 4
        msb     1
        mode    MISO, eng
top:    pullw   8
        wait    CS, lo
        tsrc    SCK, both     ; forget edges from before the transfer
        out     MISO ns       ; first bit is out before the first edge
bit:    in.t    MOSI
        out.t   MISO ns
        jmp     bcn, bit
        push
        jmp     top
