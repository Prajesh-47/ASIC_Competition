; HDLC-like framing (RFC 1662, bit-synchronous): full duplex at 1 Mbit/s, one direction in each context.
; At half rate that is 25 instructions a bit, for FCS-16, zero insertion and frame handling in software.
;
; Transmit: the first word of a frame is {length, first byte}, and the rest are {byte 2, byte 1}, so the host
; has 16 us per word. Flags fill the gaps, and consecutive frames share one flag. If the host falls behind in
; a frame, the frame is aborted with seven or more ones.
; Receive: L1 is the receive clock, sampling on its rising edge. FIFO gets 0xnn80 per byte, FCS included, then
; a status word: 0x0001 good, 0x0002 bad FCS or not whole bytes, 0x0003 too short, 0x0004 aborted. An empty
; FIFO reads as 0, so a host can read in bursts without checking status first.
.equ BIT, 50
.equ TX, 4
.equ EN, 5
.equ RX, 0
.equ RXCLK, 1
.equ VALID, 6
.equ ERR, 7
.equ POLY, 0x8408

; Transmit. r0 FCS, r1 polynomial, r2 run of ones, r3 scratch.
        msb     0
        set     TX, 1
        set     EN, 0
        mode    TX, eng
        mode    EN, eng
        tset    BIT
flag:   sri     0x7E
        bci     8
fl:     outb.t  TX, fl
        pull                  ; sets F if there's nothing to send
        jmp     f, flag
        set     EN, 1
        mov     r3, low       ; bytes in this frame
        ldi     r0, 0xFFFF
        ldi     r1, POLY
        ldi     r2, 0
        bci     8
        call    send
        djnz    r3, more
        jmp     end
more:   pull
        jmp     f, abort0
        cmpi    r3, 1
        jmp     z, last
        bci     16
        call    send
        djnz    r3, two
two:    djnz    r3, more
        jmp     end
last:   bci     8
        call    send
end:    ldi     r3, 0xFFFF    ; complemented FCS, low byte first, LSB first
        xor     r0, r3
        mov     sr, r0
        bci     16
        ldi     r1, 0         ; no feedback from here
        call    send
        set     EN, 0
        jmp     flag
abort0: sri     0xFF          ; host fell behind: abort
        bci     8
ab:     outb.t  TX, ab
        set     EN, 0
        jmp     flag

send:   tstb
        out.t   TX
        jmp     z, s1         ; Z is the bit just sent
        ldi     r2, 0
        shr     r0
        jmp     nf, sn
        xor     r0, r1
        jmp     sn
s1:     shr     r0
        jmp     f, s2
        xor     r0, r1
s2:     add     r2, 1
        cmpi    r2, 5
        jmp     nz, sn
        set.t   TX, 0         ; five ones: insert a zero
        ldi     r2, 0
sn:     jmp     bcn, send
        ret

; Receive. r0 FCS, r1 polynomial, r2 run of ones, r3 FCS at the last byte boundary, and TO counts bytes
; by shifting 8 down to 0, since no WAIT uses a timeout here.
; A closing flag's first 7 bits enter SR and the FCS before it can be recognised. They never fill a byte,
; so the snapshot in r3 is the FCS without them. That also means nothing is emitted until a byte is whole.
rxs:    msb     0
        ldi     r1, POLY
        set     VALID, 0
        set     ERR, 0
        mode    VALID, eng
        mode    ERR, eng
        tsrc    RXCLK, rise
        ldi     r2, 0
hunt:   probe.t RX
        jmp     z, h1
        cmpi    r2, 6
        jmp     z, open
        ldi     r2, 0
        jmp     hunt
h1:     add     r2, 1
        jmp     hunt

open:   ldi     r0, 0xFFFF    ; a flag just ended, so a frame may start
        ldi     r2, 8
        mov     to, r2
        ldi     r2, 0
        bci     8
        sri     0x80, 8
next:   probe.t RX
        jmp     z, one
        cmpi    r2, 5         ; five ones, then this 0: inserted
        jmp     z, drop
        cmpi    r2, 6         ; six ones, then this 0: flag
        jmp     z, close
        ldi     r2, 0
        in      RX
        shr     r0
        jmp     nf, count
        xor     r0, r1
        jmp     count
drop:   ldi     r2, 0
        jmp     next
one:    add     r2, 1
        cmpi    r2, 7
        jmp     z, abort
        in      RX
        shr     r0
        jmp     f, count
        xor     r0, r1
count:  jmp     bcn, next
        mov     r3, r0        ; a whole byte
        push
        sri     0x80, 8
        bci     8
        mov     r1, to        ; r2 stays: a run of ones can cross bytes
        shr     r1
        mov     to, r1
        ldi     r1, POLY
        jmp     next

close:  mov     r2, bc        ; the budget is tight here: the next data bit is already on its way
        cmpi    r2, 1         ; 7 bits of this flag since the last boundary, or the frame isn't whole bytes
        jmp     nz, broken
        mov     r2, to
        cmpi    r2, 8         ; no bytes at all: just flags
        jmp     z, open
        cmpi    r2, 0
        jmp     nz, short
        ldi     r2, 0xF0B8
        cmp     r3, r2
        jmp     nz, broken
        sri     1
        push
        set     ERR, 0
        set     VALID, 1
        jmp     open
broken: sri     2
        push
        set     VALID, 0
        set     ERR, 1
        jmp     open
short:  sri     3
        push
        set     VALID, 0
        set     ERR, 1
        jmp     open
abort:  mov     r1, to        ; idle ones before any byte aren't a frame
        cmpi    r1, 8
        jmp     z, quiet
        sri     4
        push
        set     VALID, 0
        set     ERR, 1
quiet:  ldi     r1, POLY
        ldi     r2, 7         ; still more than six, so the 0 ending this run isn't a flag
        jmp     hunt
.ctx1 rxs
