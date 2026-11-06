; CAN transmit in software: CRC-15, bit stuffing and arbitration. One routine sends every field.
; Host pushes the first 16 frame bits, then the next 11, left aligned. r0 CRC, r1 polynomial,
; r2 run length, r3 last bit.
.equ BIT, 100                 ; 500 kbit/s at 50 MHz
.equ L, 4                     ; one pin, reads back what the bus resolves to
        msb     1
        set     L, 1
        mode    L, eng
        tset    BIT
        ldi     r1, 0x8B32    ; polynomial 0x4599, left aligned
        ldi     r0, 0
        ldi     r2, 0
        ldi     r3, 1
        pullw   16
        call    send
        pullw   11
        call    send
        mov     sr, r0
        bci     15
        call    send
        set.t   L, 1          ; CRC delimiter; ACK and EOF are recessive
        ldi     r2, 9
eof:    nop.t
        djnz    r2, eof
        halt
lost:   set     L, 1
        push                  ; tells the host arbitration was lost
        halt
send:   tstb
        out.t   L
        jmp     z, one
        cmpi    r3, 0         ; a dominant 0 went out
        jmp     z, s0
        ldi     r3, 0
        ldi     r2, 0
s0:     add     r2, 1
        shl     r0
        jmp     nf, stf
        xor     r0, r1
        jmp     stf
one:    cmpi    r3, 1         ; a recessive 1 went out: is the bus really recessive?
        jmp     z, s1
        ldi     r3, 1
        ldi     r2, 0
s1:     add     r2, 1
        shl     r0
        jmp     f, smp
        xor     r0, r1
smp:    dly     8             ; let our own edge come back through the synchronizer
        probe   L
        jmp     nz, lost
stf:    cmpi    r2, 5
        jmp     nz, nx
        ldi     r2, 1
        cmpi    r3, 0
        jmp     z, i1
        ldi     r3, 0
        set.t   L, 0
        jmp     nx
i1:     ldi     r3, 1
        set.t   L, 1
nx:     jmp     bcn, send
        ret
