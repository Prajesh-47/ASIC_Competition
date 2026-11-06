; I2C controller: START, two bytes each checking ACK, STOP. Lines must be open-drain: set by the host.
.equ Q, 125                   ; a quarter of a 100 kHz clock
.equ SCL, 4
.equ SDA, 5
        mode    SCL, eng
        mode    SDA, eng
        msb     1
        ldi     r0, 2
        tset    Q
        set.t   SDA, 0        ; START
        set.t   SCL, 0
byte:   pullw   8
bit:    out.t   SDA
        set.t   SCL, 1
        nop.t
        set.t   SCL, 0
        jmp     bcn, bit
        set.t   SDA, 1        ; release for ACK
        set.t   SCL, 1
        probe.t SDA
        set.t   SCL, 0
        jmp     z, stop       ; high means NACK
        djnz    r0, byte
stop:   set.t   SDA, 0
        set.t   SCL, 1
        set.t   SDA, 1
        halt
