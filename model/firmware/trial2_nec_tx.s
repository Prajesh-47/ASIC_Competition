; NEC infrared transmitter, with the 38 kHz carrier made in firmware. Second blind trial (lab/explore/trial2.md).
; Line 0 drives the IR LED (high = LED on). One context.
;
; Host words: a command, then for a frame its 32 bits as two words, LSB first: normally address, ~address,
; command, ~command, but any 32 bits go (extended NEC).
;   0x00nn, w0, w1 : the frame, then nn repeat codes
;   0x80nn         : nn repeat codes only
; Every frame or repeat starts 108 ms (192 units) after the one before it, and the next command waits for that.
;
; Timing: a unit is 562.5 us = 28,125 cycles. A burst of n units is ceil(n * 28125 / 1316) carrier pulses,
; 1316 cycles apart (37.99 kHz), each 439 cycles high (a third). The unit timer is reloaded at every
; burst's end, so every burst starts on the exact unit grid however long the frame is.
.equ CAR, 1316
.equ HI, 439
.equ UNIT, 28125
.equ L, 0
.equ NCTX, 1                 ; contexts running: instructions and DLY take this many cycles each
.equ N1, (UNIT-HI-5*NCTX)/CAR   ; pulses after the first that fit in one unit (21)
.equ N16, (16*UNIT-HI-5*NCTX)/CAR ; and in the 9 ms leader (341)
.equ REM1, UNIT-N1*CAR-HI-5*NCTX ; cycles from the burst's end to one cycle before its unit boundary
.equ REM16, 16*UNIT-N16*CAR-HI-5*NCTX
        set     L, 0
        mode    L, eng
next:   ldi     r1, UNIT
        pullw   0
        mov     r3, sr
        mov     r0, sr
        ldi     r2, 0xFF
        and     r3, r2        ; repeats
        ldi     r2, 0         ; units since this frame began
        shl     r0
        jmp     f, reps       ; repeat codes only
        pullw   16            ; first 16 bits
        tset    100           ; start in 100 cycles
        ldi     r0, N16
        call    mark          ; 9 ms
        tset    REM16
        nop.t
        tset    r1
        nop.t                 ; 4.5 ms space: 8 units
        nop.t
        nop.t
        nop.t
        nop.t
        nop.t
        nop.t
        add     r2, 24
        ldi     r0, 0
        mov     to, r0        ; TO marks the second word (CALL has one level, so the bit loop is inline)
bits:   ldi     r0, N1        ; 16 bits from SR, LSB first: a one-unit burst, then 1 unit of space for 0, 3 for 1
        call    mark
        tset    REM1
        nop.t
        tset    r1
        add     r2, 2
        tstb
        out     15            ; shift: BC counts down
        jmp     nz, b0
        nop.t
        nop.t
        add     r2, 2
b0:     jmp     bcn, bits
        ldi     r0, 0
        cmp     to, r0
        jmp     nz, last
        mov     to, r1
        pullw   16
        jmp     bits
last:   ldi     r0, N1
        call    mark          ; the final burst
        tset    REM1
        nop.t
        tset    r1
        add     r2, 1
        jmp     wait

reps:   tset    100
rep:    cmpi    r3, 0
        jmp     z, next
        ldi     r0, N16
        call    mark          ; 9 ms
        tset    REM16
        nop.t
        tset    r1
        nop.t                 ; 2.25 ms space: 4 units
        nop.t
        nop.t
        ldi     r0, N1
        call    mark
        tset    REM1
        nop.t
        tset    r1
        add     r3, 0xFF      ; r3 - 1, in the low byte
        ldi     r0, 0xFF
        and     r3, r0
        add     r2, 21
wait:   cmpi    r2, 191       ; r2 is the unit just begun: the next frame or repeat starts at 192
        jmp     z, rep0
        nop.t
        add     r2, 1
        jmp     wait
rep0:   ldi     r2, 0
        cmpi    r3, 0
        jmp     nz, rep
        nop.t                 ; nothing more: the next command starts no sooner than unit 192
        jmp     next

; A burst of r0 + 1 carrier pulses, starting at the next tick
mark:   set.t   L, 1
        tset    CAR
        dly     HI/NCTX-3
        set     L, 0
p:      set.t   L, 1
        dly     HI/NCTX-2
        set     L, 0
        djnz    r0, p
        ret
