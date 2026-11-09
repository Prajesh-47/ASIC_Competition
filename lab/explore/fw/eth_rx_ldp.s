; Exploration copy (explore/isa-lanes-table): the chain of 17 compares that decodes a code group is replaced
; by one LDP from a 32-word table. Nothing else changed, so the difference measures LDP alone.
;
; 100BASE-X PCS receive: finds J/K, decodes 4B/5B, spots T/R and invalid groups. This is the code-group layer
; only: the input is the code-group stream with its bit clock on L1, with no scrambling or MLT-3. Used at the
; highest clock that works, to measure the gap against 125 MBd.
; FIFO: 0x00n0 + nibble for data, 0x0200 for a clean end of stream, 0x0100 for an invalid code group.
; Pins: RXV high inside a frame, RXE high after an invalid group.
.equ RX, 0
.equ RXCLK, 1
.equ RXV, 6
.equ RXE, 7
        set     RXV, 0
        set     RXE, 0
        mode    RXV, eng
        mode    RXE, eng
        tsrc    RXCLK, rise
hunt:   set     RXV, 0
        ldi     r0, 0x3FF
hb:     probe.t RX
        shl     r0            ; doesn't touch Z
        jmp     nz, h0
        add     r0, 1
h0:     mov     r2, r0
        ldi     r3, 0x3FF
        and     r2, r3
        ldi     r3, 0x311     ; J then K
        cmp     r2, r3
        jmp     nz, hb
        set     RXV, 1
        set     RXE, 0
group:  ldi     r1, 5
gb:     probe.t RX
        shl     r0
        jmp     nz, g0
        add     r0, 1
g0:     djnz    r1, gb
        mov     r2, r0
        ldi     r3, 0x1F
        and     r2, r3
        add     r2, dec5      ; LDP takes an address: the table's, plus the group
        ldp     r3, r2        ; data: the nibble. T: 0x10. Anything else: 0x20
        ldi     r1, 0x30
        and     r1, r3
        jmp     z, got
        cmpi    r3, 0x10
        jmp     z, term
        set     RXE, 1        ; anything else in a frame is invalid
        sri     1, 8
        push
        jmp     hunt
got:    mov     sr, r3
        push
        jmp     group
term:   ldi     r1, 5
tb:     probe.t RX
        shl     r0
        jmp     nz, t0
        add     r0, 1
t0:     djnz    r1, tb
        mov     r2, r0
        ldi     r3, 0x1F
        and     r2, r3
        cmpi    r2, 0x07      ; R
        jmp     nz, badr
        sri     2, 8
        push
        jmp     hunt
badr:   set     RXE, 1
        sri     1, 8
        push
        jmp     hunt
; 5B/4B: indexed by the 5-bit code group
dec5:   .word   0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20   ; 00-07: 0x07 is R, not data
        .word   0x20, 0x01, 0x04, 0x05, 0x20, 0x10, 0x06, 0x07   ; 08-0F: 0x0D is T
        .word   0x20, 0x20, 0x08, 0x09, 0x02, 0x03, 0x0A, 0x0B   ; 10-17
        .word   0x20, 0x20, 0x0C, 0x0D, 0x0E, 0x0F, 0x00, 0x20   ; 18-1F
