; SD host, native bus mode: 48-bit commands with CRC-7, responses, and 4-bit block transfers.
;
; Host pushes a command as: response type, then the first 40 bits of the command as 16, 16 and 8 bits (the
; last in the top byte). Response type: 0 none, 1 48 bits, 2 48 bits with no CRC (R3), 3 136 bits (R2).
; The firmware adds CRC-7 and the end bit, and checks CRC-7 on responses.
; FIFO gets the response words, then a status word: 0xA000 good, 0xC700 bad CRC, 0xEE00 no response.
; After any response the host pushes one more word: 0 nothing, 1 read a block, 2 write a block.
; A read ends with 0xA500, or 0xDD00 if no data came. A write ends with 0xA50s, s being the card's 3-bit CRC
; status, so 0xA502 is good, or 0xDD00 if no status came.
; A read gives 256 data words and 4 words of CRC-16, one per line, in the order on the wire: the host checks
; them. A write takes 256 data words and 4 words of CRC-16 from the host. Per-line CRC-16 in firmware would
; need 4 registers for the CRCs, plus a counter and a polynomial, and there are only 4 registers.
;
; The SD clock is the program's own, so it simply stops whenever the host falls behind, which SD allows.
; Commands run at 50 MHz / (2 * HALF). Data runs on instruction timing: 6.25 MHz read, 8.33 MHz write.
.equ HALF, 62                 ; 403 kHz, for identification
.equ CLK, 4
.equ CMD, 5
.equ NCR, 66                  ; clocks to wait for a response
.equ NAC, 4000                ; clocks to wait for read data
        msb     1
        set     CLK, 0
        side    CLK
        mode    CLK, eng
        set     CMD, 1
        mode    CMD, eng
        tset    HALF
next:   pullw   16
        mov     r3, sr
        mode    CMD, eng
        ldi     r0, 0
        ldi     r1, 0x1200    ; x^7 + x^3 + 1, left aligned
        pullw   16
        call    txb
        pullw   16
        call    txb
        pullw   8
        call    txb
        ldi     r1, 0x0100    ; end bit, just below the 7 CRC bits
        xor     r0, r1
        mov     sr, r0
        bci     8
        ldi     r1, 0         ; no feedback from here
        call    txb
        mode    CMD, hiz

        cmpi    r3, 0
        jmp     z, afterr
        ldi     r2, NCR
wstart: nop.t   side=0
        dly     HALF-4        ; sample late in the low phase: the card changes on the falling edge
        probe   CMD
        nop.t   side=1
        jmp     nz, got
        djnz    r2, wstart
        sri     0xEE, 8
        call    pushw
        jmp     next

got:    ldi     r0, 0         ; start bit taken. Now the rest
        sri     0
        bci     15
        ldi     r1, 0x1200
        ldi     r2, 46
        cmpi    r3, 3
        jmp     nz, rbit
        ldi     r2, 134
rbit:   nop.t   side=0
        dly     HALF-6
        probe   CMD
        in      CMD
        jmp     z, r1x
        shl     r0
        jmp     nf, rn
        xor     r0, r1
        jmp     rn
r1x:    shl     r0
        jmp     f, rn
        xor     r0, r1
rn:     nop.t   side=1
        jmp     bcn, rc
        call    pushw
        bci     16
rc:     djnz    r2, rbit
        ldi     r2, 0xA0      ; the CRC is whole now: decide before the end bit moves it
        cmpi    r3, 1
        jmp     nz, rend
        cmpi    r0, 0
        jmp     z, rend
        ldi     r2, 0xC7
rend:   nop.t   side=0
        dly     HALF-4
        in      CMD
        nop.t   side=1
        cmpi    r3, 3         ; R2 ends part way through a word
        jmp     nz, rw
fill:   in      CMD
        jmp     bcn, fill
rw:     call    pushw
        mov     sr, r2
        bci     8             ; move the status to the top byte
sh:     out     15
        jmp     bcn, sh
        call    pushw
        jmp     dataq         ; read data can start straight away, so no clocks until we know what's next

afterr: ldi     r0, 8
        call    idle
        jmp     next

dataq:  pull                  ; a word saying what comes next: 0 nothing, 1 read, 2 write
        jmp     f, dataq
        mov     r0, sr
        cmpi    r0, 1
        jmp     z, rdblk
        cmpi    r0, 2
        jmp     z, wrblk
        jmp     afterr        ; the card needs 8 clocks after a response

; ------------------------------------------------- Read a block
rdblk:  ldi     r2, NAC
waitd:  nop.t   side=0
        dly     HALF-4
        probe   0             ; DAT0 low is the start bit
        nop.t   side=1
        jmp     nz, dstart
        djnz    r2, waitd
        sri     0xDD, 8
        call    pushw
        jmp     next
dstart: ldi     r2, 260
dw:     bci     16
dn:     nop                   side=0
        dly     1             ; 2 sync cycles after the falling edge before anything can be read
        in      3
        in      2
        in      1
        in      0 side=1
        jmp     bcn, dn
        call    pushw
        djnz    r2, dw
        nop.t   side=0        ; end bit
        nop.t   side=1
        sri     0xA5, 8
        call    pushw
        ldi     r0, 8
        call    idle
        jmp     next

; ------------------------------------------------- Write a block
wrblk:  set     0, 1
        set     1, 1
        set     2, 1
        set     3, 1
        mode    0, eng
        mode    1, eng
        mode    2, eng
        mode    3, eng
        ldi     r0, 2
        call    idle
        nop.t   side=0
        set     0, 0          ; start bit on all four
        set     1, 0
        set     2, 0
        set     3, 0
        nop.t
        nop.t   side=1
        ldi     r2, 260
ww:     pullw   16
wn:     nop     side=1
        out     3 side=0
        out     2
        out     1
        out     0
        jmp     bcn, wn
        djnz    r2, ww
        nop     side=1
        nop.t   side=0        ; end bit
        set     0, 1
        set     1, 1
        set     2, 1
        set     3, 1
        nop.t
        nop.t   side=1
        mode    0, hiz
        mode    1, hiz
        mode    2, hiz
        mode    3, hiz
        ldi     r2, 40
wstat:  nop.t   side=0        ; the CRC status token: a start bit, then 3 bits
        dly     HALF-4
        probe   0
        nop.t   side=1
        jmp     nz, wtok
        djnz    r2, wstat
        sri     0xDD, 8
        call    pushw
        jmp     next
wtok:   ldi     r0, 0x14A0    ; 3 shifts move this to 0xA500, with the status in the low bits
        mov     sr, r0
        ldi     r2, 3
wt1:    nop.t   side=0
        dly     HALF-4
        in      0
        nop.t   side=1
        djnz    r2, wt1
        call    pushw
        ldi     r2, 0xFFFF    ; busy: DAT0 stays low until the card is ready
wbusy:  nop.t   side=0
        dly     HALF-4
        probe   0
        nop.t   side=1
        jmp     z, wdone
        djnz    r2, wbusy
wdone:  ldi     r0, 8
        call    idle
        jmp     next

; ------------------------------------------------- Helpers
txb:    tstb
        out.t   CMD side=0
        jmp     z, tx1
        shl     r0
        jmp     nf, txn
        xor     r0, r1
        jmp     txn
tx1:    shl     r0
        jmp     f, txn
        xor     r0, r1
txn:    nop.t   side=1
        jmp     bcn, txb
        ret

idle:   nop.t   side=0        ; r0 clocks
        nop.t   side=1
        djnz    r0, idle
        ret

pushw:  tset    0             ; a tick nobody consumes sets F too, and that would look like a full FIFO
        ldi     r1, 0
pw:     shr     r1            ; PUSH sets F when the FIFO is full, so clear F, and try until it goes in
        push
        jmp     f, pw
        ldi     r1, 0x1200
        tset    HALF
        ret
