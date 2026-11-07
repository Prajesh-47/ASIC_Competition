; HDMI-CEC initiator, one open-drain wire: start bit, then blocks of 8 bits, EOM and ACK. Timing is in 100 us
; ticks. It listens to its own 1 bits, so another initiator sending a 0 wins arbitration, and it checks each ACK.
; Timing is from memory. No source read in this trial confirmed it.
; Host pushes {block << 8 | EOM}. FIFO: 0xA000 sent and acknowledged, 0x0E00 no ACK, 0xAB00 lost arbitration.
; r0 current word. r1 scratch. r2 ticks. r3 0 for data bits, 1 for the EOM bit. Needs L open-drain (register 1).
.equ TICK, 5000
.equ L, 4
        msb     1
        set     L, 1
        mode    L, eng
        tset    TICK
next:   pullw   16
        mov     r0, sr
free:   ldi     r1, 70        ; the bus must be free for 7 bit times
f1:     probe.t L
        jmp     nz, free
        djnz    r1, f1
        set.t   L, 0          ; start bit
        ldi     r2, 37
        call    wait2
        set     L, 1
        ldi     r2, 8
        call    wait2
block:  ldi     r3, 0
        mov     sr, r0
        bci     8
bit:    tstb
        out     15
        jmp     z, one
        set     L, 0          ; 0: low 1.5 ms, high 0.9 ms
        ldi     r2, 15
        call    wait2
        set     L, 1
        ldi     r2, 9
        call    wait2
        jmp     nb
one:    set     L, 0          ; 1: low 0.6 ms, then released
        ldi     r2, 6
        call    wait2
        set     L, 1
        ldi     r2, 5
        call    wait2
        probe   L             ; at 1.1 ms: if it's low, someone else is sending a 0
        jmp     nz, lost
        ldi     r2, 13
        call    wait2
nb:     jmp     bcn, bit
        cmpi    r3, 0
        jmp     nz, ack
        ldi     r3, 1         ; now the EOM bit, from the low bit of the word
        mov     sr, r0
        bci     15
ms:     out     15
        jmp     bcn, ms
        bci     1
        jmp     bit
ack:    set     L, 0          ; ACK: send a 1, and the follower holds it low
        ldi     r2, 6
        call    wait2
        set     L, 1
        ldi     r2, 5
        call    wait2
        probe   L
        ldi     r2, 13
        jmp     nz, acked
        call    wait2
        sri     0x0E, 8
        push
        jmp     next
acked:  call    wait2
        mov     r1, r0
        ldi     r2, 1
        and     r1, r2
        jmp     nz, done      ; AND sets Z if the result is 0, so this is EOM
        pullw   16
        mov     r0, sr
        jmp     block
done:   sri     0xA0, 8
        push
        jmp     next
lost:   set     L, 1
        sri     0xAB, 8
        push
        jmp     next
wait2:  nop.t
        djnz    r2, wait2
        ret
