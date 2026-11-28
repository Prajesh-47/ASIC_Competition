; Probe for scout-options.md: Manchester transmit through CODE man at HB cycles a half-bit, 16-bit words
; back to back from the host FIFO. 10BASE-T's data coding needs a 50 ns half-bit: HB=3 at 60 MHz, HB=2 at
; 40 MHz. Not a 10BASE-T transmitter: no preamble, no link pulses, no end of frame.
.equ L, 4
        msb     0
        set     L, 1
        mode    L, eng
        tset    HB
        code    man
        pullw   16
w:      outb.t  L, w
        pullw   16
        jmp     w
