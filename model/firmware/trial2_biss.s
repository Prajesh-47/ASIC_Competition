; BiSS-C master, point to point, with line-delay compensation. Second blind trial (lab/explore/trial2.md).
; Two contexts. Host registers: 0 = 1 (two contexts), 4/5 = rx, 20 = 0x04 (rx pulls link 0),
; 21 = 0x01 (the clock context pushes to link 0).
;
; MA on line 0 (push-pull, idle high), SLO on line 1 (input).
; Host request word: bit 15 CDM, bits 12..8 m0 = bits in the first data word (1 to 16),
; bits 3..0 = full 16-bit data words after it. Data bits = position + nE + nW, MSB first.
; Reply: 1 + n words of data, right-aligned in the first, then one status word:
; bit 15 CDS, bit 14 CRC error, bit 13 no Ack or no start bit, bits 12..0 the measured line delay in
; cycles: from the master's 2nd MA rise to the Ack's falling edge on SLO.
;
; Context 0 clocks MA with its own timer. Context 1 measures the delay with TCAP, then sets its timer to
; tick in the middle of each SLO bit as the master sees it: that is the compensation. When the CRC is in,
; it forces MA high just after a rising edge (MODE), and context 0 sees the forced line and stops.
.equ P, 12                  ; MA period in cycles, a multiple of 4 with two contexts. 12 = 4.17 MHz
.equ H, P/2
.equ MA, 0
.equ SLO, 1
.equ ACKMAX, 4000           ; slots to wait for the Ack after the first MA fall
.equ ACKBITS, 40            ; longest processing time (Ack bits) accepted
.equ PH, P+H-8              ; first tick: mid-bit of the 2nd Ack bit. 8 = cycles from WAIT to TPHASE
.equ K, 0x0800              ; CRC register after data + inverted CRC: CRC6 of 111111, left-aligned
.ctx1 rx

; ---------------------------------------------------------------- context 0: MA clock
        msb     1
        set     MA, 1
        mode    MA, eng
        ldi     r1, 60000
        mov     to, r1        ; bound every wait on SLO
next0:  pullw   0             ; request from the host
        push                  ; forwarded to the receiver
        mov     r0, sr
        ldi     r1, 0x8000
        xor     r0, r1        ; bit 15: MA's level during the timeout, the inverse of CDM
        wait    SLO, hi       ; encoder ready
        dly     40            ; receiver arms its edge tick
        tset    H
clk:    set.t   MA, 0
        nop                   ; the pad reads back 3 cycles after a change
        probe   MA
        set.t   MA, 1
        jmp     hi, done      ; MA stayed high: the receiver forced it, the frame is over
        jmp     clk
done:   mov     sr, r0
        out     MA ns         ; MA = not CDM, while still forced high
        mode    MA, eng
        wait    SLO, lo       ; the encoder's timeout
        wait    SLO, hi       ; and ready again
        set     MA, 1
        jmp     next0

; ---------------------------------------------------------------- context 1: receiver
rx:     msb     1
        ldi     r1, 0x0C00
        poly    r1            ; x^6 + x + 1, left-aligned
rxn:    pullw   0             ; the request, from link 0
        mov     bc, low       ; m0, bits 12..8
        mov     r3, sr
        ldi     r1, 15
        and     r3, r1
        add     r3, 1         ; data words
        ldi     r2, 0         ; CRC
        ldi     r1, ACKMAX
        mov     to, r1
        ldi     r1, PH
        tsrc    MA, fall
        waitt                 ; first MA fall, read back from the pad
        tsrc    SLO, fall
        wait    SLO, lo       ; the Ack
        tcap    r0            ; cycles from the first MA fall to the Ack's fall
        tsrc    off
        tset    P
        tphase  r1            ; ticks mid-bit from here: this is the line-delay compensation
        jmp     f, noack
        ldi     r1, ACKBITS
ack:    probe.t SLO
        jmp     hi, sb        ; start bit
        djnz    r1, ack
        jmp     noack
sb:     ldi     r1, 0
        probe.t SLO           ; CDS
        jmp     lo, c0
        ldi     r1, 1
c0:     sri     0
word:   in.t    SLO
b:      crc     r2, msb
        inb.t   SLO, b
        crc     r2, msb
        push
        bci     16
        djnz    r3, word
        bci     6             ; the CRC, inverted on the line
        in.t    SLO
c6:     crc     r2, msb
        inb.t   SLO, c6
        crc     r2, msb
        ldi     r3, (0x10000-P-H) & 0xFFFF
        add     r0, r3        ; delay from the 2nd MA rise, which is P+H after the first fall
        ldi     r3, K
        cmp     r2, r3
        jmp     z, crcok
        ldi     r3, 0x4000
        xor     r0, r3
crcok:  cmpi    r1, 0
        jmp     z, stop
        ldi     r3, 0x8000
        xor     r0, r3
stop:   wait    MA, rise
        mode    MA, f1        ; end the clock just after a rising edge
        mov     sr, r0
        push
        jmp     rxn
noack:  sri     0             ; no Ack or start bit: zero data words, then the status
nw:     push
        djnz    r3, nw
        ldi     r0, 0x2000
        jmp     stop
