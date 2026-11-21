/*
 * Copyright (c) 2026 singhgaurav1
 * SPDX-License-Identifier: Apache-2.0
 *
 * Protocol Engine block: four pe_engine instances, the shared 64 x 16
 * instruction memory, 8 event flags, the pad registers with their owner
 * table (the pin crossbar), open-drain control, input synchronizers and the
 * CPU register interface (docs/pe_isa.md section 6). Cycle behaviour matches
 * PEBlock in tools/peemu/model.py.
 *
 * CPU map (word accesses, offsets in the block's 4 KiB window; docs/pe_isa.md 6).
 * Offsets not listed answer with err (a bus error on the CPU).
 *   0x100*n + 0x00 CTRL       [0] EN, [1] RESTART (W1), [2] DIV_RESTART (W1)
 *           + 0x04..0x1C      CLKDIV, EXECCTRL, SHIFTCTRL, PINCTRL, ASSIST, CRC_POLY, TIMING
 *           + 0x20 INSTR      W: force-execute; R: instruction at ORIGIN + PC
 *           + 0x24 TXF        W: push (dropped when full: TXOVF)
 *           + 0x28 RXF        R: pop (0 when empty: RXUNF)
 *           + 0x2C STATUS     R; W1C SERR [6], OVF [7], TXOVF [17], RXUNF [18]
 *   0x400 + 4*i  IMEM[i]      i = 0..63, [15:0]
 *   0x500 EVT (RW)  0x504 EVT_SET (W1S)  0x508 EVT_CLR (W1C)
 *   0x50C IRQ_EN       [7:0] flag n, [11:8] engine RX not empty, [15:12] engine TX not full
 *   0x510 ENGINE_CTRL  [3:0] EN (RW), [7:4] RESTART (W1), [11:8] DIV_RESTART (W1)
 *   0x514 SAFE_STATE   W 0x5AFE: disable all engines, release every non-system pad
 *   0x518 PAD_OD       [7:0] pads 8..15 (setting a bit also releases that pad)
 *   0x51C GPIO_OUT     [15:0] pads 8..23 (writes land on CPU-owned pads only)
 *   0x520 GPIO_OE      [7:0] pads 8..15 (CPU-owned pads only)
 *   0x524 PAD_STATE    R: {pad_oe[15:8], pad_out[23:8]}
 *   0x528/0x52C GPIO_OUT_SET/_CLR, 0x530/0x534 GPIO_OE_SET/_CLR, 0x538/0x53C PAD_OD_SET/_CLR
 *   0x540 + 4*(p-8) PAD_OWNER[p]  p = 8..23, [2:0] (0-3 engine, 4 CPU, 5 system, 6/7 none)
 * A pad with owner none is released: output 0, output enable off.
 */
`default_nettype none

module pe_block #(
    // reset owner of pads 8..23 (3 bits each, pad 8 in [2:0]); default all none
    parameter [47:0] OWNER_RESET = {16{3'd7}}
) (
    input  wire        clk,
    input  wire        rst_n,
    // CPU register port (one-cycle registered read data)
    input  wire        en,
    input  wire        we,
    input  wire [11:0] addr,
    input  wire [31:0] wdata,
    output reg  [31:0] rdata,
    output reg         err,          // with rdata: the access hit no register
    // pads
    input  wire [7:0]  ui_in,
    input  wire [7:0]  uio_in,
    output wire [7:0]  uo_out,
    output wire [7:0]  uio_out,
    output wire [7:0]  uio_oe,
    output wire [15:0] pad_sys,      // pads 8..23 owned by "system" (owner 5)
    output wire [7:0]  flags_out,
    output wire [11:0] irq           // [7:0] flags, [11:8] engine FIFOs (docs/pe_isa.md 6)
);
  // ---- reset values = EngineConfig() defaults (tools/peemu/config.py) ------
  localparam [31:0] RST_CLKDIV = 32'h0001_0000, RST_EXECCTRL = 32'h0000_03E0,
                    RST_SHIFTCTRL = 32'h0000_0003, RST_PINCTRL = 32'h0,
                    RST_ASSIST = 32'h0001_E140, RST_CRC_POLY = 32'h0, RST_TIMING = 32'h0;

  integer i;
  genvar g;

  wire wr = en & we;
  wire rd = en & ~we;
  wire is_eng = (addr[11:10] == 2'b00);          // 0x000 .. 0x3FF
  wire [1:0] eng_sel = addr[9:8];
  wire [5:0] eng_reg = addr[7:2];

  // ---- per-engine registers ----------------------------------------------
  // Only the bits defined in docs/pe_isa.md section 6 are stored; the rest
  // read as 0 (constant flops are removed in synthesis).
  localparam [31:0] M_CLKDIV = 32'hFFFF_FF00, M_EXEC   = 32'h3FFF_FFFF,
                    M_SHIFT  = 32'h0000_3FFF, M_PIN    = 32'h1FFF_FFFF,
                    M_ASSIST = 32'h01FF_FFFF;
  reg [31:0] r_clkdiv [0:3], r_exec [0:3], r_shift [0:3], r_pin [0:3];
  reg [31:0] r_assist [0:3], r_poly [0:3], r_timing [0:3];
  reg [3:0]  r_en;
  reg [3:0]  txovf, rxunf;           // sticky CPU-side FIFO errors (STATUS[17], [18])

  // ---- shared state ------------------------------------------------------
  wire [15:0] imem [0:63];
  reg [7:0]  flags;
  reg [23:8] pad_out;
  reg [15:8] pad_oe;
  reg [2:0]  owner [8:23];
  reg [15:8] od;
  reg [15:0] sync1, sync2;
  reg [15:0] irq_en;

  // ---- shared register decode ----------------------------------------------
  wire is_imem   = (addr[11:8] == 4'h4);
  wire is_owner  = (addr[11:6] == 6'b0101_01);             // 0x540 .. 0x57C
  wire [4:0] own_pad = {1'b0, addr[5:2]} + 5'd8;
  localparam [15:0] SAFE_KEY = 16'h5AFE;
  wire sh_hit = (addr == 12'h500) || (addr == 12'h504) || (addr == 12'h508) ||
                (addr == 12'h50C) || (addr == 12'h510) || (addr == 12'h514) ||
                (addr[11:2] >= 10'h146 && addr[11:2] <= 10'h14F) || is_owner;  // 0x518..0x53C
  wire eng_hit = is_eng & (eng_reg <= 6'h0B);
  wire hit = eng_hit | is_imem | sh_hit;
  wire ectl_wr = wr & (addr == 12'h510);

  wire [31:0] pins = {8'h00, pad_out[23:16], sync2};

  // ---- instruction memory: 64 x 16 flops, one clock gate per word ---------
  // A write enables exactly one word's clock, so the word flops need no
  // enable mux and the array draws no clock power when idle.
  wire imem_wr = wr & (addr[11:8] == 4'h4);
  generate
    for (g = 0; g < 64; g = g + 1) begin : gen_imem
      wire       gclk;
      reg [15:0] word;
      cell_clock_gate u_cg (
          .clk_i(clk), .en_i(imem_wr & (addr[7:2] == g)), .test_en_i(1'b0), .clk_o(gclk)
      );
      always @(posedge gclk) word <= wdata[15:0];
      assign imem[g] = word;
    end
  endgenerate

  // ---- engines -----------------------------------------------------------
  wire [31:0] e_out_we [0:3], e_out_val [0:3], e_oe_we [0:3], e_oe_val [0:3];
  wire [7:0]  e_fset [0:3], e_fclr [0:3];
  wire [31:0] e_status [0:3], e_rxf [0:3];
  wire [5:0]  e_iaddr [0:3];
  wire [3:0]  e_drop;

  generate
    for (g = 0; g < 4; g = g + 1) begin : gen_eng
      wire sel = is_eng & (eng_sel == g);
      pe_engine #(.ENGINE_ID(g)) u_eng (
          .clk(clk), .rst_n(rst_n),
          .cfg_clkdiv(r_clkdiv[g]), .cfg_execctrl(r_exec[g]), .cfg_shiftctrl(r_shift[g]),
          .cfg_pinctrl(r_pin[g]), .cfg_assist(r_assist[g]), .cfg_crc_poly(r_poly[g]),
          .cfg_timing(r_timing[g]), .enabled(r_en[g]),
          .cpu_restart((wr & sel & (eng_reg == 6'h00) & wdata[1]) | (ectl_wr & wdata[4+g])),
          .cpu_div_restart((wr & sel & (eng_reg == 6'h00) & wdata[2]) | (ectl_wr & wdata[8+g])),
          .cpu_force_we(wr & sel & (eng_reg == 6'h08)), .cpu_force_instr(wdata[15:0]),
          .cpu_txf_push(wr & sel & (eng_reg == 6'h09)), .cpu_txf_data(wdata), .cpu_txf_drop(e_drop[g]),
          .cpu_rxf_pop(rd & sel & (eng_reg == 6'h0A)), .cpu_rxf_data(e_rxf[g]),
          .cpu_serr_clr(wr & sel & (eng_reg == 6'h0B) & wdata[6]),
          .cpu_ovf_clr(wr & sel & (eng_reg == 6'h0B) & wdata[7]),
          .status(e_status[g]),
          .imem_addr(e_iaddr[g]), .imem_data(imem[e_iaddr[g]]),
          .pins(pins), .flags(flags),
          .out_we(e_out_we[g]), .out_val(e_out_val[g]), .oe_we(e_oe_we[g]), .oe_val(e_oe_val[g]),
          .flag_set(e_fset[g]), .flag_clr(e_fclr[g])
      );
    end
  endgenerate

  // ---- event flags: engines commit, then CPU writes on top ----------------
  wire [7:0] fset = e_fset[0] | e_fset[1] | e_fset[2] | e_fset[3];
  wire [7:0] fclr = e_fclr[0] | e_fclr[1] | e_fclr[2] | e_fclr[3];
  wire [7:0] flags_eng = (flags & ~fclr) | fset;
  assign flags_out = flags;

  // ---- interrupt lines: level, cleared at the source ----------------------
  assign irq[7:0] = flags & irq_en[7:0];
  generate
    for (g = 0; g < 4; g = g + 1) begin : gen_irq
      assign irq[8+g] = (irq_en[8+g] & (e_status[g][15:12] != 4'h0)) |
                        (irq_en[12+g] & ~e_status[g][9]);          // TX level < 2
    end
  endgenerate

  // ---- pad commit: each pad takes writes only from its owner -------------
  reg [23:8] pad_out_n;
  reg [15:8] pad_oe_n;
  integer p;
  always @* begin
    pad_out_n = pad_out;
    pad_oe_n  = pad_oe;
    for (p = 8; p < 24; p = p + 1) begin
      if (owner[p] < 3'd4) begin
        if (e_out_we[owner[p][1:0]][p]) pad_out_n[p] = e_out_val[owner[p][1:0]][p];
        if (p < 16 && e_oe_we[owner[p][1:0]][p]) pad_oe_n[p] = e_oe_val[owner[p][1:0]][p];
      end
    end
  end

  // ---- CPU pad writes: called only from the clocked block below, so reading
  // owner/od there is safe (no combinational sensitivity involved) ----------
  // Enabling open drain on a pad presets its level to 1 (released), so a pad
  // switched to open drain never pulls low by surprise.
  task od_write(input [7:0] v);
    integer k;
    begin
      od <= v;
      for (k = 0; k < 8; k = k + 1) if (v[k] && !od[8+k]) pad_out[8+k] <= 1'b1;
    end
  endtask
  task gpio_out_write(input [15:0] v);
    integer k;
    begin
      for (k = 8; k < 24; k = k + 1) if (owner[k] == 3'd4) pad_out[k] <= v[k-8];
    end
  endtask
  task gpio_oe_write(input [7:0] v);
    integer k;
    begin
      for (k = 8; k < 16; k = k + 1) if (owner[k] == 3'd4) pad_oe[k] <= v[k-8];
    end
  endtask

  // ---- sequential: registers, imem, shared state -------------------------
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (i = 0; i < 4; i = i + 1) begin
        r_clkdiv[i] <= RST_CLKDIV; r_exec[i] <= RST_EXECCTRL; r_shift[i] <= RST_SHIFTCTRL;
        r_pin[i] <= RST_PINCTRL; r_assist[i] <= RST_ASSIST; r_poly[i] <= RST_CRC_POLY;
        r_timing[i] <= RST_TIMING;
      end
      r_en    <= 4'h0;
      txovf   <= 4'h0;
      rxunf   <= 4'h0;
      irq_en  <= 16'h0;
      err     <= 1'b0;
      flags   <= 8'h0;
      pad_out <= 16'h0;
      pad_oe  <= 8'h0;
      od      <= 8'h0;
      sync1   <= 16'h0;
      sync2   <= 16'h0;
      for (i = 8; i < 24; i = i + 1) owner[i] <= OWNER_RESET[3*(i-8) +: 3];
      rdata   <= 32'h0;
    end else begin
      sync1   <= {uio_in, ui_in};
      sync2   <= sync1;
      flags   <= flags_eng;
      pad_out <= pad_out_n;
      pad_oe  <= pad_oe_n;
      if (en) err <= ~hit;

      // sticky CPU-side FIFO errors
      for (i = 0; i < 4; i = i + 1) begin
        if (wr && is_eng && eng_sel == i[1:0] && eng_reg == 6'h09 && e_drop[i]) txovf[i] <= 1'b1;
        if (rd && is_eng && eng_sel == i[1:0] && eng_reg == 6'h0A && e_status[i][15:12] == 4'h0)
          rxunf[i] <= 1'b1;
      end

      if (wr) begin
        if (is_eng) begin
          case (eng_reg)
            6'h00: r_en[eng_sel] <= wdata[0];
            6'h01: r_clkdiv[eng_sel] <= wdata & M_CLKDIV;
            6'h02: r_exec[eng_sel] <= wdata & M_EXEC;
            6'h03: r_shift[eng_sel] <= wdata & M_SHIFT;
            6'h04: r_pin[eng_sel] <= wdata & M_PIN;
            6'h05: r_assist[eng_sel] <= wdata & M_ASSIST;
            6'h06: r_poly[eng_sel] <= wdata;
            6'h07: r_timing[eng_sel] <= wdata;
            6'h0B: begin
              if (wdata[17]) txovf[eng_sel] <= 1'b0;
              if (wdata[18]) rxunf[eng_sel] <= 1'b0;
            end
            default: ;
          endcase
        end else if (is_imem) begin
          // instruction memory: written by the gated word flops below
        end else if (is_owner) begin
          owner[own_pad] <= wdata[2:0];
        end else begin
          case (addr)
            12'h500: flags <= wdata[7:0];
            12'h504: flags <= flags_eng | wdata[7:0];
            12'h508: flags <= flags_eng & ~wdata[7:0];
            12'h50C: irq_en <= wdata[15:0];
            12'h510: r_en <= wdata[3:0];
            12'h514: if (wdata[15:0] == SAFE_KEY) begin
              r_en <= 4'h0;
              for (i = 8; i < 24; i = i + 1) if (owner[i] != 3'd5) owner[i] <= 3'd7;
            end
            12'h518: od_write(wdata[7:0]);
            12'h538: od_write(od | wdata[7:0]);
            12'h53C: od <= od & ~wdata[7:0];
            12'h51C: gpio_out_write(wdata[15:0]);
            12'h528: gpio_out_write(pad_out | wdata[15:0]);
            12'h52C: gpio_out_write(pad_out & ~wdata[15:0]);
            12'h520: gpio_oe_write(wdata[7:0]);
            12'h530: gpio_oe_write(pad_oe | wdata[7:0]);
            12'h534: gpio_oe_write(pad_oe & ~wdata[7:0]);
            default: ;
          endcase
        end
      end

      if (rd) begin
        if (is_eng) begin
          case (eng_reg)
            6'h00: rdata <= {31'h0, r_en[eng_sel]};
            6'h01: rdata <= r_clkdiv[eng_sel];
            6'h02: rdata <= r_exec[eng_sel];
            6'h03: rdata <= r_shift[eng_sel];
            6'h04: rdata <= r_pin[eng_sel];
            6'h05: rdata <= r_assist[eng_sel];
            6'h06: rdata <= r_poly[eng_sel];
            6'h07: rdata <= r_timing[eng_sel];
            6'h08: rdata <= {16'h0, imem[e_iaddr[eng_sel]]};
            6'h0A: rdata <= (e_status[eng_sel][15:12] != 4'h0) ? e_rxf[eng_sel] : 32'h0;
            6'h0B: rdata <= e_status[eng_sel] | {13'h0, rxunf[eng_sel], txovf[eng_sel], 17'h0};
            default: rdata <= 32'h0;
          endcase
        end else if (is_imem) begin
          rdata <= {16'h0, imem[addr[7:2]]};
        end else if (is_owner) begin
          rdata <= {29'h0, owner[own_pad]};
        end else begin
          case (addr)
            12'h500: rdata <= {24'h0, flags};
            12'h50C: rdata <= {16'h0, irq_en};
            12'h510: rdata <= {28'h0, r_en};
            12'h518: rdata <= {24'h0, od};
            12'h51C: rdata <= {16'h0, pad_out};
            12'h520: rdata <= {24'h0, pad_oe};
            12'h524: rdata <= {8'h0, pad_oe, pad_out};
            default: rdata <= 32'h0;
          endcase
        end
      end
    end
  end

  // ---- pads: owner none (6, 7) releases the pad ---------------------------
  wire [23:8] released;
  generate
    for (g = 8; g < 24; g = g + 1) begin : gen_rel
      assign released[g] = (owner[g][2:1] == 2'b11);
    end
  endgenerate
  assign uo_out  = pad_out[23:16] & ~released[23:16];
  assign uio_out = pad_out[15:8] & ~od & ~released[15:8];
  assign uio_oe  = ((pad_oe & ~od) | (~pad_out[15:8] & od)) & ~released[15:8];
  generate
    for (g = 8; g < 24; g = g + 1) begin : gen_sys
      assign pad_sys[g-8] = (owner[g] == 3'd5);
    end
  endgenerate
endmodule
