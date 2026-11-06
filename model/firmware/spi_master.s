; SPI controller, mode 0, full duplex. Host pushes byte << 8; each reply is in the low 8 bits.
.equ HALF, 25                 ; 1 MHz at 50 MHz
.equ SCK, 4
.equ MOSI, 5
.equ CS, 6
.equ MISO, 7
        set     SCK, 0        ; set values first, or SCK glitches when the line is taken
        mode    SCK, eng
        mode    MOSI, eng
        set     CS, 1
        mode    CS, eng
        set     CS, 0
        msb     1
        side    SCK
        tset    HALF
top:    pullw   8
bit:    out.t   MOSI ns side=0
        inb.t   MISO, bit side=1
        push
        jmp     top
