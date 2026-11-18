/*
 * Copyright (c) 2026 singhgaurav1
 * SPDX-License-Identifier: Apache-2.0
 *
 * Small synchronous FIFO (flop based) for the Protocol Engine TX/RX paths.
 * One push and one pop per cycle. ALLOW_PUSH_WHEN_POP lets a push into a full
 * FIFO succeed when a pop happens in the same cycle (the CPU side of the TX
 * FIFO relies on it; see docs/pe_isa.md section 4 for the ordering rules).
 * A rejected push is dropped and reported on push_drop.
 */
`default_nettype none

module pe_fifo #(
    parameter integer WIDTH = 32,
    parameter integer DEPTH = 4,            // power of two
    parameter integer AW    = 2,            // log2(DEPTH)
    parameter         ALLOW_PUSH_WHEN_POP = 1
) (
    input  wire             clk,
    input  wire             rst_n,
    input  wire             clear,
    input  wire             push,
    input  wire [WIDTH-1:0] wdata,
    output wire             push_drop,
    input  wire             pop,            // ignored when empty
    output wire [WIDTH-1:0] rdata,          // head, valid when !empty
    output wire [AW:0]      level,
    output wire             empty,
    output wire             full
);
  reg [WIDTH-1:0] mem [0:DEPTH-1];
  reg [AW-1:0]    rd, wr;
  reg [AW:0]      cnt;

  wire do_pop  = pop & (cnt != 0);
  wire room    = (cnt != DEPTH[AW:0]) | (ALLOW_PUSH_WHEN_POP != 0 && do_pop);
  wire do_push = push & room;

  assign push_drop = push & ~room;
  assign rdata = mem[rd];
  assign level = cnt;
  assign empty = (cnt == 0);
  assign full  = (cnt == DEPTH[AW:0]);

  always @(posedge clk) if (do_push) mem[wr] <= wdata;

  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      rd  <= {AW{1'b0}};
      wr  <= {AW{1'b0}};
      cnt <= {(AW + 1){1'b0}};
    end else if (clear) begin
      rd  <= {AW{1'b0}};
      wr  <= {AW{1'b0}};
      cnt <= {(AW + 1){1'b0}};
    end else begin
      if (do_push) wr <= wr + 1'b1;
      if (do_pop) rd <= rd + 1'b1;
      cnt <= cnt + {{AW{1'b0}}, do_push} - {{AW{1'b0}}, do_pop};
    end
endmodule
