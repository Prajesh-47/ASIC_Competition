; USB low speed, data packet, transmit: NRZI, bit stuffing and CRC-16 in software.
; Host pushes SYNC and PID, then 2 data bytes. r0 CRC, r1 polynomial, r2 run of ones, r3 D- level.
.equ BIT, 33                  ; 1% from 1.5 Mbit/s at 50 MHz
.equ DP, 4
.equ DM, 5
        set     DM, 1         ; idle J: D- high at low speed
        set     DP, 0
        mode    DM, eng
        mode    DP, eng
        side    DP
        tset    BIT
        ldi     r1, 0xA001
        ldi     r2, 0
        ldi     r3, 1
        pullw   16
        call    send          ; SYNC and PID
        ldi     r0, 0xFFFF    ; the CRC covers only what comes next
        pullw   16
        call    send
        ldi     r1, 0xFFFF
        xor     r0, r1
        mov     sr, r0
        bci     16
        ldi     r1, 0         ; no more CRC feedback
        call    send
        set.t   DM, 0 side=0  ; end of packet: SE0 for 2 bits, then J
        nop.t
        set.t   DM, 1 side=0
        halt
send:   tstb
        out     15            ; shift only
        jmp     z, one
        ldi     r2, 0         ; a 0 toggles the line
        cmpi    r3, 0
        jmp     z, t1
        ldi     r3, 0
        set.t   DM, 0 side=1
        jmp     c0
t1:     ldi     r3, 1
        set.t   DM, 1 side=0
c0:     shr     r0
        jmp     nf, chk
        xor     r0, r1
        jmp     chk
one:    add     r2, 1
        nop.t
        shr     r0
        jmp     f, chk
        xor     r0, r1
chk:    cmpi    r2, 6
        jmp     nz, nx
        ldi     r2, 0         ; stuff a 0: one more toggle
        cmpi    r3, 0
        jmp     z, s1
        ldi     r3, 0
        set.t   DM, 0 side=1
        jmp     nx
s1:     ldi     r3, 1
        set.t   DM, 1 side=0
nx:     jmp     bcn, send
        ret
