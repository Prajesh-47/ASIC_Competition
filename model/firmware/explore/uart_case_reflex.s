; Inline UART with the match reflex: pass RX through to TX, and invert data bit 5 of every byte whose bits
; 0-4 equal RULE ('a' <-> 'A' for RULE 1). MATCH watches SR as INB fills it. The hit on bit 4 queues a
; one-bit INV pulse that starts on the next half-period point of the timer, which is the bit 5 boundary
; because the ticks are mid-bit. No instruction runs between the decision and the flip.
.equ BIT, 434
.equ RULE, 1
.equ RX, 0
.equ TX, 4
.equ SLOT, 1                   ; cycles per instruction: 2 when two contexts share the engine
.equ P0, 3 * BIT / 2 - 3      ; TPHASE preset: first tick in the middle of bit 0, 1.5 bits after the start edge
        mode    TX, pass
        tset    BIT
        ldi     r1, P0
        ldi     r2, RULE << 11
        ldi     r3, 0xF800    ; the five newest bits, LSB first
        match   r2, r3 pos=0  ; only once all five are in (BC reaches 0)
top:    bci     5
        arm     TX, inv, pulse once
        wait    RX, fall
        tphase  r1
lp:     inb.t   RX, lp
        delay   (4 * BIT) / SLOT - 4 ; to the middle of the stop bit, less the 3 slots before WAIT (as the baseline)
        jmp     top
