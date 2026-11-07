; MDIO controller (IEEE 802.3 clause 22): 32 preamble ones, then a 16-bit control word, a turnaround, 16 bits.
; Host pushes {ST, OP, PHYAD, REGAD, TA}, and for a write the data as well. FIFO gets, for a read, the data word
; and then a status: 0xA000 good, 0xEE00 no PHY answered the turnaround. A write just pushes 0xA000.
.equ HALF, 10                 ; 2.5 MHz, the most the standard allows
.equ MDC, 4
.equ MDIO, 5
        msb     1
        set     MDC, 0
        side    MDC
        mode    MDC, eng
        set     MDIO, 1
        mode    MDIO, eng
        tset    HALF
next:   pullw   16
        mov     r0, sr
        mode    MDIO, eng
        ldi     r1, 32
pre:    set.t   MDIO, 1 side=0
        nop.t   side=1
        djnz    r1, pre
        mov     sr, r0
        mov     r1, low
        ldi     r2, 0xF0
        and     r1, r2
        bci     14
b:      out.t   MDIO side=0
        nop.t   side=1
        jmp     bcn, b
        cmpi    r1, 0x60      ; ST 01, OP 10: a read
        jmp     z, read
        bci     2             ; write: TA is ours
ta:     out.t   MDIO side=0
        nop.t   side=1
        jmp     bcn, ta
        pullw   16
wd:     out.t   MDIO side=0
        nop.t   side=1
        jmp     bcn, wd
        sri     0xA0, 8
        push
        jmp     next
read:   mode.t  MDIO, hiz
        nop     side=0
        nop.t   side=1
        nop.t   side=0        ; the PHY pulls this turnaround bit low
        dly     HALF-4
        probe   MDIO
        nop.t   side=1
        ldi     r2, 0xA0
        jmp     nz, ok
        ldi     r2, 0xEE
ok:     bci     16
        sri     0
rd:     nop.t   side=0
        dly     HALF-4
        in      MDIO
        nop.t   side=1
        jmp     bcn, rd
        cmpi    r2, 0xEE
        jmp     z, st
        push
st:     mov     sr, r2
        bci     8
sh:     out     15
        jmp     bcn, sh
        push
        jmp     next
