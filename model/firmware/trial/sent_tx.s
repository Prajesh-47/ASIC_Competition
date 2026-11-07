; SENT transmitter (SAE J2716): sync of 56 ticks, then a status nibble, 6 data nibbles and CRC-4. Each nibble is
; the time between falling edges: 12 ticks plus its value. CRC-4 is x^4 + x^3 + x^2 + 1, seeded with 0101, over
; the data nibbles plus a zero nibble. Timing and CRC are from memory. No source read in this trial confirmed them.
; Host pushes {status, d1, d2, d3}, then {d4, d5, d6, 0}.
; r0 CRC, left aligned. r1 ticks. r2 scratch. r3 polynomial.
.equ TICK, 150                ; 3 us
.equ L, 4
.equ POLY, 0xD000
        msb     1
        set     L, 1
        mode    L, eng
        tset    TICK
next:   pullw   16
        tphase  period        ; a tick may have been waiting while the FIFO was empty
        ldi     r0, 0x5000
        ldi     r3, POLY
        ldi     r1, 56        ; sync
        call    pulse
        bci     4             ; status isn't in the CRC
        ldi     r1, 12
        tstb
        out     15
        jmp     nz, s1
        add     r1, 8
s1:     tstb
        out     15
        jmp     nz, s2
        add     r1, 4
s2:     tstb
        out     15
        jmp     nz, s3
        add     r1, 2
s3:     tstb
        out     15
        jmp     nz, s4
        add     r1, 1
s4:     call    pulse
        call    nibble
        call    nibble
        call    nibble
        pullw   16
        call    nibble
        call    nibble
        call    nibble
        ldi     r2, 4         ; the zero nibble, CRC only
z:      shl     r0
        jmp     nf, z1
        xor     r0, r3
z1:     djnz    r2, z
        mov     sr, r0
        ldi     r0, 0
        ldi     r3, 0
        call    nibble
        ldi     r1, 12        ; a pause pulse, so the CRC nibble has a falling edge to end on
        call    pulse
        jmp     next

; 4 bits of SR, added to the CRC, then sent. CALL is one level, so PULSE is inline here.
nibble: ldi     r1, 12
        tstb
        out     15
        jmp     z, n1a
        shl     r0
        jmp     nf, n2
        xor     r0, r3
        jmp     n2
n1a:    add     r1, 8
        shl     r0
        jmp     f, n2
        xor     r0, r3
n2:     tstb
        out     15
        jmp     z, n2a
        shl     r0
        jmp     nf, n3
        xor     r0, r3
        jmp     n3
n2a:    add     r1, 4
        shl     r0
        jmp     f, n3
        xor     r0, r3
n3:     tstb
        out     15
        jmp     z, n3a
        shl     r0
        jmp     nf, n4
        xor     r0, r3
        jmp     n4
n3a:    add     r1, 2
        shl     r0
        jmp     f, n4
        xor     r0, r3
n4:     tstb
        out     15
        jmp     z, n4a
        shl     r0
        jmp     nf, pulse
        xor     r0, r3
        jmp     pulse
n4a:    add     r1, 1
        shl     r0
        jmp     f, pulse
        xor     r0, r3

; r1 ticks between falling edges, the first 5 low. RET goes back to whichever CALL got here.
pulse:  set.t   L, 0
        ldi     r2, 4
lo:     nop.t
        djnz    r2, lo
        set.t   L, 1
        ldi     r2, 5
sub:    djnz    r1, sb2       ; count the 5 ticks already spent
sb2:    djnz    r2, sub
pw:     djnz    r1, pw2
        ret
pw2:    nop.t
        jmp     pw
