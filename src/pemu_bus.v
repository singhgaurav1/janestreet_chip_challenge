/*
 * Copyright (c) 2026 singhgaurav1
 * SPDX-License-Identifier: Apache-2.0
 *
 * System bus: Ibex-style req/gnt/rvalid, three masters, two targets.
 *
 *   masters (priority high -> low): m0 host bridge, m1 CPU data, m2 CPU instruction
 *   0x0xxx_xxxx  SRAM        (word address = addr[SRAM_AW+1:2], aliases above its size)
 *   0x1xxx_xxxx  peripherals (addr[15:0] to the peripheral block)
 *   other        no target: granted, answered with rdata 0 and err
 *
 * Every target answers exactly one cycle after the grant, so each master can
 * have a request in flight every cycle and responses never need reordering.
 * Instruction fetches may only target SRAM.
 */
`default_nettype none

module pemu_bus #(
    parameter integer SRAM_AW = 9
) (
    input  wire               clk,
    input  wire               rst_n,
    // m0: host bridge
    input  wire               m0_req,
    input  wire [31:0]        m0_addr,
    input  wire               m0_we,
    input  wire [3:0]         m0_be,
    input  wire [31:0]        m0_wdata,
    output wire               m0_gnt,
    output reg                m0_rvalid,
    output wire               m0_err,
    output wire [31:0]        m0_rdata,
    // m1: CPU data
    input  wire               m1_req,
    input  wire [31:0]        m1_addr,
    input  wire               m1_we,
    input  wire [3:0]         m1_be,
    input  wire [31:0]        m1_wdata,
    output wire               m1_gnt,
    output reg                m1_rvalid,
    output wire               m1_err,
    output wire [31:0]        m1_rdata,
    // m2: CPU instruction fetch (read only)
    input  wire               m2_req,
    input  wire [31:0]        m2_addr,
    output wire               m2_gnt,
    output reg                m2_rvalid,
    output wire               m2_err,
    output wire [31:0]        m2_rdata,
    // SRAM target (synchronous, one-cycle read)
    output wire               sram_en,
    output wire               sram_we,
    output wire [SRAM_AW-1:0] sram_addr,
    output wire [31:0]        sram_wdata,
    output wire [3:0]         sram_be,
    input  wire [31:0]        sram_rdata,
    // peripheral target (registered read data, one cycle)
    output wire               per_en,
    output wire               per_we,
    output wire [15:0]        per_addr,
    output wire [31:0]        per_wdata,
    output wire [3:0]         per_be,
    input  wire [31:0]        per_rdata
);
  localparam [1:0] T_SRAM = 2'd0, T_PER = 2'd1, T_NONE = 2'd2;

  function [1:0] target;
    input [31:0] a;
    target = (a[31:28] == 4'h0) ? T_SRAM : (a[31:28] == 4'h1) ? T_PER : T_NONE;
  endfunction

  wire [1:0] t0 = target(m0_addr);
  wire [1:0] t1 = target(m1_addr);
  wire [1:0] t2 = (target(m2_addr) == T_SRAM) ? T_SRAM : T_NONE;

  // ---- arbitration: fixed priority per target --------------------------
  wire s0 = m0_req & (t0 == T_SRAM);
  wire s1 = m1_req & (t1 == T_SRAM) & ~s0;
  wire s2 = m2_req & (t2 == T_SRAM) & ~s0 & ~(m1_req & (t1 == T_SRAM));
  wire p0 = m0_req & (t0 == T_PER);
  wire p1 = m1_req & (t1 == T_PER) & ~p0;

  assign m0_gnt = s0 | p0 | (m0_req & (t0 == T_NONE));
  assign m1_gnt = s1 | p1 | (m1_req & (t1 == T_NONE));
  assign m2_gnt = s2 | (m2_req & (t2 == T_NONE));

  // ---- SRAM port --------------------------------------------------------
  assign sram_en    = s0 | s1 | s2;
  assign sram_we    = s0 ? m0_we : s1 ? m1_we : 1'b0;
  wire [31:0] sa    = s0 ? m0_addr : s1 ? m1_addr : m2_addr;
  assign sram_addr  = sa[SRAM_AW+1:2];
  assign sram_wdata = s0 ? m0_wdata : m1_wdata;
  assign sram_be    = s0 ? m0_be : s1 ? m1_be : 4'hF;

  // ---- peripheral port --------------------------------------------------
  assign per_en    = p0 | p1;
  assign per_we    = p0 ? m0_we : m1_we;
  assign per_addr  = p0 ? m0_addr[15:0] : m1_addr[15:0];
  assign per_wdata = p0 ? m0_wdata : m1_wdata;
  assign per_be    = p0 ? m0_be : m1_be;

  // ---- responses: one cycle after the grant -----------------------------
  reg [1:0] r0, r1, r2;   // target each master was granted last cycle
  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      {m0_rvalid, m1_rvalid, m2_rvalid} <= 3'b000;
      r0 <= T_NONE;
      r1 <= T_NONE;
      r2 <= T_NONE;
    end else begin
      m0_rvalid <= m0_gnt;
      m1_rvalid <= m1_gnt;
      m2_rvalid <= m2_gnt;
      r0 <= t0;
      r1 <= t1;
      r2 <= t2;
    end

  // Explicit muxes (not a function reading module signals: a continuous
  // assignment would only re-evaluate when the function's arguments change).
  assign m0_rdata = (r0 == T_SRAM) ? sram_rdata : (r0 == T_PER) ? per_rdata : 32'h0;
  assign m1_rdata = (r1 == T_SRAM) ? sram_rdata : (r1 == T_PER) ? per_rdata : 32'h0;
  assign m2_rdata = (r2 == T_SRAM) ? sram_rdata : 32'h0;
  assign m0_err   = m0_rvalid & (r0 == T_NONE);
  assign m1_err   = m1_rvalid & (r1 == T_NONE);
  assign m2_err   = m2_rvalid & (r2 == T_NONE);
endmodule
