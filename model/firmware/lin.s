; LIN responder: break detection, baud measured from the sync byte, PID parity, enhanced checksum.
;
; The trial version (trial/lin.s) worked around two gaps that ADDR and TSETR now fill. TSETR loads the measured
; bit time once per byte, where TPHASE had to reload it before every bit. ADDR adds a byte to the checksum in
; constant time, where counting up to it took about 1,000 cycles.
;
; Frames have 2 data bytes here. FIFO gets the PID, then, if it isn't ours, 0x01nn per data byte, and 0xA000 or
; 0xC500 for a bad checksum. A PID with bad parity gives 0xBAD0. If the PID is RESP, the node answers with the
; 2 bytes the host left in the FIFO, one word each, and pushes 0xA000 when done.
; r0 bit time. r1 scratch. r2 current byte. r3 checksum. Needs L open-drain (register 1).
.equ L, 4
.equ NOMINAL, 2604            ; 19200 baud
.equ RESP, 0x3C               ; the PID this node answers: ID 0x3C with its parity bits
        msb     0
        set     L, 1
        mode    L, eng
        ldi     r1, 0
        mov     to, r1
hunt:   wait    L, lo
        ldi     r2, 11        ; a break is at least 11 bits, and usually 13. Reject anything shorter
        tset    NOMINAL*9/10
brk:    probe.t L
        jmp     hi, hunt
        djnz    r2, brk
        wait    L, hi
        wait    L, lo         ; the sync byte's start bit
        ldi     r0, 0
        ldi     r1, 4         ; 4 more falling edges span 8 bits. Each count takes 8 cycles, so r0 becomes a bit time
low:    add     r0, 1
        dly     4
        probe   L
        jmp     lo, low
high1:  add     r0, 1
        dly     4
        probe   L
        jmp     hi, high1
        djnz    r1, low
        wait    L, hi

        call    getb          ; PID
        mov     r2, low
        mov     sr, r2
        ldi     r1, 0
        ldi     r3, 0
        tstb                  ; bit 0: P0
        out     15
        jmp     nz, p0
        add     r1, 1
p0:
        tstb                  ; bit 1: P0 and P1
        out     15
        jmp     nz, p1
        add     r1, 1
        add     r3, 1
p1:
        tstb                  ; bit 2: P0
        out     15
        jmp     nz, p2
        add     r1, 1
p2:
        tstb                  ; bit 3: P1
        out     15
        jmp     nz, p3
        add     r3, 1
p3:
        tstb                  ; bit 4: P0 and P1
        out     15
        jmp     nz, p4
        add     r1, 1
        add     r3, 1
p4:
        tstb                  ; bit 5: P1
        out     15
        jmp     nz, p5
        add     r3, 1
p5:
        tstb                  ; P0 itself
        out     15
        jmp     nz, p6
        add     r1, 1
p6:
        tstb                  ; P1 itself
        out     15
        jmp     nz, p7
        add     r3, 1
p7:
        shr     r1            ; P0 makes its group even
        jmp     f, badp
        shr     r3            ; P1 makes its group odd
        jmp     nf, badp
        mov     sr, r2
        push
        mov     r3, r2        ; the enhanced checksum covers the PID
        cmpi    r2, RESP
        jmp     z, answer

        call    getb
        mov     r2, low
        call    report
        call    addb
        call    getb
        mov     r2, low
        call    report
        call    addb
        call    getb          ; the checksum: with it added, the total is 0xFF
        mov     r2, low
        call    addb
        cmpi    r3, 0xFF
        jmp     nz, badc
        sri     0xA0, 8
        push
        jmp     hunt
badc:   sri     0xC5, 8
        push
        jmp     hunt
badp:   ldi     r1, 0xBAD0
        mov     sr, r1
        push
        jmp     hunt

answer: pullw   16
        mov     r2, sr
        call    putb
        call    addb
        pullw   16
        mov     r2, sr
        call    putb
        call    addb
        ldi     r2, 0xFF
        xor     r3, r2        ; invert the 8-bit sum
        mov     r2, r3
        call    putb
        sri     0xA0, 8
        push
        jmp     hunt

; One byte into the top of SR, sampled mid-bit using the measured bit time
getb:   wait    L, lo
        tset    r0            ; the timer now runs at the bit time on its own
        tphase  half          ; first tick half a bit after the edge: the middle of the start bit
        nop.t
        bci     8
gb:     in.t    L
        jmp     bcn, gb
        nop.t                 ; the middle of the stop bit
        ret

; Sends the low byte of r2, and keeps r2
putb:   mov     sr, r2
        bci     8
        tset    r0
        set.t   L, 0          ; start bit
pb:     out.t   L
        jmp     bcn, pb
        set.t   L, 1          ; stop bit
        nop.t
        ret

report: ldi     r1, 0x100
        xor     r1, r2
        mov     sr, r1
        push
        ret

; r3 += r2 with the carry wrapped around, in constant time. Both are bytes, so the sum is at most 0x1FE, and
; with a carry, (sum + 1) & 0xFF is sum - 0xFF. Uses r1.
addb:   add     r3, r2
        ldi     r1, 0x100
        and     r1, r3        ; Z unless there was a carry
        jmp     z, addd
        add     r3, 1
        ldi     r1, 0xFF
        and     r3, r1
addd:   ret
