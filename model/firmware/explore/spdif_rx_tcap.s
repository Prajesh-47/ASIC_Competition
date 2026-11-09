; S/PDIF receive at any sample rate from one binary, using the exploration opcodes TCAP and CMPU.
; Branch explore/scout-edge-timing. Not approved, not in RTL.
;
; The trial version (trial/spdif_rx.s) samples at fixed delays tuned for 48 kHz. This one never counts cycles.
; The tick source is RX on both edges, and TCAP.t reads how long the interval that just ended was, as latched
; by hardware. So the engine doesn't have to be waiting when an edge comes: it only has to read each interval
; before the next edge ends. Intervals are classed against two thresholds taken from the last preamble's
; 3-state interval P: short (one state) below P/2, long (two states) below 13P/16, and P above.
; Thresholds are refreshed at every preamble, which is the clock recovery.
;
; After a slot boundary the line level is recorded with IN, so the host decodes as for the trial version:
; bit k is 1 when the levels after boundaries k and k+1 are equal. That leaves the data path decode-free.
; FIFO gets 2 words a subframe, LSB first:
;   word 1: bits 2:0 preamble (1 M, 2 W, 3 B), bits 15:3 levels after the boundaries opening slots 4-16
;   word 2: levels after the boundaries opening slots 17-31 and the next preamble, bits 0-15
; Pins as the trial version: ERR high while unlocked or after a bad interval, PERR after a parity error,
; SYNC for the subframe after a B. Parity is even over slots 4-31, so it holds when the levels after
; slot 4's boundary and the next preamble's boundary are equal.
;
; r0 short/long threshold. r1 long/P threshold. r2 interval. r3 calibration count, then word 1.
.equ RX, 0
.equ ERR, 5
.equ PERR, 6
.equ SYNC, 7
        msb     0
        set     ERR, 1
        set     PERR, 0
        set     SYNC, 0
        mode    ERR, eng
        mode    PERR, eng
        mode    SYNC, eng
        tsrc    RX, both

; Calibrate: the longest of 96 intervals is P, since a subframe has at most 60 edges and contains one.
cal:    set     ERR, 1
        tcap.t  r2            ; the first interval may span a gap in the signal, so drop it
        ldi     r1, 0
        ldi     r3, 96
calp:   tcap.t  r2
        cmpu    r2, r1
        jmp     f, calk
        mov     r1, r2
calk:   djnz    r3, calp
        mov     r2, r1
        call    thr
        ldi     r3, 128       ; a subframe has at most 60 edges and one P, so 2 subframes' worth
hunt:   tcap.t  r2
        cmpu    r2, r1
        jmp     nf, pre       ; P
        djnz    r3, hunt
        jmp     cal           ; none: calibration took in a gap, such as idle before the stream, so redo it

; r2 holds P, which opened a preamble. The next three intervals tell B (1 3), M (3 1 1) or W (2 1 2) apart.
pre:    call    thr
        tcap.t  r2
        cmpu    r2, r0
        jmp     f, isb
        cmpu    r2, r1
        jmp     f, isw
        tcap.t  r2            ; M: two shorts
        cmpu    r2, r0
        jmp     nf, bad
        sri     1, 13
        set     SYNC, 0
        tcap.t  r2
        cmpu    r2, r0
        jmp     f, go
        jmp     bad
isw:    tcap.t  r2            ; W: short, long
        cmpu    r2, r0
        jmp     nf, bad
        sri     2, 13
        set     SYNC, 0
        tcap.t  r2
        cmpu    r2, r0
        jmp     f, bad
        cmpu    r2, r1
        jmp     f, go
        jmp     bad
isb:    tcap.t  r2            ; B: short, P
        cmpu    r2, r0
        jmp     nf, bad
        sri     3, 13
        set     SYNC, 1
        tcap.t  r2
        cmpu    r2, r1
        jmp     f, bad
go:     in      RX            ; slot 4's boundary just passed, at most 5 cycles ago
        set     ERR, 0
        bci     12

; Word 1: 12 more boundaries. A short means a 1, and the boundary is one more short away.
n1:     tcap.t  r2
        cmpu    r2, r0
        jmp     f, o1
        in      RX
        cmpu    r2, r1
        jmp     nf, bad       ; P inside the data
        jmp     bcn, n1
        jmp     w1
o1:     tcap.t  r2
        cmpu    r2, r0
        jmp     nf, bad
        in      RX
        jmp     bcn, n1
w1:     mov     r3, sr
        push
        sri     0
        bci     16

; Word 2: 16 boundaries, the last being the next preamble's.
n2:     tcap.t  r2
        cmpu    r2, r0
        jmp     f, o2
        in      RX
        cmpu    r2, r1
        jmp     nf, bad
        jmp     bcn, n2
        jmp     w2
o2:     tcap.t  r2
        cmpu    r2, r0
        jmp     nf, bad
        in      RX
        jmp     bcn, n2
w2:     push
        ldi     r2, 8         ; slot 4's level is bit 3 of word 1
        and     r2, r3        ; Z when it was 0
        jmp     z, l4lo
        probe   RX            ; still the level after the preamble's boundary, since P is long. Z when 1
        jmp     z, pok
        jmp     perr
l4lo:   probe   RX
        jmp     nz, pok
perr:   set     PERR, 1
        jmp     next
pok:    set     PERR, 0
next:   tcap.t  r2
        cmpu    r2, r1
        jmp     nf, pre       ; the preamble's P, as expected
bad:    set     ERR, 1
        jmp     cal

; Thresholds from P in r2: r0 = P/2, r1 = P/2 + P/4 + P/16. Uses r2.
thr:    mov     r0, r2
        shr     r0
        mov     r1, r0
        shr     r1
        mov     r2, r1
        shr     r2
        shr     r2
        addr    r1, r0
        addr    r1, r2
        ret
