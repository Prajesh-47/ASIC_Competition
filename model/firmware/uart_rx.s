; UART receive, 8N1, on line 0. Each byte reaches the host in the top 8 bits of a FIFO word.
.equ BIT, 434
.equ RX, 0
        tset    BIT
top:    wait    RX, fall
        tphase  threehalf     ; first sample is the middle of data bit 0
        bci     8
bit:    inb.t   RX, bit
        push
        jmp     top
