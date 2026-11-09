; CAN receive in software: clock recovery, destuffing, CRC-15 check and ACK.
;
; Clock recovery: the bit timer samples mid-bit. After each sample, a WAIT with a timeout watches for
; the falling edge expected half a bit later. If it comes, TPHASE puts the next sample half a bit
; after that edge. If it doesn't, the timer carries on. Only falling edges count, as in CAN.
;
; For each frame the FIFO gets the bits from SOF to the end of the CRC, in 16-bit words, MSB first
; (the last word's unused low bits mean nothing), then one status word:
;   0 good and ACKed, 1 bad CRC, 2 stuff error (the frame's bits stop at the error).
; Assumes DLC is 8 or less. Needs L open-drain (register 1). The FIFO holds 8 words, so a frame whose bits
; and status take more than that needs the host reading during the frame. Words pushed to a full FIFO are lost.
;
; r0 CRC, r1 scratch, r2 bits left in this field, r3 run length, with bit 7 set for a run of ones.
.equ BIT, 100                 ; 500 kbit/s at 50 MHz
.equ L, 4
        msb     1
        set     L, 1
        mode    L, eng
        tset    BIT
idle:   ldi     r2, 10        ; a new frame needs 10 recessive bits first
i1:     probe.t L
        jmp     z, i2
        jmp     idle
i2:     djnz    r2, i1
        ldi     r1, 0
        mov     to, r1        ; no timeout
        wait    L, fall       ; start of frame: hard sync
        tphase  half
        ldi     r1, BIT/2
        mov     to, r1
        ldi     r0, 0
        ldi     r3, 0x81      ; the bus was recessive
        ldi     r2, 19        ; SOF, ID, RTR, IDE, r0, DLC
        bci     16
f1:     call    bit
        cmpi    r2, 0
        jmp     nz, f1
        mov     r2, sr        ; DLC is in the low 4 bits
        ldi     r1, 15
        and     r2, r1
        shl     r2
        shl     r2
        shl     r2
        add     r2, 15        ; data, then CRC
f2:     call    bit
        cmpi    r2, 0
        jmp     nz, f2
        cmpi    r0, 0         ; the CRC of data plus its own CRC is 0
        jmp     nz, badcrc
        nop.t                 ; middle of the CRC delimiter
        tphase  half
        set.t   L, 0          ; drive the ACK slot
        nop.t
        set     L, 1
        call    flush
        sri     0
        push
        jmp     idle
badcrc: call    flush
        sri     1
        push
        jmp     idle
err:    call    flush
        sri     2
        push
        jmp     idle

flush:  in      L             ; fill the word in progress, then send it
        jmp     bcn, flush
        push
        ret

; One bit on the wire. Stuff bits are checked and dropped, and everything else goes to the CRC and SR.
bit:    probe.t L
        ldi     r1, 0x80
        jmp     z, one
        cmpi    r3, 0x85      ; five ones, then this 0: stuff bit
        jmp     z, st0
        cmpi    r3, 5         ; a sixth 0
        jmp     z, err
        and     r1, r3
        jmp     nz, new0
        add     r3, 1
        jmp     crc0
new0:   ldi     r3, 1
crc0:   shl     r0            ; CRC feedback is its top bit XOR this bit
        jmp     nf, keep
        ldi     r1, 0x8B32
        xor     r0, r1
        jmp     keep
st0:    ldi     r3, 1
        jmp     sync
one:    cmpi    r3, 5         ; five zeros, then this 1: stuff bit
        jmp     z, st1
        cmpi    r3, 0x85      ; a sixth 1
        jmp     z, err
        and     r1, r3
        jmp     z, new1
        add     r3, 1
        jmp     crc1
new1:   ldi     r3, 0x81
crc1:   shl     r0
        jmp     f, keep
        ldi     r1, 0x8B32
        xor     r0, r1
        jmp     keep
st1:    ldi     r3, 0x81
        jmp     sync
keep:   in      L
        jmp     bcn, cnt
        push
        bci     16
cnt:    djnz    r2, sync
sync:   ldi     r1, 0
        shr     r1            ; clear F, so it shows only a timeout
        wait    L, fall
        jmp     f, done
        tphase  half
done:   ret
