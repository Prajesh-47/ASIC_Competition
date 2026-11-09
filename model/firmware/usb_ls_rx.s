; USB low speed receive in software: clock recovery, NRZI decoding and destuffing, at 33 cycles a bit.
;
; Words reach the FIFO as they fill, oldest bit lowest, from SYNC on. At the end of the packet (SE0)
; the unfinished word follows. Its lowest set bit is a marker, with the last data bits above it, so
; the packet's length needs no counter. IRQ then says the packet is over. CRC is left to the host.
; A word takes about 10 us on the wire and the FIFO holds 8, so the host must keep up.
;
; Clock recovery as in can_rx.s, on both edges. A timed-out WAIT sets F, and shifts overwrite F, so the
; two paths that don't shift, or that shift a 1 out, clear it first by shifting r3 out and back.
;
; r0 word in progress, with its marker. r1 is 0x8000. r2 run of ones. r3 last D- level.
.equ BIT, 33                  ; 1% from 1.5 Mbit/s at 50 MHz
.equ DP, 4
.equ DM, 5
        msb     0
        tset    BIT
        ldi     r1, 0x8000
idle:   ldi     r3, 0
        mov     to, r3        ; no timeout
        wait    DM, hi        ; back to J
        wait    DM, fall      ; K: start of SYNC
        tphase  half
        ldi     r3, BIT/2
        mov     to, r3
        ldi     r3, 1
        ldi     r2, 0
        mov     r0, r1
bit:    probe.t DM
        jmp     z, high
        probe   DP
        jmp     nz, eop       ; both low: SE0
        cmpi    r3, 0
        jmp     z, one
        ldi     r3, 0
        jmp     zero
high:   cmpi    r3, 1
        jmp     z, one
        ldi     r3, 1
zero:   cmpi    r2, 6         ; six ones, then a transition: stuff bit
        jmp     z, drop
        ldi     r2, 0
        shr     r0
        jmp     f, word
        jmp     sync
drop:   ldi     r2, 0
        jmp     clear
one:    add     r2, 1
        shr     r0
        xor     r0, r1
        jmp     f, word
sync:   cmpi    r3, 0
        jmp     z, up
        wait    DM, fall
        jmp     f, bit
        tphase  half
        jmp     bit
up:     wait    DM, rise
        jmp     f, bit
        tphase  half
        jmp     bit
word:   mov     sr, r0
        push
        mov     r0, r1
clear:  shl     r3
        shr     r3
        jmp     sync
eop:    mov     sr, r0
        push
        irq
        jmp     idle
