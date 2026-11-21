/*
 * Copyright (c) 2026 singhgaurav1
 * SPDX-License-Identifier: Apache-2.0
 *
 * Tiny Tapeout wrapper: pin assignment only (docs/soc.md).
 *   ui_in[7]  host SCK      ui_in[6]  host CS_N     ui_in[5]  host MOSI
 *   uo_out[7] host MISO     uo_out[6:0] GPIO        uio[7:0]  GPIO (bidirectional)
 * The remaining ui_in pins are general inputs. uo_out and uio go through
 * the Protocol Engine block's pad owners (docs/pe_isa.md 2.2): at reset every
 * pad belongs to the system, i.e. uo_out[7] = MISO and the rest follow the
 * sysctrl GPIO registers.
 */
`default_nettype none

module tt_um_singhgaurav1_protoemu (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,      // always 1 when powered
    input  wire       clk,
    input  wire       rst_n
);
  wire sys_rst_n;
  pemu_reset_sync u_rst (.clk(clk), .rst_n_async(rst_n), .rst_n(sys_rst_n));

  wire host_miso;     // also on uo_out[7] while pad 23 belongs to the system

  pemu_soc u_soc (
      .clk(clk), .rst_n(sys_rst_n),
      .host_sck(ui_in[7]), .host_cs_n(ui_in[6]), .host_mosi(ui_in[5]), .host_miso(host_miso),
      .ui_in(ui_in), .uio_in(uio_in),
      .uo_out(uo_out), .uio_out(uio_out), .uio_oe(uio_oe)
  );

  wire _unused = ena | host_miso;
endmodule
