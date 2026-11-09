; DALI application controller: forward frames, backward frames, collision recovery.
; The trial version (trial/dali_controller.s) counted 3 ticks of Te/3, because the 13-bit timer stopped at
; 8,191 and Te is 20,833 cycles. The 16-bit timer takes Te directly through TSETR, so each half bit is one
; tick, and exact. The collision check a third into each released half is a delay instead. On receive, DLYR
; counts Te.
;
; Host pushes a 16-bit forward frame. FIFO gets one word per frame, and 0x04xx-free retries are silent
; apart from one 0x0400 per collision:
;   0x01nn backward byte nn   0x0200 no reply (NO)   0x0300 malformed reply   0x0400 collision, retrying
; Needs L open-drain (register 1).
;
; r0 frame, kept for retries. r1 delays and scratch. r2 counts. r3 scratch.
.equ TE, 20833
.equ L, 4
        msb     1
        set     L, 1
        mode    L, eng
next:   ldi     r1, 0         ; the timer can keep running: a missed tick sets M, not F
        mov     to, r1
        pullw   16
        mov     r0, sr
send:   ldi     r1, 0
        mov     to, r1
        wait    L, hi
        mov     sr, r0
        bci     16
        ldi     r1, TE/3      ; where to check a released half for a collision
        ldi     r3, TE
        tset    r3            ; one tick per half bit
        set.t   L, 0          ; start bit, a 1: low, then high
        set.t   L, 1
        call    high
bit:    tstb
        out     15            ; shift only: line 15 doesn't exist
        jmp     z, one
        set.t   L, 1          ; 0: high, then low
        call    high
        set.t   L, 0
        jmp     bcn, bit
        jmp     stop
one:    set.t   L, 0          ; 1: low, then high
        set.t   L, 1
        call    high
        jmp     bcn, bit
stop:   set.t   L, 1          ; two stop bits, idle
        ldi     r2, 4
s1:     nop.t
        djnz    r2, s1

        ldi     r1, 60000     ; the reply must start within 22 Te. A WAIT timeout is 16 bits, so loop
        mov     to, r1
        ldi     r2, 8
rw:     ldi     r3, 0
        shr     r3            ; clear F, so it shows only a timeout
        wait    L, fall
        jmp     nf, got
        djnz    r2, rw
        sri     2, 8
        push
        jmp     next

got:    ldi     r1, TE*5/4    ; every bit has an edge in its middle, so a missing one is an error
        mov     to, r1
        ldi     r3, 0
        shr     r3
        wait    L, hi         ; middle of the start bit
        jmp     f, bad
        bci     8
        sri     0
        ldi     r1, TE*3/2
rbit:   dlyr    r1            ; from one middle to the first half of the next bit
        in      L             ; first half low means 1, so SR holds the complement
        probe   L
        jmp     hi, rf
        wait    L, rise
        jmp     f, bad
        jmp     rn
rf:     wait    L, fall
        jmp     f, bad
rn:     jmp     bcn, rbit
        dlyr    r1            ; must be idle from here: another edge means too many bits
        probe   L
        jmp     lo, bad
        ldi     r1, TE*2
        mov     to, r1
        wait    L, fall
        jmp     nf, bad
        mov     r1, sr
        ldi     r2, 0xFF
        xor     r1, r2
        and     r1, r2
        ldi     r2, 0x100
        xor     r1, r2
        mov     sr, r1
        push
        jmp     next
bad:    sri     3, 8
        push
        jmp     next

; A third into a half we released. The timer keeps running through the delay, so the next tick is still
; the end of the half.
high:   dlyr    r1
        probe   L             ; if it's low, someone else is driving
        jmp     lo, coll
        ret
coll:   set     L, 1
        sri     4, 8
        push
        ldi     r1, 0
        mov     to, r1
        tset    TE/3          ; check the idle bus every third of a Te, as finely as before
back:   ldi     r2, 66        ; 22 Te of idle bus before trying again
b1:     probe.t L
        jmp     lo, back
        djnz    r2, b1
        jmp     send
