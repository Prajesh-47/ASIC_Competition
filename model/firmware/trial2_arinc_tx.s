; ARINC 429 transmitter. Second blind trial (lab/explore/trial2.md). One context.
; The line is bipolar return-to-zero: HI, NULL or LO. SPLICE drives a line driver's two logic inputs, as on
; common ARINC 429 driver chips: line 0 high = HI, line 1 high = LO, both low = NULL. Each bit is half a bit
; of HI (1) or LO (0), then half a bit of NULL. Words are separated by at least 4 bit times of NULL.
;
; Host words, two per ARINC word, in ARINC bit numbering (bit 1 is sent first):
;   w0 = bits 16..9 << 8 | label, the label in its usual order (its MSB, ARINC bit 1, in bit 7)
;   w1 = bits 32..17, bit 17 in bit 0. Bit 32, the parity, is replaced by odd parity.
; The label goes out MSB first and everything else LSB first: MSB switches the order mid-word.
.equ HALF, 250              ; 100 kbit/s. 2000 for 12.5 kbit/s
.equ A, 0
.equ B, 1
        side    B
        set     A, 0 side=0
        mode    A, eng
        mode    B, eng
        ldi     r1, 1
        poly    r1            ; one-bit CRC with x + 1: parity
        tset    HALF
next:   pullw   8
        tset    HALF          ; restart the bit clock: a tick left pending while PULLW waited would cut bit 1 short
        mov     r2, sr        ; w0, for bits 9..16
        ldi     r1, 0         ; parity so far
        msb     1
sh:     outb    15, sh        ; label to the top of SR (dummy shifts, nothing driven)
        bci     8
lab:    call    bit
        jmp     bcn, lab
        msb     0
        mov     sr, r2
        bci     8
sh2:    outb    15, sh2       ; drop the label: bits 9..16 at the bottom
        bci     8
lo8:    call    bit
        jmp     bcn, lo8
        pullw   15            ; bits 17..31
hi15:   call    bit
        jmp     bcn, hi15
        ldi     r0, 1
        xor     r1, r0        ; odd parity: the bit that makes the count of ones odd
        mov     sr, r1
        call    bit
        ldi     r0, 8         ; then 4 bit times (8 halves) of NULL before the next word
gap:    nop.t
        djnz    r0, gap
        jmp     next

; One bit from SR: HI or LO for half a bit, then NULL. Both lines change on the same edge
bit:    tstb
        jmp     z, one
        out     15            ; shift, and it's the bit CRC steps on (CRC sets Z, so after the branch)
        crc     r1, lsb
        set.t   A, 0 side=1   ; LO
        jmp     null
one:    out     15
        crc     r1, lsb
        set.t   A, 1 side=0   ; HI
null:   set.t   A, 0 side=0
        ret
