; Inline UART on the current ISA: pass RX through to TX, and invert data bit 5 of every byte whose bits 0-4
; equal RULE. With RULE 1 that swaps 'a' and 'A' in flight. Bit 4 decides, and the flip must start on the
; bit 5 boundary, so the compare, branch and MODE all have to fit in the second half of bit 4.
; Baseline for lab/explore/match-reflex.md: written to be as fast as the current ISA allows.
.equ BIT, 434
.equ RULE, 1
.equ RX, 0
.equ TX, 4
.equ SLOT, 1                   ; cycles per instruction: 2 when two contexts share the engine
.equ P0, 3 * BIT / 2 - 3      ; TPHASE preset: first tick in the middle of bit 0, 1.5 bits after the start edge
        mode    TX, pass
        tset    BIT
        ldi     r1, P0
        ldi     r2, RULE << 11 ; after SRI 0 and five LSB-first shifts, SR is exactly bits 0-4 << 11
top:    sri     0
        bci     5
        wait    RX, fall
        tphase  r1
lp:     inb.t   RX, lp        ; bits 0-4, mid-bit
        tphase  half          ; ticks move to bit boundaries
        cmp     sr, r2
        jmp     nz, skip
        mode.t  TX, inv       ; bit 5 boundary
        mode.t  TX, pass      ; bit 6 boundary
        delay   (5 * BIT / 2) / SLOT - 4 ; to the middle of the stop bit
        jmp     top
skip:   delay   (4 * BIT) / SLOT - 6
        jmp     top
