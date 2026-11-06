; SWD: line reset, then read DPIDR. One data line, turned around mid-transaction.
.equ HALF, 25
.equ CLK, 4
.equ DIO, 5
        set     CLK, 0
        set     DIO, 1
        mode    CLK, eng
        mode    DIO, eng
        side    CLK
        tset    HALF
        ldi     r0, 52
rst:    nop.t   side=0
        nop.t   side=1
        djnz    r0, rst
        set.t   DIO, 0 side=0  ; idle low frames the request's start bit
        nop.t   side=1
        nop.t   side=0
        nop.t   side=1
        sri     0xA5          ; start, DP, read, A=0, parity, stop, park
        bci     8
rq:     out.t   DIO side=0
        nop.t   side=1
        jmp     bcn, rq
        nop.t   side=0
        mode    DIO, hiz      ; release the shared line
        nop.t   side=1
        nop.t   side=0
        nop.t   side=1
        bci     3
ack:    in.t    DIO side=0
        nop.t   side=1
        jmp     bcn, ack
        push                  ; ACK, in the top 3 bits of SR
        bci     16
d0:     in.t    DIO side=0
        nop.t   side=1
        jmp     bcn, d0
        push
        bci     16
d1:     in.t    DIO side=0
        nop.t   side=1
        jmp     bcn, d1
        push
        bci     1
        in.t    DIO side=0    ; parity
        push
        nop.t   side=1
        halt
