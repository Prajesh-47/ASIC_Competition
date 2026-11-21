; v2.1 measurement copy of trial2_dcc_rx.s: each `ldi rN, K / cmpu a, rN` is `cmpu a, K` (CMPK). Nothing else changed
; NMRA DCC decoder (receive side of S-9.1 and S-9.2). Second blind trial (lab/explore/trial2.md).
; Line 4 in, from the track through an optocoupler. Either polarity. One context.
;
; Every edge is a tick (TSRC both), and TCAP gives the length of the half bit that just ended.
; A half of 52 to 64 us is a 1 half; 90 us or more is a 0 half (TCAP saturates at 1.31 ms, so longer
; stretched zeros still read as 0 halves). Anything else, or two halves that disagree, is an error.
; A packet needs at least 20 one halves (10 bits) of preamble before its start bit.
;
; To the host: each byte as it arrives (0x00bb), then 0xA000 if the XOR of all the bytes is 0,
; 0xE001 if not, or 0xE002 if a bad bit cut the packet short.
.equ RX, 4
.equ S_MIN, 2600            ; 52 us
.equ S_MAX, 3200            ; 64 us
.equ L_MIN, 4500            ; 90 us
        msb     1
        tsrc    RX, both
sync:   ldi     r3, 0
rpre:   call    half
        cmpi    r1, 1
        jmp     nz, notone
        cmpi    r3, 40        ; count up to 40 halves
        jmp     z, rpre
        add     r3, 1
        jmp     rpre
notone: cmpi    r1, 2
        jmp     z, sync
        cmpu    r3, 20   ; a 0 half: the packet start bit, if the preamble was long enough
        jmp     f, sync
        call    half
        cmpi    r1, 0
        jmp     nz, sync
        ldi     r0, 0
        mov     to, r0        ; TO keeps the running XOR
rbyte:  ldi     r2, 0
        ldi     r3, 8
rbl:    call    half
        cmpi    r1, 2
        jmp     z, bad
        mov     sr, r1        ; the first half's class
        call    half
        cmp     r1, sr
        jmp     nz, bad
        shl     r2
        add     r2, r1        ; the bit: 1 for a pair of short halves
        djnz    r3, rbl
        mov     sr, r2
        push
        mov     r0, to
        xor     r0, r2
        mov     to, r0
        call    half          ; the bit after a byte: 0 for another byte, 1 for the end
        cmpi    r1, 2
        jmp     z, bad
        mov     sr, r1
        call    half
        cmp     r1, sr
        jmp     nz, bad
        cmpi    r1, 0
        jmp     z, rbyte
        mov     r0, to
        cmpi    r0, 0
        jmp     nz, xbad
        sri     0xA0, 8
        push
        jmp     sync
xbad:   ldi     r0, 0xE001
        mov     sr, r0
        push
        jmp     sync
bad:    ldi     r0, 0xE002
        mov     sr, r0
        push
        jmp     sync

; r1 = class of the half bit that ends at the next edge: 1 short, 0 long, 2 neither
half:   tcap.t  r0
        cmpu    r0, S_MIN
        jmp     f, h2
        cmpu    r0, S_MAX+1
        jmp     f, h1
        cmpu    r0, L_MIN
        jmp     f, h2
        ldi     r1, 0
        ret
h1:     ldi     r1, 1
        ret
h2:     ldi     r1, 2
        ret
