/*
 * Copyright (c) 2026 singhgaurav1
 * SPDX-License-Identifier: Apache-2.0
 *
 * System control and GPIO, at 0x1000_0000 (docs/soc.md):
 *   0x00 ID       RO  0x50454D55 "PEMU"
 *   0x04 GPIO_OUT RW  [6:0] -> uo_out[6:0]
 *   0x08 GPIO_IN  RO  {uio_in, ui_in} (synchronized)
 *   0x0C UIO_OUT  RW  [7:0]
 *   0x10 UIO_OE   RW  [7:0]
 *   0x14 CYCLE    RO  free-running cycle counter
 *   0x18 SCRATCH  RW  mailbox between firmware and the host
 * Reads return data one cycle after the access, as the bus requires.
 * Writes honour byte enables.
 */
`default_nettype none

module pemu_sysctrl (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        en,
    input  wire        we,
    input  wire [11:0] addr,
    input  wire [31:0] wdata,
    input  wire [3:0]  be,
    output reg  [31:0] rdata,
    input  wire [7:0]  ui_in,
    input  wire [7:0]  uio_in,
    output reg  [6:0]  gpio_out,
    output reg  [7:0]  uio_out,
    output reg  [7:0]  uio_oe
);
  localparam [31:0] ID_VALUE = 32'h50454D55;

  wire [15:0] pins_s;
  cell_sync2 #(.WIDTH(16)) u_sync (.clk(clk), .rst_n(rst_n), .d({uio_in, ui_in}), .q(pins_s));

  reg [31:0] cycle, scratch;
  wire [31:0] bm = {{8{be[3]}}, {8{be[2]}}, {8{be[1]}}, {8{be[0]}}};
  wire wr = en & we;
  wire [3:0] reg_idx = addr[5:2];
  wire in_range = (addr[11:6] == 6'd0);

  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      cycle    <= 32'h0;
      scratch  <= 32'h0;
      gpio_out <= 7'h0;
      uio_out  <= 8'h0;
      uio_oe   <= 8'h0;
      rdata    <= 32'h0;
    end else begin
      cycle <= cycle + 32'd1;
      if (wr && in_range)
        case (reg_idx)
          4'd1: gpio_out <= (gpio_out & ~bm[6:0]) | (wdata[6:0] & bm[6:0]);
          4'd3: uio_out  <= (uio_out & ~bm[7:0]) | (wdata[7:0] & bm[7:0]);
          4'd4: uio_oe   <= (uio_oe & ~bm[7:0]) | (wdata[7:0] & bm[7:0]);
          4'd6: scratch  <= (scratch & ~bm) | (wdata & bm);
          default: ;
        endcase
      if (en && !we)
        case (in_range ? reg_idx : 4'hF)
          4'd0: rdata <= ID_VALUE;
          4'd1: rdata <= {25'h0, gpio_out};
          4'd2: rdata <= {16'h0, pins_s};
          4'd3: rdata <= {24'h0, uio_out};
          4'd4: rdata <= {24'h0, uio_oe};
          4'd5: rdata <= cycle;
          4'd6: rdata <= scratch;
          default: rdata <= 32'h0;
        endcase
    end
endmodule
