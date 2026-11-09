; I2C target: match an address, ACK it, receive a byte, ACK it, and hand it to the host.
; The host must set lines 4 and 5 open-drain.
.equ ADDR, 0x50
.equ SCL, 4
.equ SDA, 5
        msb     1
        set     SDA, 1
        mode    SDA, eng
        ldi     r1, 0xFF
idle:   wait    SDA, fall
        probe   SCL
        jmp     lo, idle      ; SDA fell with SCL low: not a START
        tsrc    SCL, rise
        bci     8
adr:    inb.t   SDA, adr
        mov     r0, sr
        and     r0, r1
        cmpi    r0, ADDR << 1
        jmp     nz, idle
        call    ack
        bci     8
dat:    inb.t   SDA, dat
        push
        call    ack
        jmp     idle
ack:    wait    SCL, fall
        set     SDA, 0
        wait    SCL, rise
        wait    SCL, fall
        set     SDA, 1
        tsrc    SCL, rise     ; forget the ACK clock's own edge
        ret
