; WS2812 addressable LEDs: a bit is the width of a high pulse, in a 1.25 us period.
.equ PERIOD, 62
.equ T0H, 20
.equ T1H, 40
.equ DO, 4
        msb     1
        set     DO, 0
        mode    DO, eng
        tset    PERIOD
word:   pullw   16
bit:    tstb
        out     15            ; shift only: line 15 doesn't exist
        jmp     z, one
        set.t   DO, 1
        dly     T0H - 2
        set     DO, 0
        jmp     bcn, bit
        jmp     word
one:    set.t   DO, 1
        dly     T1H - 2
        set     DO, 0
        jmp     bcn, bit
        jmp     word
