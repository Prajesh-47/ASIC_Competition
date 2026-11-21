; v2.1 measurement copy of trial2_nec_rx.s: each `ldi rN, K / cmpu a, rN` is `cmpu a, K` (CMPK). Nothing else changed
; NEC infrared receiver, from a demodulating IR receiver's output (low while a burst arrives). Second blind
; trial (lab/explore/trial2.md). Line 4 in. One context.
;
; To the host, for each frame: 0xF000 | flags, then the 32 bits as two words, LSB first. Flag bit 0: the
; address byte's inverse checks, bit 1: the command's does (extended NEC fails bit 0, by design).
; A repeat code is 0xF100. An error is one word, 0xE00n: 1 leader burst, 2 leader space, 3 bit burst,
; 4 bit space, 5 final burst, 6 repeat burst. Lows shorter than 3.5 ms are taken as noise and ignored.
;
; Every length is counted in ticks of 1/16 unit (35.2 us), polling the line once a tick. A bit's value is the
; line level 32 ticks (1.125 ms) after its burst ends: still in the space means a 1.
.equ UNIT, 28125            ; 562.5 us
.equ T, (UNIT+8)/16         ; a sixteenth of it, 1758
.equ RX, 4
        ldi     r0, 0
        mov     to, r0
        tset    T
idle:   wait    RX, fall
        ldi     r1, 400       ; longest low counted
        call    low
        cmpu    r0, 100
        jmp     f, idle       ; noise
        cmpu    r0, 200   ; 9 ms is 256 ticks: accept 200 to 312
        jmp     f, e1
        cmpu    r0, 313
        jmp     nf, e1
        ldi     r1, 200
        call    high
        cmpu    r0, 48   ; 2.25 ms (64): a repeat code
        jmp     f, e2
        cmpu    r0, 81
        jmp     f, repc
        cmpu    r0, 96   ; 4.5 ms (128): a frame
        jmp     f, e2
        cmpu    r0, 161
        jmp     nf, e2
        bci     16
        ldi     r3, 2
bit:    ldi     r1, 40
        call    low
        cmpu    r0, 9   ; a 562.5 us burst is 16 ticks: accept 9 to 24
        jmp     f, e3
        cmpu    r0, 25
        jmp     nf, e3
        ldi     r1, 64
        call    space
        cmpu    r0, 9   ; 0: 16 ticks, 9 to 24. 1: 48 ticks, 36 to 60
        jmp     f, e4
        cmpu    r0, 25
        jmp     f, bok
        cmpu    r0, 36
        jmp     f, e4
        cmpu    r0, 61
        jmp     nf, e4
bok:    jmp     bcn, bit
        djnz    r3, w2
        jmp     fin
w2:     mov     r2, sr        ; first word
        bci     16
        jmp     bit
fin:    ldi     r1, 40
        call    low           ; the final burst
        cmpu    r0, 9
        jmp     f, e5
        cmpu    r0, 25
        jmp     nf, e5
        mov     to, sr        ; TO holds the second word while the inverses are checked
        ldi     r3, 0xF000
        ldi     r1, 0xFF
        mov     sr, r2
        mov     r0, low
        xor     r0, sr
        and     r0, r1
        cmpi    r0, 0xFF      ; high byte is the low byte inverted
        jmp     nz, x1
        add     r3, 1
x1:     mov     sr, to
        mov     r0, low
        xor     r0, sr
        and     r0, r1
        cmpi    r0, 0xFF
        jmp     nz, x2
        add     r3, 2
x2:     mov     sr, r3
        push
        mov     sr, r2
        push
        mov     sr, to
        push
        ldi     r0, 0
        mov     to, r0
        jmp     idle

repc:   ldi     r1, 40
        call    low
        cmpu    r0, 9
        jmp     f, e6
        cmpu    r0, 25
        jmp     nf, e6
        sri     0xF1, 8
        push
        jmp     idle

e1:     sri     1
        jmp     err
e2:     sri     2
        jmp     err
e3:     sri     3
        jmp     err
e4:     sri     4
        jmp     err
e5:     sri     5
        jmp     err
e6:     sri     6
err:    mov     r0, sr
        ldi     r1, 0xE000
        xor     r0, r1
        mov     sr, r0
        push
        ldi     r0, 0
        mov     to, r0
        jmp     idle

; r0 = ticks while the line stays low, up to r1
low:    ldi     r0, 0
ll:     nop.t
        add     r0, 1
        cmp     r0, r1
        jmp     z, lret
        probe   RX
        jmp     lo, ll
lret:   ret

; r0 = ticks while the line stays high, up to r1
high:   ldi     r0, 0
hl:     nop.t
        add     r0, 1
        cmp     r0, r1
        jmp     z, hret
        probe   RX
        jmp     hi, hl
hret:   ret

; A bit's space: like high, and shifts the bit into SR: the level 32 ticks in, or 0 if it ended sooner
space:  ldi     r0, 0
sl:     nop.t
        add     r0, 1
        cmpi    r0, 32
        jmp     nz, s2
        in      RX
s2:     cmp     r0, r1
        jmp     z, sret
        probe   RX
        jmp     hi, sl
        cmpu    r0, 32
        jmp     nf, sret
        in      RX            ; the burst has begun, so this reads 0
sret:   ret
