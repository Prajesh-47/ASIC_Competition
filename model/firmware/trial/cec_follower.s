; HDMI-CEC follower: finds the start bit, samples each bit 1.05 ms after its falling edge, and pulls ACK low when
; a block is addressed to it. Timing is from memory. No source read in this trial confirmed it.
; FIFO: one word per block, {0x0100 if EOM | block}, or 0xBAD0 for a low pulse that isn't a start bit.
; r0 this frame is for us. r1 scratch. r2 ticks. r3 EOM. Needs L open-drain (register 1).
.equ TICK, 500                ; 10 us
.equ L, 4
.equ ADDR, 4
        msb     1
        set     L, 1
        mode    L, eng
        ldi     r1, 0
        mov     to, r1
        tset    TICK
idle:   wait    L, lo
        tphase  period
        ldi     r2, 340       ; a start bit is 3.5 to 3.9 ms low
        call    wait2
        probe   L
        jmp     z, nostart    ; PROBE sets Z when the line is high: it should still be low
        wait    L, hi
        ldi     r0, 2         ; 2: the next block is the header
block:  bci     8
        sri     0
bits:   wait    L, lo
        tphase  period
        ldi     r2, 105
        call    wait2
        in      L
        wait    L, hi
        jmp     bcn, bits
        wait    L, lo         ; EOM
        tphase  period
        ldi     r2, 105
        call    wait2
        probe   L
        ldi     r3, 0
        jmp     z, eom0
        jmp     eomd
eom0:   ldi     r3, 1
eomd:   wait    L, hi
        ldi     r2, 0x0F      ; header: is the low nibble our address?
        mov     r1, sr
        and     r1, r2
        cmpi    r0, 2
        jmp     nz, later
        ldi     r0, 0
        cmpi    r1, ADDR
        jmp     nz, later
        ldi     r0, 1
later:  cmpi    r3, 0
        jmp     z, pushb
        ldi     r1, 0x100
        mov     r2, sr
        xor     r2, r1
        mov     sr, r2
pushb:  push
        wait    L, lo         ; ACK bit: the initiator starts it
        cmpi    r0, 1
        jmp     nz, noack
        set     L, 0          ; hold it low until 1.5 ms
        tphase  period
        ldi     r2, 150
        call    wait2
        set     L, 1
noack:  wait    L, hi
        cmpi    r3, 0
        jmp     z, block
        jmp     idle
nostart: ldi    r1, 0xBAD0
        mov     sr, r1
        push
        jmp     idle
wait2:  nop.t
        djnz    r2, wait2
        ret
