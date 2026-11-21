/*
 * Copyright (c) 2026 singhgaurav1
 * SPDX-License-Identifier: Apache-2.0
 *
 * Protocol emulator SoC: Ibex (RV32IMC) + 4 KiB SRAM + system control/GPIO
 * + host SPI bridge + Protocol Engine block (0x1000_1000). Address map and
 * pins: docs/soc.md.
 *
 * Pads (docs/pe_isa.md 2.2): 0-7 = ui_in, 8-15 = uio, 16-23 = uo_out. Each
 * driveable pad (8-23) has an owner in the PE block. Owner "system" (the
 * reset value) means the SoC drives it: pad 23 from the host bridge (MISO),
 * pads 8-22 from the sysctrl GPIO registers. Engines and the PE block's CPU
 * GPIO drive the pads firmware assigns to them.
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
    output wire [7:0] uo_out,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe
);
  localparam [3:0] MUBI_ON = 4'b0101, MUBI_OFF = 4'b1010;

  // ---- host bridge --------------------------------------------------------
  wire        cpu_run, cpu_sleep;
  wire        host_miso_int;
  wire [11:0] pe_irq;              // PE block interrupt lines
  wire        h_req, h_we, h_gnt, h_rvalid, h_err;
  wire [31:0] h_addr, h_wdata, h_rdata;

  pemu_host_spi u_host (
      .clk(clk), .rst_n(rst_n),
      .sck(host_sck), .cs_n(host_cs_n), .mosi(host_mosi), .miso(host_miso_int),
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
      .irq_software_i(1'b0), .irq_timer_i(1'b0), .irq_external_i(1'b0),
      .irq_fast_i({3'b000, pe_irq}),       // IDs 16-23 event flags, 24-27 engine FIFOs
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
  wire [6:0]  sys_gpio_out;               // sysctrl GPIO: drives system-owned pads
  wire [7:0]  sys_uio_out, sys_uio_oe;

  // A window with no peripheral answers with a bus error.
  wire        sys_sel = per_addr[15:12] == 4'h0;
  wire        pe_sel  = per_addr[15:12] == 4'h1;
  wire [31:0] sys_rdata, pe_rdata;
  wire        sys_err, pe_err;
  reg  [1:0]  sel_q;                       // {pe, sys} of the access last cycle
  always @(posedge clk or negedge rst_n)
    if (!rst_n) sel_q <= 2'b00;
    else if (per_en) sel_q <= {pe_sel, sys_sel};
  assign per_rdata = sel_q[0] ? sys_rdata : sel_q[1] ? pe_rdata : 32'h0;
  assign per_err   = sel_q[0] ? sys_err   : sel_q[1] ? pe_err   : 1'b1;

  pemu_sysctrl u_sys (
      .clk(clk), .rst_n(rst_n),
      .en(per_en & sys_sel), .we(per_we), .addr(per_addr[11:0]), .wdata(per_wdata),
      .be(per_be), .rdata(sys_rdata), .err(sys_err),
      .ui_in(ui_in), .uio_in(uio_in),
      .gpio_out(sys_gpio_out), .uio_out(sys_uio_out), .uio_oe(sys_uio_oe)
  );

  // ---- Protocol Engine block (docs/pe_isa.md) -------------------------------
  wire [7:0]  pe_uo_out, pe_uio_out, pe_uio_oe, pe_flags;
  wire [15:0] pad_sys;                     // pads 8..23 owned by the system

  pe_block #(.OWNER_RESET({16{3'd5}})) u_pe (
      .clk(clk), .rst_n(rst_n),
      .en(per_en & pe_sel), .we(per_we), .addr(per_addr[11:0]), .wdata(per_wdata),
      .rdata(pe_rdata), .err(pe_err),
      .ui_in(ui_in), .uio_in(uio_in),
      .uo_out(pe_uo_out), .uio_out(pe_uio_out), .uio_oe(pe_uio_oe),
      .pad_sys(pad_sys), .flags_out(pe_flags), .irq(pe_irq)
  );

  // ---- pad multiplex: system-owned pads come from the SoC -------------------
  wire [7:0] sys_uo = {host_miso_int, sys_gpio_out};
  assign uo_out    = (pad_sys[15:8] & sys_uo)      | (~pad_sys[15:8] & pe_uo_out);
  assign uio_out   = (pad_sys[7:0]  & sys_uio_out) | (~pad_sys[7:0]  & pe_uio_out);
  assign uio_oe    = (pad_sys[7:0]  & sys_uio_oe)  | (~pad_sys[7:0]  & pe_uio_oe);
  assign host_miso = host_miso_int;        // also exported for debug benches

  // unused bus status and flags (flags are visible to firmware via EVT)
  wire unused = h_err | (|pe_flags);
endmodule
