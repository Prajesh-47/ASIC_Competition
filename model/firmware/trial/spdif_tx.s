; S/PDIF transmit, 48 kHz stereo: about 8 cycles per biphase-mark state, in one context.
;
; Host pushes 2 words per subframe, sent LSB first: 12 bits for slots 4-15, then 15 bits for slots 16-30.
; Parity isn't calculated. Even parity means every subframe ends at the level it started from, so the second
; half of slot 31 is simply forced to 0. That also means the preamble always follows a 0.
;
; 6.144 MHz isn't a whole number of cycles. Data states take 8 cycles, and preamble states take 9, with the
; first longer on 5 subframes in 6, so subframes average 520.83 cycles: 48 kHz, with 20 ns of jitter.
; r0 which word is being sent. r1 M or W next. r2 counts to 6 for the stretch. r3 subframes to the next block.
.equ L, 4
.equ ERR, 5                   ; the host fell behind
.equ FIRST, 3                 ; tuned against the measured rate
        msb     0
        set     L, 0
        set     ERR, 0
        mode    L, eng
        mode    ERR, eng
        ldi     r1, 0
        ldi     r2, 6
        ldi     r3, 1
        tset    8
sub:    djnz    r2, a
        ldi     r2, 6
        tset    FIRST
        jmp     b
a:      nop
        tset    FIRST+1
b:      djnz    r3, nb
        ldi     r3, 384
        ldi     r1, 1         ; W comes next
        set.t   L, 1          ; B: 1110 1000
        tset    9
        nop.t
        nop.t
        set.t   L, 0
        set.t   L, 1
        set.t   L, 0
        nop.t
        tset    8
        nop.t
        jmp     data
nb:     cmpi    r1, 0
        jmp     z, m
        ldi     r1, 0
        set.t   L, 1          ; W: 1110 0100
        tset    9
        nop.t
        nop.t
        set.t   L, 0
        nop.t
        set.t   L, 1
        set.t   L, 0
        tset    8
        nop.t
        jmp     data
m:      ldi     r1, 1
        set.t   L, 1          ; M: 1110 0010
        tset    9
        nop.t
        nop.t
        set.t   L, 0
        nop.t
        nop.t
        set.t   L, 1
        tset    8
        set.t   L, 0
data:   ldi     r0, 0
        pull
        bci     12
        jmp     nf, l0
        set     ERR, 1
l0:     set.t   L, 1          ; every slot starts with a change
        tstb
        out     15            ; shift only: line 15 doesn't exist
        jmp     z, l0one
        nop.t                 ; a 0 has no change in its middle
        jmp     bcn, h1
        jmp     endh
l0one:  set.t   L, 0          ; a 1 has one
        jmp     bcn, l0
        jmp     endl
h1:     set.t   L, 0
        tstb
        out     15
        jmp     z, h1one
        nop.t
        jmp     bcn, l0
        jmp     endl
h1one:  set.t   L, 1
        jmp     bcn, h1
        jmp     endh

endl:   cmpi    r0, 0
        jmp     nz, lastl
        ldi     r0, 1
        pull
        bci     15
        jmp     l0
endh:   cmpi    r0, 0
        jmp     nz, lasth
        ldi     r0, 1
        pull
        bci     15
        jmp     h1
lastl:  set.t   L, 1          ; slot 31, from 0: up, then back to 0
        set.t   L, 0
        jmp     sub
lasth:  set.t   L, 0          ; slot 31, from 1: down, and it stays at 0
        nop.t
        jmp     sub
