; Four contexts at once, each a quarter of the cycles (host register 0 = 3), and a protocol bridge
; that never touches the host:
;   context 0: UART receive on line 0, PUSH to link 0          (register 21 bits 1:0 = 1)
;   context 1: SPI controller on lines 4-6, PULLW from link 0  (register 20 bits 3:2 = 1)
;   context 2: UART transmit on line 1, from the host FIFO
;   context 3: UART receive on line 2, to the host FIFO
; Contexts 0 and 1 are the two halves of two_protocols.s, unchanged: only the routing differs. A UART
; byte lands in the top 8 bits of SR, which is where an MSB-first SPI byte starts, so no code converts.
.equ BIT, 40
.equ HALF, 10
.equ RXA, 0
.equ TX, 1
.equ RXB, 2
.equ SCK, 4
.equ MOSI, 5
.equ CS, 6
        tset    BIT
top:    wait    RXA, fall
        tphase  threehalf
        bci     8
rx:     inb.t   RXA, rx
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

utx:    set     TX, 1
        mode    TX, eng
        tset    BIT
utop:   pullw   8
        set.t   TX, 0
ubit:   outb.t  TX, ubit
        set.t   TX, 1
        jmp     utop

urx:    tset    BIT
btop:   wait    RXB, fall
        tphase  threehalf
        bci     8
brx:    inb.t   RXB, brx
        push
        jmp     btop
.ctx1 spi
.ctx2 utx
.ctx3 urx
