; S/PDIF transmit, 48 kHz stereo, with the line coder's biphase-mark encoding (CODE bmc, explore/isa-code-rx).
; The preambles and their timing are trial/spdif_tx.s unchanged. The slots go out through OUTB.
;
; Host pushes 2 words per subframe, sent LSB first: 12 bits for slots 4-15, then 16 bits for slots 16-31, the
; last being even parity. The host computes it, where the trial version forced the line to 0 at the end.
; Even parity means every subframe ends at the level it started from, so the preamble always follows a 0.
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
        code    bmc
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
data:   pull
        bci     12
        jmp     nf, w1
        set     ERR, 1
w1:     outb.t  L, w1         ; slots 4-15: a change at each start, and a second for a 1
        pull
        bci     16
        jmp     nf, w2
        set     ERR, 1
w2:     outb.t  L, w2         ; slots 16-31
        jmp     sub
