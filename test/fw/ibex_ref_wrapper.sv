// Reference model for the firmware testbench: wraps the UPSTREAM Ibex
// SystemVerilog (deps/ibex) under the converted core's module name.
//   default:     same parameters as src/ibex_small.v ("small": RV32IMC,
//                RV32MFast, FF regfile, everything else at its default)
//   IBEX_MICRO:  the "micro" fallback (RV32EC, no M, FF regfile), as in
//                scripts/ibex_flow.py
// Built by `test/fw/build_tb.sh ref` / `ref_micro`.
// Running the same firmware on both models is a differential check of the
// SV-to-Verilog conversion with an independent frontend (Verilator).
module ibex_small_top (
  input  logic clk_i, rst_ni, test_en_i, scan_rst_ni,
  input  logic [23:0] ram_cfg_icache_tag_i, ram_cfg_icache_data_i,
  output logic [1:0]  ram_cfg_icache_tag_o, ram_cfg_icache_data_o,
  input  logic [3:0]  cheriot_enable_i,
  input  logic [31:0] hart_id_i, boot_addr_i, trvk_heap_base_addr_i,
  output logic instr_req_o, input logic instr_gnt_i, instr_rvalid_i,
  output logic [31:0] instr_addr_o, input logic [31:0] instr_rdata_i,
  input  logic [6:0] instr_rdata_intg_i, input logic instr_err_i,
  output logic data_req_o, input logic data_gnt_i, data_rvalid_i,
  output logic data_we_o, output logic [3:0] data_be_o,
  output logic [31:0] data_addr_o, data_wdata_o, output logic [6:0] data_wdata_intg_o,
  output logic data_tag_o, input logic [31:0] data_rdata_i, input logic [6:0] data_rdata_intg_i,
  input  logic data_tag_i, data_err_i,
  output logic trvk_revbm_req_o, input logic trvk_revbm_gnt_i, trvk_revbm_rvalid_i,
  output logic [31:0] trvk_revbm_addr_o, input logic [31:0] trvk_revbm_rdata_i,
  input  logic [6:0] trvk_revbm_rdata_intg_i, input logic trvk_revbm_err_i,
  input  logic irq_software_i, irq_timer_i, irq_external_i, input logic [14:0] irq_fast_i,
  input  logic irq_nm_i,
  input  logic scramble_key_valid_i, input logic [127:0] scramble_key_i,
  input  logic [63:0] scramble_nonce_i, output logic scramble_req_o,
  input  logic debug_req_i, output ibex_pkg::crash_dump_t crash_dump_o,
  output logic double_fault_seen_o,
  input  logic [3:0] fetch_enable_i, mcounteren_writable_i,
  output logic alert_minor_o, alert_major_internal_o, alert_major_bus_o, core_sleep_o,
  output logic [3:0] lockstep_cmp_en_o,
  output logic instr_req_shadow_o, output logic [31:0] instr_addr_shadow_o,
  output logic data_req_shadow_o, data_we_shadow_o, output logic [3:0] data_be_shadow_o,
  output logic [31:0] data_addr_shadow_o, data_wdata_shadow_o,
  output logic [6:0] data_wdata_intg_shadow_o
);
`ifdef IBEX_MICRO
  ibex_top #(.RV32E(1'b1), .RV32M(ibex_pkg::RV32MNone), .RV32B(ibex_pkg::RV32BNone),
             .RegFile(ibex_pkg::RegFileFF)) u_ibex (.*);
`else
  ibex_top #(.RV32E(1'b0), .RV32M(ibex_pkg::RV32MFast), .RV32B(ibex_pkg::RV32BNone),
             .RegFile(ibex_pkg::RegFileFF)) u_ibex (.*);
`endif
endmodule
