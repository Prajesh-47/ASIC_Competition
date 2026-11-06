; UART transmit, 8N1. Host pushes one byte per FIFO word.
.equ BIT, 434                 ; 50 MHz / 115200
.equ TX, 4
        set     TX, 1
        mode    TX, eng
        tset    BIT
top:    pullw   8
        set.t   TX, 0         ; start bit
bit:    outb.t  TX, bit       ; one word per data bit
        set.t   TX, 1         ; stop bit
        jmp     top
