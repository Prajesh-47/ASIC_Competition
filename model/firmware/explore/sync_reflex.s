; Sync-word hunt with the match reflex: pass a raw NRZ stream from RX to TX, and invert the first bit after
; every 16-bit sync word, wherever it starts. MATCH compares the whole of SR on every shift, ARM stays armed
; (no "once"), and each hit queues a one-bit INV pulse from the next bit boundary. The loop only samples.
; SYNC is the 16 newest bits, oldest in bit 0.
.equ BIT, 100
.equ SYNC, 0x2DD4
.equ RX, 0
.equ TX, 4
.equ SLOT, 1
.equ P0, BIT / 2 - 3          ; first tick mid-bit, from the first falling edge of the preamble
.equ ADJ, 0                   ; unused: the flip is placed by the timer, not by instruction counts
        mode    TX, pass
        tset    BIT
        ldi     r1, P0
        ldi     r2, SYNC
        ldi     r3, 0xFFFF
        match   r2, r3
        arm     TX, inv, pulse
        wait    RX, fall
        tphase  r1
hunt:   in.t    RX
        jmp     hunt
