; Two protocols at once: UART receive on line 0 in context 0, SPI controller on lines 4 to 7 in context 1.
; They share the FIFOs by direction: the host writes bytes for SPI (byte << 8), and reads UART bytes in
; the top 8 bits of each word. SPI replies are dropped, because they'd mix with the UART bytes.
.equ BIT, 40
.equ HALF, 10
.equ RX, 0
.equ SCK, 4
.equ MOSI, 5
.equ CS, 6
        tset    BIT
top:    wait    RX, fall
        tphase  threehalf
        bci     8
rx:     inb.t   RX, rx
        push
        jmp     top

spi:    set     SCK, 0
        mode    SCK, eng
        mode    MOSI, eng
        set     CS, 0
        mode    CS, eng
        msb     1
        side    SCK
        tset    HALF
next:   pullw   8
tx:     out.t   MOSI ns side=0
        outb.t  MOSI, tx side=1
        jmp     next
.ctx1 spi
