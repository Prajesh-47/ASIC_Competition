# Place-and-route constraints: the template's own, plus one extra load the resizer must drive. Signoff
# still reads only the template's (SIGNOFF_SDC_FILE is unset), so the final checks see the real chip.
source $::env(FALLBACK_SDC)

# The capture SRAM's write enable is a ~130 um route to a macro pin, and the routing estimate can't see
# the macro end of it (EST-0026), so the resizer leaves it on an o21ai_1 and it misses the pin's 0.595 ns
# slew limit after detailed routing (gds 44918283875: 0.634 ns). 30 fF is about what 150 um of wire adds.
set_load 0.030 [get_nets -of_objects [get_pins cap_sram/A_WEN]]
