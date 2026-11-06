; PS/2 receive. The device owns the clock, so every falling edge is a tick.
.equ CLK, 0
.equ DATA, 1
        tsrc    CLK, fall
top:    bci     11
bit:    inb.t   DATA, bit
        push
        jmp     top
