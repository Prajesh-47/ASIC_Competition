; NMRA DCC command station signal (S-9.1, S-9.2). Second blind trial (lab/explore/trial2.md).
; Line 0 is the DCC signal and line 1 its complement, for an H-bridge; both change on the same edge
; (side-set). One context.
;
; The track signal never stops: when the host has no packet waiting, an idle packet goes out
; (FF 00, check FF). A host packet is a word with the byte count n (1 to 5), then n words each holding a
; byte in the low 8 bits, all sent together. The firmware adds the preamble (14 ones), the start bits,
; the error-detection byte (XOR of the others) and the end bit.
.equ ONE, 2900              ; 58 us half bit
.equ ZERO, 5000             ; 100 us half bit
.equ PRE, 14
.equ L, 0
        msb     1
        side    L+1
        set     L, 0 side=1
        mode    L, eng
        mode    L+1, eng
        tset    100
pkt:    ldi     r3, 0
        pull
        jmp     nf, have
        ldi     r3, 1         ; idle packet
        sri     2
have:   mov     to, sr        ; byte count
        ldi     r2, PRE
pre:    ldi     r0, ONE-1
        call    half2
        djnz    r2, pre
        mov     r2, to
        add     r2, 1         ; and the error-detection byte
        ldi     r1, 0
byte:   ldi     r0, ZERO-1
        call    half2         ; packet or data start bit
        cmpi    r2, 1
        jmp     z, chk
        cmpi    r3, 0
        jmp     nz, idleb
        pull                  ; a host byte, in the low 8 bits
        bci     8
sh:     outb    15, sh        ; to the top of SR, for MSB first
        jmp     got
idleb:  sri     0xFF, 8
        cmpi    r2, 3
        jmp     z, got
        sri     0
        jmp     got
chk:    mov     sr, r1
got:    mov     r0, sr
        xor     r1, r0
        bci     8
bl:     tstb
        out     15            ; shift
        ldi     r0, ONE-1
        jmp     z, b1
        ldi     r0, ZERO-1
b1:     call    half2
        jmp     bcn, bl
        djnz    r2, byte
        ldi     r0, ONE-1
        call    half2         ; packet end bit
        jmp     pkt

; One bit: two halves of r0 + 1 cycles each, the line high then low. Each half is timed from the edge
; that starts it, so the code between bits doesn't change the timing
half2:  set.t   L, 1 side=0
        tset    r0
        set.t   L, 0 side=1
        tset    r0
        ret
