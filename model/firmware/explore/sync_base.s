; Sync-word hunt on the current ISA: pass a raw NRZ stream from RX to TX, and invert the first bit after
; every 16-bit sync word, wherever it starts. The window is compared after every bit, and sampling carries
; on through the flip (DLYR places the MODEs on the bit boundaries while the ticks stay mid-bit).
; Baseline for lab/explore/match-reflex.md. SYNC is the 16 newest bits, oldest in bit 0.
.equ BIT, 100
.equ SYNC, 0x2DD4
.equ RX, 0
.equ TX, 4
.equ SLOT, 1
.equ P0, BIT / 2 - 3          ; first tick mid-bit, from the first falling edge of the preamble
.equ ADJ, 0                   ; fine alignment of the flip, in instruction slots
        mode    TX, pass
        tset    BIT
        ldi     r1, P0
        ldi     r2, SYNC
        ldi     r3, (BIT / 2) / SLOT - 4 + ADJ   ; slots from the deciding sample to the next boundary
        ldi     r0, (BIT / 2) / SLOT - 2 + ADJ   ; the same from the following sample
        wait    RX, fall
        tphase  r1
hunt:   in.t    RX
        cmp     sr, r2
        jmp     nz, hunt
        dlyr    r3
        mode    TX, inv       ; boundary into the bit after the sync word
        in.t    RX            ; keep sampling: the input itself is untouched
        dlyr    r0
        mode    TX, pass
        jmp     hunt
