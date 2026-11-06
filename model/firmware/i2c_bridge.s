; Transparent I2C: bridge SCL (lines 0 and 4) and SDA (lines 1 and 5). The hardware does the rest.
; Set holdoff (register 3) to more than the bus rise time, and low enough for the bus speed.
        mode    0, odb
        mode    4, odb
        mode    1, odb
        mode    5, odb
        halt
