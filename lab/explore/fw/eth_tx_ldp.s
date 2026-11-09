; Exploration copy (explore/isa-lanes-table): the 4B/5B tree is replaced by one LDP from a 16-word table.
; Nothing else changed, so the difference measures LDP alone.
;
; 100BASE-TX transmit chain in software: 4B/5B, scrambler and MLT-3. The real rate, 125 MBd, is out of reach: a
; pin can change at most once per 20 ns cycle, and this needs a symbol every 8 ns. So this runs the whole chain
; as fast as the instructions allow, to measure the gap. An MLT-3 step on every 1 is NRZI and MLT-3 combined.
;
; Host pushes 3 nibbles per word, in bits 15:4, and then 0x0001 to end the frame. One nibble per word would cost
; more SPI time than the nibble takes to send. With nothing to send, it sends idle. MLT-3 goes out on two lines:
; POS high for +1, NEG high for -1.
; The scrambler polynomial, x^11 + x^9 + 1, is from memory. No source read in this trial confirmed it.
;
; r0 current word. r1 scrambler. r2 scrambler taps. r3 MLT-3 position, 0 to 3.
.equ BIT, 40
.equ POS, 4
.equ NEG, 5
.equ TAPS, 0x500
        msb     1
        set     POS, 0
        set     NEG, 0
        side    NEG
        mode    POS, eng
        mode    NEG, eng
        ldi     r1, 0x7FF
        ldi     r2, TAPS
        ldi     r3, 0
        tset    BIT
idle:   pull
        jmp     nf, have
        sri     0x1F, 11      ; idle: 11111
        call    send
        jmp     idle
have:   mov     r0, sr
        sri     0x18, 11      ; J: 11000
        call    send
        sri     0x11, 11      ; K: 10001
        call    send
word:   cmpi    r0, 1
        jmp     z, tail
        ldi     r2, 4         ; TO counts nibbles by shifting down: 4, 2, 1, 0
        mov     to, r2
        ldi     r2, TAPS
nib:    mov     sr, r0
        mov     r2, low       ; the top byte, then its top nibble
        shr     r2
        shr     r2
        shr     r2
        shr     r2
        add     r2, code5     ; LDP takes an address: the table's, plus the nibble
        ldp     r2, r2        ; the code group, left aligned
        mov     sr, r2
        ldi     r2, TAPS      ; send needs the taps back
sendn:  call    send
        mov     r2, to
        shr     r2
        mov     to, r2
        cmpi    r2, 0
        ldi     r2, TAPS
        jmp     z, wnext
        shl     r0
        shl     r0
        shl     r0
        shl     r0
        jmp     nib
wnext:  pull
        jmp     f, wnext
        mov     r0, sr
        jmp     word
tail:   sri     0x0D, 11      ; T: 01101
        call    send
        sri     0x07, 11      ; R: 00111
        call    send
        jmp     idle

; 4B/5B, read with LDP. Each entry is the code group shifted to bits 15:11, as SRI loads it
code5:  .word   0x1E << 11, 0x09 << 11, 0x14 << 11, 0x15 << 11, 0x0A << 11, 0x0B << 11, 0x0E << 11, 0x0F << 11
        .word   0x12 << 11, 0x13 << 11, 0x16 << 11, 0x17 << 11, 0x1A << 11, 0x1B << 11, 0x1C << 11, 0x1D << 11

; One 5-bit group, left aligned in SR, one symbol per BIT cycles
send:   bci     5
sb:     tstb
        out     15            ; Z is the data bit
        jmp     z, d1
        shr     r1            ; data 0: sends the scrambler bit
        jmp     f, s0
        nop.t                 ; 0: no change
        jmp     sc
s0:     xor     r1, r2
        jmp     step
d1:     shr     r1            ; data 1: sends the opposite
        jmp     nf, step
        xor     r1, r2
        nop.t
        jmp     sc
step:   add     r3, 1         ; a 1 steps MLT-3 through 0, +1, 0, -1
        cmpi    r3, 4
        jmp     nz, st1
        ldi     r3, 0
st1:    cmpi    r3, 1
        jmp     z, stp
        cmpi    r3, 3
        jmp     z, stn
        set.t   POS, 0 side=0
        jmp     sc
stp:    set.t   POS, 1 side=0
        jmp     sc
stn:    set.t   POS, 0 side=1
sc:     jmp     bcn, sb
        ret
