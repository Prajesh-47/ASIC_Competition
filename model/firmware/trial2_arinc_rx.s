; ARINC 429 receiver. Second blind trial (lab/explore/trial2.md). One context.
; A line receiver's two logic outputs: line 4 high while the bus is at HI, line 5 high while it's at LO.
; Self-clocked: every bit starts with a pulse on one of the two lines, so the rate only sets how long to
; wait. A word must follow at least 2 bit times of NULL.
;
; To the host, three words per ARINC word: a status, then w0 and w1 as the transmitter takes them
; (w0 = bits 16..9 << 8 | label in its usual order, w1 = bits 32..17). Status 0xA000 good, 0xE001 parity
; error, 0xE002 a bit with both or neither line high in its first half, 0xE003 the word stopped early,
; 0xE004 a line stuck high.
.equ BIT, 500               ; 100 kbit/s. 4000 for 12.5 kbit/s
.equ Q, BIT/5               ; sample this long after a pulse begins
.equ TMO, BIT/5             ; polling loops of 5 cycles: about a bit time
.equ GAPL, 2*BIT/5          ; 2 bit times of NULL before a word
.equ RA, 4
.equ RB, 5
        ldi     r1, 1
        poly    r1            ; parity, as a one-bit CRC
rgap:   ldi     r0, GAPL
g:      probe   RA
        jmp     hi, rgap
        probe   RB
        jmp     hi, rgap
        djnz    r0, g
first:  probe   RA             ; the word's first pulse, however long it takes
        jmp     hi, go
        probe   RB
        jmp     lo, first
go:     ldi     r1, 0
        ldi     r3, 0
        msb     1
        sri     0
        call    rs            ; bit 1, already begun
        cmpi    r3, 0
        jmp     nz, err
        bci     7
rlab:   call    rbit          ; label: MSB first
        cmpi    r3, 0
        jmp     nz, err
        jmp     bcn, rlab
        bci     8
fill:   inb     15, fill      ; SR = label << 8 | 0xFF
        msb     0
        bci     8
rlo8:   call    rbit          ; bits 9..16, LSB first, slide the label down to the low byte
        cmpi    r3, 0
        jmp     nz, err
        jmp     bcn, rlo8
        mov     r2, sr        ; w0
        bci     16
hi16:   call    rbit
        cmpi    r3, 0
        jmp     nz, err
        jmp     bcn, hi16
        cmpi    r1, 1         ; odd parity over all 32 bits
        jmp     z, good
        ldi     r3, 1
err:    mov     r0, sr        ; status 0xE00n, w0, w1 (as far as it got)
        sri     0xE0, 8
        mov     r1, sr
        xor     r1, r3
        mov     sr, r1
        push
        mov     sr, r2
        push
        mov     sr, r0
        push
        jmp     rgap
good:   mov     r0, sr
        sri     0xA0, 8
        push
        mov     sr, r2
        push
        mov     sr, r0
        push
        jmp     rgap

; One bit: wait for its pulse, check it, shift it in, wait for NULL. r3 = error code, 0 if fine
rbit:   ldi     r0, TMO
rw:     probe   RA
        jmp     hi, rs
        probe   RB
        jmp     hi, rs
        djnz    r0, rw
        ldi     r3, 3
        ret
rs:     dly     Q
        probe   RA
        jmp     hi, ra
        probe   RB
        jmp     lo, bad
        jmp     ok
ra:     probe   RB
        jmp     hi, bad
ok:     in      RA             ; HI is a 1
        crc     r1, lsb
        ldi     r0, TMO
rn:     probe   RA
        jmp     hi, rn2
        probe   RB
        jmp     lo, done
rn2:    djnz    r0, rn
        ldi     r3, 4
done:   ret
bad:    ldi     r3, 2
        ret
