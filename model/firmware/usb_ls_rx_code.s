; USB low speed receive with the v2 line coder: clock recovery (CODE sync), NRZI decoding and destuffing in
; hardware. Same FIFO format as usb_ls_rx.s: words as they fill, oldest bit lowest, from SYNC on, then at
; SE0 the unfinished word, whose lowest set bit is a marker with the last data bits above it, then IRQ.
;
; Each word starts as 0x8000, the marker, and shifts right a bit at a time. The SE0 sample has been shifted
; in by the time it's seen, so at the end the word shifts left one place to drop it, and if that word had
; just filled (BC = 0, marker gone) bit 0 becomes the marker.
.equ BIT, 33                  ; 1% from 1.5 Mbit/s at 50 MHz
.equ DP, 4
.equ DM, 5
        msb     0
        tset    BIT
        tsrc    DM, off       ; period timer; DM is the line the clock recovery follows
idle:   code    off
        wait    DM, hi        ; back to J
        wait    DM, fall      ; K: start of SYNC
        tphase  half
        code    nrzi, ones 6, lvl 1, sync   ; the level before SYNC was J, D- high
word:   sri     0x80, 8
        bci     16
bit:    in.t    DM            ; a stuff bit repeats this, at its own bit time
        probe   DP
        jmp     hi, live      ; D+ high: K, so not SE0
        probe   DM
        jmp     lo, eop       ; both low: SE0
live:   jmp     bcn, bit
        push
        jmp     word
eop:    mov     r0, sr
        shl     r0            ; drop the SE0 sample, the newest bit
        jmp     bcn, emit     ; the marker is still in the word
        ldi     r1, 1
        xor     r0, r1
emit:   mov     sr, r0
        push
        irq
        jmp     idle
