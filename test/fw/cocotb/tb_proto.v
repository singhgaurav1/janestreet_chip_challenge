// Full-chip top for the firmware protocol tests (cocotb, Icarus). Owned by
// the firmware chat. The chip is a black box behind its pins; the host SPI
// pins (ui_in[7:5], uo_out[7]) and every other pin get their own named
// signal so third-party VIPs can attach to single bits.
`default_nettype none
`timescale 1ns / 1ps

module tb_proto ();
  reg clk = 0, rst_n = 1;   // the test drives a real reset edge (see ../README.md)
  // host SPI (docs/soc.md, info.yaml)
  reg host_sck = 0, host_cs_n = 1, host_mosi = 0;
  wire host_miso;
  // free inputs ui_in[4:0] (pins 0..4), idle high
  reg ui0 = 1, ui1 = 1, ui2 = 1, ui3 = 1, ui4 = 1;
  // uio: external pull-ups on every line (open-drain buses); a VIP can pull low
  reg [7:0] uio_pull_low = 8'h00;
  wire [7:0] uo_out, uio_out, uio_oe;
  wire [7:0] uio_in;
  wire [7:0] ui_in = {host_sck, host_cs_n, host_mosi, ui4, ui3, ui2, ui1, ui0};
  assign host_miso = uo_out[7];
  genvar i;
  generate for (i = 0; i < 8; i = i + 1) begin : g_uio
    assign uio_in[i] = uio_pull_low[i] ? 1'b0 : (uio_oe[i] ? uio_out[i] : 1'b1);
  end endgenerate

  // single-bit views for VIPs
  wire uo0 = uo_out[0], uo1 = uo_out[1], uo2 = uo_out[2], uo3 = uo_out[3];
  wire uo4 = uo_out[4], uo5 = uo_out[5], uo6 = uo_out[6];

  tt_um_singhgaurav1_protoemu dut (
      .ui_in(ui_in), .uo_out(uo_out), .uio_in(uio_in), .uio_out(uio_out), .uio_oe(uio_oe),
      .ena(1'b1), .clk(clk), .rst_n(rst_n));
endmodule
`default_nettype wire
