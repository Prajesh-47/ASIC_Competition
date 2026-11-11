; USB low speed, data packet, transmit with the v2 line coder: NRZI, bit stuffing and the D+/D- pair in
; hardware (CODE), CRC-16 one CRC step per data bit. Same host interface as usb_ls_tx.s: the host pushes
; SYNC and PID, then 2 data bytes, each word low byte first.
.equ BIT, 33                  ; 1% from 1.5 Mbit/s at 50 MHz
.equ DP, 4
.equ DM, 5                    ; the coded line. diff drives DM ^ 1 = DP with its complement
        set     DM, 1         ; idle J: D- high at low speed
        set     DP, 0
        mode    DM, eng
        mode    DP, eng
        side    DP
        tset    BIT
        ldi     r1, 0xA001    ; CRC-16, reflected
        poly    r1
        code    nrzi, ones 6, diff
        pullw   16
sp:     outb.t  DM, sp        ; SYNC and PID, outside the CRC
        ldi     r0, 0xFFFF
        pullw   16
data:   out.t   DM            ; a stuffed bit repeats this instruction, so the CRC sees data bits only
        crc     r0, lsb
        jmp     bcn, data
        ldi     r1, 0xFFFF
        xor     r0, r1
        mov     sr, r0
        bci     16
crc:    outb.t  DM, crc       ; the CRC is stuffed too
        code    off
        set.t   DM, 0 side=0  ; end of packet: SE0 for 2 bits, then J
        nop.t
        set.t   DM, 1 side=0
        halt
