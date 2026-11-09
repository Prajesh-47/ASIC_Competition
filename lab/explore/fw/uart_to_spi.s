; Protocol translation with no host in the loop (explore/isa-lanes-table: ROUTE).
; Context 0 receives UART 8N1 on line 0 and hands each byte to context 1 through the mailbox. Context 1 sends it
; as an SPI controller, mode 0, on lines 4 to 6. This is ../../../model/firmware/two_protocols.s with two
; ROUTE lines added: everything else is unchanged, so existing firmware composes as it is.
.equ BIT, 40
.equ HALF, 10
.equ RX, 0
.equ SCK, 4
.equ MOSI, 5
.equ CS, 6
        route   push          ; PUSH goes to context 1, not the host
        tset    BIT
top:    wait    RX, fall
        tphase  threehalf
        bci     8
rx:     inb.t   RX, rx
        push
        jmp     top

spi:    route   pull          ; PULL and PULLW come from context 0
        set     SCK, 0
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
