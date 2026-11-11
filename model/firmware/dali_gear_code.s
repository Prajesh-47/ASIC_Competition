; DALI control gear with the line coder (explore/isa-code-rx): Manchester receive with clock recovery, and
; Manchester transmit for the reply. Same behaviour and FIFO format as dali_gear.s: every well-formed frame,
; then 0x0100 if it was addressed to us, or 0x0300 for a malformed frame. Answers 0xFF (YES) to QUERY when
; addressed. Needs L open-drain (register 1).
;
; One tick per half bit. CODE sync re-phases the timer at every edge, so ticks land a quarter bit from the
; edges, and CODE man takes a bit from each pair of halves. A pair with no change breaks the code and sets F:
; a missing edge inside the frame, or the idle line after it, which is the stop condition.
.equ TE, 20833
.equ L, 4
.equ ADDR, 5                  ; short address
.equ GROUP, 3
.equ QUERY, 0x91
.equ REPLY, 0xFF
        msb     1
        set     L, 1
        mode    L, eng
        tsrc    L, off        ; the period timer, re-phased by edges on L
        ldi     r3, TE
        tset    r3            ; one tick per half bit
next:   code    off
        ldi     r3, 0
        shr     r3            ; clear F, so it shows only a broken code
        wait    L, fall       ; the start bit's first half
        tphase  half          ; the first tick a quarter bit in
        code    man, sync     ; IEEE: a 1 is low, then high
        bci     17
rb:     inb.t   L, rb         ; the start bit, then 16; the start bit falls out of SR
        jmp     f, bad
        mov     r0, sr
        in.t    L             ; the first stop bit: no change, so F
        jmp     nf, bad

        mov     sr, r0
        push
        mov     r1, low       ; address byte
        shr     r1
        cmpi    r1, 0x7F      ; broadcast
        jmp     z, hit
        cmpi    r1, 0x40+GROUP
        jmp     z, hit
        cmpi    r1, ADDR
        jmp     nz, next
hit:    sri     1, 8
        push
        ldi     r2, 0xFF
        and     r0, r2
        cmpi    r0, QUERY
        jmp     nz, next

        code    man           ; transmit now, without the clock recovery
        ldi     r2, 10        ; the reply starts 8.5 Te after the stop bits, with a margin
gap:    nop.t
        djnz    r2, gap
        sri     REPLY, 8      ; left aligned: MSB first
        set.t   L, 0          ; start bit
        set.t   L, 1
        bci     8
rp:     outb.t  L, rp
        set.t   L, 1
        jmp     next
bad:    sri     3, 8
        push
        jmp     next
