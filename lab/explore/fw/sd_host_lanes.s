; SD host, native bus mode, rewritten for the exploration ISA (explore/isa-lanes-table): LANES, word CRC.
; Same host protocol and results as ../../../model/firmware/sd_host.s, which this is a copy of. Only the two
; data loops changed:
;   read:  "nop side=0 / inb 0 side=1" moves a nibble every 2 cycles, so the SD clock runs at 25 MHz inside a
;          word. After each word, "crcw" steps each line's CRC-16 on its 4 bits of SR in one cycle.
;   write: "nop side=1 / outb 0 side=0", the same 25 MHz.
; Inputs reach the engine 3 cycles after the output that caused them, so in the 2-cycle read loop each INB
; reads the nibble from the clock before. One clock before the loop primes it, and the loop's last fall clocks
; out the end bit.
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
        ldi     r1, 0x1200    ; CRC-7, x^7 + x^3 + 1, left aligned
        poly    r1
        pullw   16
        call    txb
        pullw   16
        call    txb
        pullw   8
        call    txb
        ldi     r1, 0x0100    ; end bit, just below the 7 CRC bits
        xor     r0, r1
        mov     sr, r0        ; the CRC and end bit go out from SR. Stepping r0 on them is harmless
        bci     8
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
        ldi     r2, 46
        cmpi    r3, 3
        jmp     nz, rbit
        ldi     r2, 134
rbit:   nop.t   side=0
        dly     HALF-5        ; the same sampling point as before, which had a PROBE first
        in      CMD
        crc     r0, msb
        nop.t   side=1
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
dstart: tset    0             ; no ticks in the loop, so F stays clear and PUSH can be checked cheaply
        ldi     r0, 0x1021    ; CRC-16, x^16 + x^12 + x^5 + 1, from 0, one per line
        poly    r0
        ldi     r0, 260       ; words: 256 of data and 4 of CRC. The read loop has no WAIT, so TO is free
        mov     to, r0
        ldi     r0, 0
        ldi     r1, 0
        ldi     r2, 0
        ldi     r3, 0
        lanes   4             ; IN and INB now take DAT3..DAT0 as one nibble
        nop     side=0        ; prime: the card puts out nibble 0
        nop     side=1
dw:     bci     4
dn:     nop     side=0
        inb     0, dn side=1
        crcw    r3, msb, 3    ; each line's 4 bits of this word, oldest first
        crcw    r2, msb, 2
        crcw    r1, msb, 1
        crcw    r0, msb, 0
        push
        jmp     f, full
counted: mov    sr, r0        ; count down in TO, with r0 parked in SR, which was just pushed
        mov     r0, to
        djnz    r0, more
        mov     r0, sr
        jmp     dend
more:   mov     to, r0
        mov     r0, sr
        jmp     dw
full:   shr     r3            ; FIFO full: clear F without losing r3 (see pushw), and try again
        jmp     nf, fe
        shl     r3
        add     r3, 1
        jmp     ff
fe:     shl     r3
ff:     push
        jmp     f, full
        jmp     counted
dend:   lanes   1             ; the loop's last fall already clocked out the end bit
        tset    HALF
        cmpi    r3, 0         ; data and its CRC leave a zero remainder on each good line
        ldi     r3, 0         ; r3 becomes the mask of bad lines. LDI keeps Z
        jmp     z, k2
        ldi     r3, 8
k2:     cmpi    r2, 0
        jmp     z, k1
        add     r3, 4
k1:     cmpi    r1, 0
        jmp     z, k0
        add     r3, 2
k0:     cmpi    r0, 0
        jmp     z, kd
        add     r3, 1
kd:     sri     0xA5, 8
        cmpi    r3, 0
        jmp     z, kp
        ldi     r0, 0xC100
        xor     r3, r0
        mov     sr, r3
kp:     call    pushw
        ldi     r0, 8
        call    idle
        jmp     next

; ------------------------------------------------- Write a block
wrblk:  lanes   4
        sri     0xF0, 8       ; all four high, from the top nibble
        out     0 ns
        mode    0, eng
        mode    1, eng
        mode    2, eng
        mode    3, eng
        ldi     r0, 2
        call    idle
        sri     0             ; start bit on all four
        nop.t   side=0
        out     0 ns
        nop.t
        nop.t   side=1
        ldi     r2, 260
ww:     pullw   4
wn:     nop     side=1
        outb    0, wn side=0
        djnz    r2, ww
        nop     side=1
        sri     0xF0, 8
        nop.t   side=0        ; end bit
        out     0 ns
        nop.t
        nop.t   side=1
        lanes   1
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
txb:    out.t   CMD side=0
        crc     r0, msb
        nop.t   side=1
        jmp     bcn, txb
        ret

idle:   nop.t   side=0        ; r0 clocks
        nop.t   side=1
        djnz    r0, idle
        ret

; PUSH sets F when the FIFO is full, so clear F and try until the word goes in. F is cleared through r3 and
; restored, since r0-r3 can all be live: shifting right and back loses only bit 0, which F holds, so it is
; added back when it was 1. A tick nobody consumes sets F too, so the timer stops meanwhile.
pushw:  tset    0
pd:     shr     r3
        jmp     nf, pe
        shl     r3
        add     r3, 1
        jmp     pf
pe:     shl     r3
pf:     push
        jmp     f, pd
        tset    HALF
        ret
