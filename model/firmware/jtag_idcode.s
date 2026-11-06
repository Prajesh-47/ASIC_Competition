; JTAG: reset the TAP, go to Shift-DR, read a 32-bit IDCODE into two FIFO words.
.equ HALF, 25
.equ TCK, 4
.equ TMS, 5
.equ TDI, 6
.equ TDO, 7
        set     TCK, 0
        set     TDI, 0
        mode    TCK, eng
        mode    TMS, eng
        mode    TDI, eng
        side    TCK
        tset    HALF
        sri     0x5F          ; TMS, LSB first: 5 ones, Idle, Select, Capture, Shift
        bci     9
tms:    out.t   TMS side=0
        nop.t   side=1
        jmp     bcn, tms
        bci     16
lo:     nop.t   side=0
        inb.t   TDO, lo side=1
        push
        bci     15
hi:     nop.t   side=0
        inb.t   TDO, hi side=1
        set.t   TMS, 1 side=0  ; the last bit leaves Shift-DR
        in.t    TDO side=1
        push
        halt
