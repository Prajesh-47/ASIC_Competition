; I2C fault injection: bridge both lines, and hide the target's ACK of the third byte, so the
; controller sees a NACK while the target sees nothing unusual.
.equ ACK3, 27                 ; falling SCL edges from START to the third ACK clock
.equ SCL_A, 0
.equ SDA_A, 1
.equ SCL_B, 4
.equ SDA_B, 5
        mode    SCL_A, odb
        mode    SDA_A, odb
        mode    SCL_B, odb
        mode    SDA_B, odb
        ldi     r0, ACK3
        tsrc    SCL_A, fall
cnt:    nop.t
        djnz    r0, cnt
        mode    SDA_A, f1     ; stop copying SDA to the controller for one clock
        nop.t
        mode    SDA_A, odb
        halt
