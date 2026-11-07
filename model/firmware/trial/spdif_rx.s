; S/PDIF receive, 48 kHz stereo, in one context, with no decoding on the data path.
;
; One sample per slot, 3/4 of the way through. Every slot starts with a change and a 1 has a second change,
; so a bit is 1 exactly when this sample equals the last. Decoding is an XOR the host does, and it works
; for either polarity. Every slot's start edge recentres the sampling, which is the clock recovery.
; Even parity comes down to one comparison: the last sample of a subframe equals the last preamble state.
;
; FIFO gets 2 raw words a subframe, sampled levels with oldest highest:
;   {preamble states 4-7, slots 4-15}, then {slots 16-31}. States 4-7 tell the preamble: 0010 or 1101 is M,
;   0100 or 1011 is W, 1000 or 0111 is B. Anything else is malformed. A host can decode everything from these.
; Pins: ERR goes high from the preamble after a bad one, or when sync is lost. PERR after a parity error.
; SYNC is high for the subframe after a B. FIFO takes 2 words per 10.4 us, so the host must keep up.
;
; r1 preamble states 0,3,4,5,6,7. r2 and r3 scratch.
.equ RX, 0
.equ ERR, 5
.equ PERR, 6
.equ SYNC, 7
        msb     1
        set     ERR, 0
        set     PERR, 0
        set     SYNC, 0
        mode    ERR, eng
        mode    PERR, eng
        mode    SYNC, eng
lost:   tset    0
        ldi     r1, 0x22      ; nothing to check yet
        ldi     r2, 13
        mov     to, r2
hunt:   ldi     r2, 0
        shr     r2            ; clear F, so it shows only a timeout
        in      RX            ; state 0's level if this turns out to be a preamble
        probe   RX
        jmp     z, hh
        wait    RX, hi
        jmp     f, pre
        jmp     hunt
hh:     wait    RX, lo
        jmp     f, pre
        jmp     hunt

; Three states with no change: a preamble. State 3 starts about now.
pre:    tset    8
        ldi     r2, 0
        shr     r2
        tphase  half          ; first sample in the middle of state 3, and the rest stay near the middle
        in.t    RX            ; state 3
        ldi     r2, 0x3F      ; first check the last subframe's preamble, in the gaps
        and     r1, r2
        cmpi    r1, 0x28
        jmp     z, isb
        cmpi    r1, 0x17
        jmp     z, isb
        in.t    RX            ; state 4
        cmpi    r1, 0x22
        jmp     z, good4
        cmpi    r1, 0x1D
        jmp     z, good4
        cmpi    r1, 0x24
        jmp     z, good4
        in.t    RX            ; state 5
        cmpi    r1, 0x1B
        jmp     z, good5
        set     ERR, 1
        set     SYNC, 0
        ldi     r2, 8
        mov     to, r2
        in.t    RX            ; state 6
        bci     13            ; 12 after state 7
        in.t    RX
        jmp     data
isb:    in.t    RX            ; state 4
        set     SYNC, 1
        set     ERR, 0
        ldi     r2, 8
        mov     to, r2
        in.t    RX            ; state 5
        in.t    RX            ; state 6
        bci     13
        in.t    RX
        jmp     data
good4:  set     SYNC, 0
        set     ERR, 0
        ldi     r2, 8
        mov     to, r2
        in.t    RX            ; state 5
        in.t    RX            ; state 6
        bci     13
        in.t    RX
        jmp     data
good5:  set     SYNC, 0
        set     ERR, 0
        ldi     r2, 8
        mov     to, r2
        in.t    RX            ; state 6
        bci     13
        in.t    RX
data:   mov     r1, sr
        probe   RX            ; still state 7: slot 4 starts with a change
        jmp     nz, d1lo
        wait    RX, lo
        jmp     f, bad
        tset    0
        jmp     d1
d1lo:   wait    RX, hi
        jmp     f, bad
        tset    0
d1:     dly     5
        in      RX
        jmp     bc0, half2
        probe   RX
        jmp     nz, a1lo
        wait    RX, lo
        jmp     f, bad
        jmp     a1
a1lo:   wait    RX, hi
        jmp     f, bad
a1:     dly     7
        in      RX
        jmp     bc0, half2
        probe   RX
        jmp     nz, a1lo
        wait    RX, lo
        jmp     f, bad
        jmp     a1

half2:  push                  ; the first word
        bci     16
        probe   RX
        jmp     nz, a2lo
        wait    RX, lo
        jmp     f, bad
        jmp     a2
a2lo:   wait    RX, hi
        jmp     f, bad
a2:     dly     7
        in      RX
        jmp     bc0, end
        probe   RX
        jmp     nz, a2lo
        wait    RX, lo
        jmp     f, bad
        jmp     a2

end:    push                  ; the second word
        mov     r3, sr
        xor     r3, r1
        ldi     r2, 1
        and     r3, r2
        jmp     z, pok
        set     PERR, 1
        jmp     tail
pok:    set     PERR, 0
        nop
tail:   dly     4
        in      RX            ; state 0, a slot start ago
        probe   RX
        jmp     nz, tl
        wait    RX, lo        ; if this changes soon, it isn't a preamble
        jmp     f, pre
        jmp     bad
tl:     wait    RX, hi
        jmp     f, pre
bad:    set     ERR, 1
        jmp     lost
