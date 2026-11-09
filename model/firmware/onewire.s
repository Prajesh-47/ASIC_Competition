; 1-Wire controller: reset, check presence, write a command, read a reply into the FIFO.
; The host must set line 4 open-drain.
.equ US, 50
.equ CMD, 0xCC
.equ L, 4
        set     L, 1
        mode    L, eng
        set     L, 0          ; reset pulse, 480 us
        ldi     r0, 6
rst:    dly     3999
        djnz    r0, rst
        set     L, 1
        dly     70 * US
        probe   L             ; a device answers by pulling low
        jmp     hi, fail
        delay   410 * US
        sri     CMD
        bci     8
wr:     tstb
        out     15
        set     L, 0
        jmp     z, w1
        dly     60 * US
        set     L, 1
        dly     10 * US
        jmp     bcn, wr
        jmp     rd
w1:     dly     6 * US
        set     L, 1
        dly     64 * US
        jmp     bcn, wr
rd:     bci     8
rb:     set     L, 0
        dly     6 * US
        set     L, 1
        dly     8 * US
        in      L
        dly     55 * US
        jmp     bcn, rb
        push
fail:   halt
