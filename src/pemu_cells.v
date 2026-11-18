/*
 * Copyright (c) 2026 singhgaurav1
 * SPDX-License-Identifier: Apache-2.0
 *
 * Technology wrappers. Every place the design needs a specific library cell
 * goes through one of these modules, so the rest of the RTL stays portable.
 *
 *   PROTOEMU_ASIC defined (LibreLane, see src/config.json): IHP SG13CMOS5L cells.
 *   Otherwise (simulation, equivalence proof, FPGA): generic behavioural RTL.
 */
`default_nettype none

// Glitch-free clock gate: clk_o = clk_i while (en_i | test_en_i), latched
// while clk_i is low. The ASIC version is the integrated clock-gate cell, so
// CTS and STA treat it as a clock gate (setup/hold checks on GATE).
module cell_clock_gate (
    input  wire clk_i,
    input  wire en_i,
    input  wire test_en_i,
    output wire clk_o
);
`ifdef PROTOEMU_ASIC
  sg13cmos5l_slgcp_1 u_icg (
      .CLK (clk_i),
      .GATE(en_i),
      .SCE (test_en_i),
      .GCLK(clk_o)
  );
`else
  reg en_latch;
  /* verilator lint_off LATCH */
  always @* if (!clk_i) en_latch = en_i | test_en_i;
  /* verilator lint_on LATCH */
  assign clk_o = en_latch & clk_i;
`endif
endmodule

// Two-flop synchronizer for asynchronous inputs (pads, host SPI).
module cell_sync2 #(
    parameter integer WIDTH = 1,
    parameter [WIDTH-1:0] RESET_VALUE = {WIDTH{1'b0}}
) (
    input  wire             clk,
    input  wire             rst_n,
    input  wire [WIDTH-1:0] d,
    output wire [WIDTH-1:0] q
);
  reg [WIDTH-1:0] s1, s2;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      s1 <= RESET_VALUE;
      s2 <= RESET_VALUE;
    end else begin
      s1 <= d;
      s2 <= s1;
    end
  assign q = s2;
endmodule

// Reset synchronizer: asserts asynchronously, releases synchronously.
module pemu_reset_sync (
    input  wire clk,
    input  wire rst_n_async,
    output wire rst_n
);
  reg [1:0] r;
  always @(posedge clk or negedge rst_n_async)
    if (!rst_n_async) r <= 2'b00;
    else r <= {r[0], 1'b1};
  assign rst_n = r[1];
endmodule
