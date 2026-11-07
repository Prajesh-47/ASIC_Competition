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
        cmpi    r2, 0x1e
        jmp     z, v0
        cmpi    r2, 0x9
        jmp     z, v1
        cmpi    r2, 0x14
        jmp     z, v2
        cmpi    r2, 0x15
        jmp     z, v3
        cmpi    r2, 0xa
        jmp     z, v4
        cmpi    r2, 0xb
        jmp     z, v5
        cmpi    r2, 0xe
        jmp     z, v6
        cmpi    r2, 0xf
        jmp     z, v7
        cmpi    r2, 0x12
        jmp     z, v8
        cmpi    r2, 0x13
        jmp     z, v9
        cmpi    r2, 0x16
        jmp     z, va
        cmpi    r2, 0x17
        jmp     z, vb
        cmpi    r2, 0x1a
        jmp     z, vc
        cmpi    r2, 0x1b
        jmp     z, vd
        cmpi    r2, 0x1c
        jmp     z, ve
        cmpi    r2, 0x1d
        jmp     z, vf
        cmpi    r2, 0x0D      ; T
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
v0:     ldi     r3, 0
        jmp     got
v1:     ldi     r3, 1
        jmp     got
v2:     ldi     r3, 2
        jmp     got
v3:     ldi     r3, 3
        jmp     got
v4:     ldi     r3, 4
        jmp     got
v5:     ldi     r3, 5
        jmp     got
v6:     ldi     r3, 6
        jmp     got
v7:     ldi     r3, 7
        jmp     got
v8:     ldi     r3, 8
        jmp     got
v9:     ldi     r3, 9
        jmp     got
va:     ldi     r3, 10
        jmp     got
vb:     ldi     r3, 11
        jmp     got
vc:     ldi     r3, 12
        jmp     got
vd:     ldi     r3, 13
        jmp     got
ve:     ldi     r3, 14
        jmp     got
vf:     ldi     r3, 15
        jmp     got
