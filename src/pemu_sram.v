/*
 * Copyright (c) 2026 singhgaurav1
 * SPDX-License-Identifier: Apache-2.0
 *
 * Firmware SRAM: one IHP RM_IHPSG13_1P_1024x32 macro (4 KiB), synchronous
 * one-cycle read, byte enables. The memory is only enabled on an access
 * (A_MEN), so it draws no dynamic power while idle. BIST port tied off.
 */
`default_nettype none

module pemu_sram (
    input  wire        clk,
    input  wire        en,
    input  wire        we,
    input  wire [9:0]  addr,
    input  wire [31:0] wdata,
    input  wire [3:0]  be,
    output wire [31:0] rdata
);
  wire [31:0] bm = {{8{be[3]}}, {8{be[2]}}, {8{be[1]}}, {8{be[0]}}};

  RM_IHPSG13_1P_1024x32_c2_bm_bist u_mem (
      .A_CLK      (clk),
      .A_MEN      (en),
      .A_WEN      (en & we),
      .A_REN      (en & ~we),
      .A_ADDR     (addr),
      .A_DIN      (wdata),
      .A_DLY      (1'b1),          // datasheet: must be tied high
      .A_DOUT     (rdata),
      .A_BM       (bm),
      .A_BIST_CLK (1'b0),
      .A_BIST_EN  (1'b0),
      .A_BIST_MEN (1'b0),
      .A_BIST_WEN (1'b0),
      .A_BIST_REN (1'b0),
      .A_BIST_ADDR(10'h0),
      .A_BIST_DIN (32'h0),
      .A_BIST_BM  (32'h0)
  );
endmodule
