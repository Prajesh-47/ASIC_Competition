; DALI control gear: receives forward frames, checks the address, answers a query with a backward frame.
; FIFO gets every well-formed frame, then 0x0100 if it was addressed to us, or 0x0300 for a malformed frame.
; Answers 0xFF (YES) to QUERY when addressed. Needs L open-drain (register 1).
; The trial version (trial/dali_gear.s) sent with 3 ticks of Te/3, since the 13-bit timer stopped at 8,191.
; This one loads Te through TSETR, one tick per half bit.
.equ TE, 20833
.equ L, 4
.equ ADDR, 5                  ; short address
.equ GROUP, 3
.equ QUERY, 0x91
.equ REPLY, 0xFF
        msb     1
        set     L, 1
        mode    L, eng
next:   tset    0
        ldi     r1, 0
        mov     to, r1
        wait    L, fall
        ldi     r1, TE*5/4
        mov     to, r1
        ldi     r3, 0
        shr     r3
        wait    L, hi
        jmp     f, bad
        bci     16
        ldi     r1, TE*3/2
rbit:   dlyr    r1
        in      L
        probe   L
        jmp     z, rf
        wait    L, rise
        jmp     f, bad
        jmp     rn
rf:     wait    L, fall
        jmp     f, bad
rn:     jmp     bcn, rbit
        dlyr    r1
        probe   L
        jmp     nz, bad
        ldi     r1, TE*2
        mov     to, r1
        wait    L, fall
        jmp     nf, bad

        mov     r0, sr        ; first halves low mean 1, so complement
        ldi     r1, 0xFFFF
        xor     r0, r1
        mov     sr, r0
        push
        mov     r1, low       ; address byte
        shr     r1
        cmpi    r1, 0x7F      ; broadcast
        jmp     z, hit
        cmpi    r1, 0x40+GROUP
        jmp     z, hit
        cmpi    r1, ADDR
        jmp     nz, next
hit:    sri     1, 8
        push
        ldi     r2, 0xFF
        and     r0, r2
        cmpi    r0, QUERY
        jmp     nz, next

        ldi     r3, TE
        tset    r3            ; one tick per half bit
        ldi     r2, 8         ; the reply starts 9 Te from here: 7 Te after the stop bits end, with a margin
gap:    nop.t
        djnz    r2, gap
        sri     REPLY, 8      ; left aligned: MSB first
        bci     8
        set.t   L, 0
        set.t   L, 1
bit:    tstb
        out     15
        jmp     z, one
        set.t   L, 1
        set.t   L, 0
        jmp     bcn, bit
        jmp     end
one:    set.t   L, 0
        set.t   L, 1
        jmp     bcn, bit
end:    set.t   L, 1
        jmp     next
bad:    sri     3, 8
        push
        jmp     next
