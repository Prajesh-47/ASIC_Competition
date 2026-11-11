; S/PDIF receive with the line coder's biphase-mark decoding (CODE bmc, explore/isa-code-rx).
;
; Calibrates from the stream, so one binary takes any sample rate the engine can follow: of 96 edge intervals
; the shortest is one biphase state and the longest is a preamble's three, so a quarter of the two is a state,
; and the period timer is set to it. CODE sync then re-phases the timer
; at every edge, so ticks land mid-state, and CODE bmc decodes a bit from each pair of states. A missing
; change at a slot's start breaks the code and sets F, which is how loss of lock is seen.
;
; FIFO gets 2 words a subframe, MSB first, decoded:
;   word 1: bits 15:12 preamble states 4-7, then slots 4-15. States 4-7 are 0010 or 1101 for M, 0100 or 1011
;           for W, 1000 or 0111 for B, as for the trial version
;   word 2: slots 16-31
; Pins: ERR high while unlocked or after a bad preamble, PERR after a parity error, SYNC for the subframe
; after a B. Preambles and parity are checked in the next preamble's first states, so pins lag a subframe.
; Even parity holds when the next preamble's first state differs from this one's state 7: every slot starts
; with a change, so the line ends where it started exactly when the 1s are even.
;
; r0 preamble threshold. r1 one state, in cycles. r2 scratch. r3 the last subframe's preamble states, in bits 7:4.
.equ RX, 0
.equ ERR, 5
.equ PERR, 6
.equ SYNC, 7
        msb     1
        set     ERR, 1
        set     PERR, 0
        set     SYNC, 0
        mode    ERR, eng
        mode    PERR, eng
        mode    SYNC, eng

cal:    set     ERR, 1
        code    off
        tsrc    RX, both
        tcap.t  r2            ; the first interval may span a gap in the signal, so drop it
        ldi     r1, 0xFFFF    ; the shortest interval, one state
        ldi     r0, 0         ; the longest, a preamble's three
        ldi     r3, 96
calp:   tcap.t  r2
        cmpu    r2, r1
        jmp     nf, cal1
        mov     r1, r2
cal1:   cmpu    r0, r2
        jmp     nf, cal2
        mov     r0, r2
cal2:   djnz    r3, calp
        addr    r1, r0        ; a state is a quarter of the two, rounded: both are whole cycles
        add     r1, 2
        shr     r1
        shr     r1
        tset    r1
        mov     r0, r1        ; a preamble's 3-state run is longer than 2.5 states
        shr     r0
        addr    r0, r1
        addr    r0, r1
        ldi     r3, 128       ; a subframe has at most 60 edges and one preamble, so 2 subframes' worth
hunt:   tcap.t  r2
        cmpu    r2, r0
        jmp     nf, pre
        djnz    r3, hunt
        jmp     cal

; State 3 has just begun. Take states 4-7 as levels, then the slots decoded.
pre:    tsrc    RX, off       ; the period timer, re-phased by edges on RX
        tphase  period
        code    sync
        ldi     r2, 0
        shr     r2            ; clear F, so it shows only a broken code or a full FIFO
pre4:   bci     4
st:     inb.t   RX, st    ; states 4-7
        probe   RX            ; still state 7, the level slot 4 starts from
        jmp     hi, s7hi
        code    bmc, sync
        jmp     data
s7hi:   code    bmc, sync, lvl 1
data:   bci     12
w1:     inb.t   RX, w1    ; slots 4-15
        push
        mov     r2, low
        ldi     r3, 0xF0
        and     r2, r3        ; states 4-7 in bits 7:4
        bci     16
w2:     inb.t   RX, w2    ; slots 16-31
        push
        code    sync          ; levels again, for the preamble
        jmp     f, bad
        mov     r3, r2        ; this subframe's preamble, checked in the next one's first states
        ldi     r2, 0x10
        and     r2, r3        ; Z when state 7 was low
        jmp     z, l7
        ldi     r2, 0xF0
        xor     r3, r2        ; as if state 7 were low: B 1000, M 0010, W 0100
        probe.t RX            ; next state 0: it must differ from state 7. Z when high
        jmp     nz, pok
        jmp     perr
l7:     probe.t RX
        jmp     z, pok
perr:   set     PERR, 1
        jmp     kind
pok:    set     PERR, 0
kind:   nop.t                 ; state 1
        cmpi    r3, 0x80
        jmp     z, isb
        set     SYNC, 0
        cmpi    r3, 0x20
        jmp     z, good
        cmpi    r3, 0x40
        jmp     z, good
        jmp     bad
isb:    set     SYNC, 1
good:   set     ERR, 0
        nop.t                 ; state 2
        nop.t                 ; state 3
        jmp     pre4
bad:    set     ERR, 1
        jmp     cal
