; SPI target, MSB first, CPHA 1 (modes 1 and 3): drive on odd edges, sample on even ones.
.equ SCK, 0
.equ CS, 1
.equ MOSI, 2
.equ MISO, 4
        msb     1
        mode    MISO, eng
top:    pullw   8
        wait    CS, lo
        tsrc    SCK, both
bit:    out.t   MISO ns
        inb.t   MOSI, bit
        push
        jmp     top
