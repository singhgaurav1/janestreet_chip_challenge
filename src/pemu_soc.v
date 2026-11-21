/*
 * Copyright (c) 2026 singhgaurav1
 * SPDX-License-Identifier: Apache-2.0
 *
 * Protocol emulator SoC (skeleton): Ibex (RV32IMC) + 4 KiB SRAM + system
 * control/GPIO + host SPI bridge. The Protocol Engine block attaches at
 * 0x1000_1000 in a later step. Address map and pins: docs/soc.md.
 *
 * Boot: after reset the CPU is held in reset. The host loads the firmware
 * over SPI, then sets CTRL.run. Ibex starts at 0x80 (boot_addr 0).
 */
`default_nettype none

module pemu_soc (
    input  wire       clk,
    input  wire       rst_n,        // synchronized system reset
    // host SPI
    input  wire       host_sck,
    input  wire       host_cs_n,
    input  wire       host_mosi,
    output wire       host_miso,
    // GPIO
    input  wire [7:0] ui_in,
    input  wire [7:0] uio_in,
    output wire [6:0] gpio_out,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe
);
  localparam [3:0] MUBI_ON = 4'b0101, MUBI_OFF = 4'b1010;

  // ---- host bridge --------------------------------------------------------
  wire        cpu_run, cpu_sleep;
  wire        h_req, h_we, h_gnt, h_rvalid, h_err;
  wire [31:0] h_addr, h_wdata, h_rdata;

  pemu_host_spi u_host (
      .clk(clk), .rst_n(rst_n),
      .sck(host_sck), .cs_n(host_cs_n), .mosi(host_mosi), .miso(host_miso),
      .cpu_run(cpu_run), .cpu_sleep(cpu_sleep),
      .bus_req(h_req), .bus_addr(h_addr), .bus_we(h_we), .bus_wdata(h_wdata),
      .bus_gnt(h_gnt), .bus_rvalid(h_rvalid), .bus_rdata(h_rdata)
  );

  // CPU reset: held while the host has not set CTRL.run; released synchronously.
  reg cpu_rst_n;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) cpu_rst_n <= 1'b0;
    else cpu_rst_n <= cpu_run;

  // ---- CPU ------------------------------------------------------------------
  wire        d_req, d_we, d_gnt, d_rvalid, d_err;
  wire [3:0]  d_be;
  wire [31:0] d_addr, d_wdata, d_rdata;
  wire        i_req, i_gnt, i_rvalid, i_err;
  wire [31:0] i_addr, i_rdata;

  ibex_small_top u_cpu (
      .clk_i(clk), .rst_ni(cpu_rst_n), .test_en_i(1'b0), .scan_rst_ni(1'b1),
      .ram_cfg_icache_tag_i(24'h0), .ram_cfg_icache_data_i(24'h0),
      .ram_cfg_icache_tag_o(), .ram_cfg_icache_data_o(),
      .cheriot_enable_i(MUBI_OFF), .hart_id_i(32'h0), .boot_addr_i(32'h0),
      .trvk_heap_base_addr_i(32'h0),
      .instr_req_o(i_req), .instr_gnt_i(i_gnt), .instr_rvalid_i(i_rvalid),
      .instr_addr_o(i_addr), .instr_rdata_i(i_rdata), .instr_rdata_intg_i(7'h0),
      .instr_err_i(i_err),
      .data_req_o(d_req), .data_gnt_i(d_gnt), .data_rvalid_i(d_rvalid),
      .data_we_o(d_we), .data_be_o(d_be), .data_addr_o(d_addr), .data_wdata_o(d_wdata),
      .data_wdata_intg_o(), .data_rdata_i(d_rdata), .data_rdata_intg_i(7'h0), .data_err_i(d_err),
      .data_tag_o(), .data_tag_i(1'b0),
      .trvk_revbm_req_o(), .trvk_revbm_gnt_i(1'b0), .trvk_revbm_rvalid_i(1'b0),
      .trvk_revbm_addr_o(), .trvk_revbm_rdata_i(32'h0), .trvk_revbm_rdata_intg_i(7'h0),
      .trvk_revbm_err_i(1'b0),
      .irq_software_i(1'b0), .irq_timer_i(1'b0), .irq_external_i(1'b0), .irq_fast_i(15'h0),
      .irq_nm_i(1'b0),
      .scramble_key_valid_i(1'b0), .scramble_key_i(128'h0), .scramble_nonce_i(64'h0),
      .scramble_req_o(),
      .debug_req_i(1'b0), .crash_dump_o(), .double_fault_seen_o(),
      .fetch_enable_i(MUBI_ON), .mcounteren_writable_i(MUBI_ON),
      .alert_minor_o(), .alert_major_internal_o(), .alert_major_bus_o(), .core_sleep_o(cpu_sleep),
      .lockstep_cmp_en_o(),
      .instr_req_shadow_o(), .instr_addr_shadow_o(), .data_req_shadow_o(), .data_we_shadow_o(),
      .data_be_shadow_o(), .data_addr_shadow_o(), .data_wdata_shadow_o(),
      .data_wdata_intg_shadow_o()
  );

  // ---- bus ------------------------------------------------------------------
  wire        sram_en, sram_we;
  wire [9:0]  sram_addr;
  wire [3:0]  sram_be;
  wire [31:0] sram_wdata, sram_rdata;
  wire        per_en, per_we;
  wire [15:0] per_addr;
  wire [3:0]  per_be;
  wire [31:0] per_wdata, per_rdata;
  wire        per_err;

  pemu_bus #(.SRAM_AW(10)) u_bus (
      .clk(clk), .rst_n(rst_n),
      .m0_req(h_req), .m0_addr(h_addr), .m0_we(h_we), .m0_be(4'hF), .m0_wdata(h_wdata),
      .m0_gnt(h_gnt), .m0_rvalid(h_rvalid), .m0_err(h_err), .m0_rdata(h_rdata),
      .m1_req(d_req), .m1_addr(d_addr), .m1_we(d_we), .m1_be(d_be), .m1_wdata(d_wdata),
      .m1_gnt(d_gnt), .m1_rvalid(d_rvalid), .m1_err(d_err), .m1_rdata(d_rdata),
      .m2_req(i_req), .m2_addr(i_addr),
      .m2_gnt(i_gnt), .m2_rvalid(i_rvalid), .m2_err(i_err), .m2_rdata(i_rdata),
      .sram_en(sram_en), .sram_we(sram_we), .sram_addr(sram_addr), .sram_wdata(sram_wdata),
      .sram_be(sram_be), .sram_rdata(sram_rdata),
      .per_en(per_en), .per_we(per_we), .per_addr(per_addr), .per_wdata(per_wdata),
      .per_be(per_be), .per_rdata(per_rdata), .per_err(per_err)
  );

  pemu_sram u_sram (
      .clk(clk), .en(sram_en), .we(sram_we), .addr(sram_addr), .wdata(sram_wdata),
      .be(sram_be), .rdata(sram_rdata)
  );

  // ---- peripherals: 4 KiB windows selected by per_addr[15:12] ---------------
  // A window with no peripheral answers with a bus error.
  wire        sys_sel = per_addr[15:12] == 4'h0;
  wire [31:0] sys_rdata;
  wire        sys_err;
  reg         sys_sel_q;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) sys_sel_q <= 1'b0;
    else if (per_en) sys_sel_q <= sys_sel;
  assign per_rdata = sys_sel_q ? sys_rdata : 32'h0;
  assign per_err   = sys_sel_q ? sys_err : 1'b1;

  pemu_sysctrl u_sys (
      .clk(clk), .rst_n(rst_n),
      .en(per_en & sys_sel), .we(per_we), .addr(per_addr[11:0]), .wdata(per_wdata),
      .be(per_be), .rdata(sys_rdata), .err(sys_err),
      .ui_in(ui_in), .uio_in(uio_in),
      .gpio_out(gpio_out), .uio_out(uio_out), .uio_oe(uio_oe)
  );

  // unused bus status (kept for the PE block and debug in later steps)
  wire unused = h_err;
endmodule
