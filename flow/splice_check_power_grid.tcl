# SPDX-FileCopyrightText: © 2026 Prajesh-47
# SPDX-License-Identifier: Apache-2.0
#
# Check power grid connectivity after flow/splice_power_columns.py has drawn the
# macro supply columns. OpenROAD.GeneratePDN runs the same check before those
# columns exist, so it always reports the macros' supply pins as unconnected.
# Writes one <net>-grid-errors.rpt per supply net; librelane_plugin_splice.py
# counts the violations in them.

source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
read_current_odb

source $::env(SCRIPTS_DIR)/openroad/common/set_power_nets.tcl

foreach net "$::env(VDD_NETS) $::env(GND_NETS)" {
    set report_file $::env(STEP_DIR)/$net-grid-errors.rpt
    set f [open $report_file "w"]
    close $f

    if { [catch {check_power_grid -net $net -error_file $report_file} err] } {
        puts "\[WARNING\] Grid check for $net failed: $err"
        # A failed check that wrote no violations must still count as one.
        if { [file size $report_file] == 0 } {
            set f [open $report_file "w"]
            puts $f "violation type: check_power_grid failed"
            puts $f "\tsrcs: $err"
            close $f
        }
    }
}
