; Inline UART: pass line 0 through to line 4, and flip one chosen data bit of every frame.
.equ BIT, 434
.equ K, 3
.equ RX, 0
.equ TX, 4
        mode    TX, pass
        tset    BIT
        ldi     r1, (K + 1) * BIT - 1
top:    wait    RX, fall
        tphase  r1
        mode.t  TX, inv
        mode.t  TX, pass
        delay   19 * BIT / 2 - (K + 2) * BIT      ; hold off until the stop bit
        jmp     top
