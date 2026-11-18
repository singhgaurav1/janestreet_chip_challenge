/*
 * Copyright (c) 2026 singhgaurav1
 * SPDX-License-Identifier: Apache-2.0
 *
 * Protocol Engine: one cycle-exact sequencer (docs/pe_isa.md).
 *
 * Structure follows the golden model (tools/peemu/model.py, Engine.cycle):
 * one combinational block computes every next-state value for the cycle, in
 * the model's order (filter/edge, divider, forced instruction or tick,
 * timestamper); one sequential block commits it. CPU-side actions (config,
 * FIFO push/pop, forced instruction, restart) arrive from pe_block and are
 * applied on top at the same clock edge, i.e. "between cycles" as in the
 * model. Lockstep tests compare this module's state with the model each cycle.
 *
 * Helper functions take everything they use as arguments, so they are safe
 * inside always @* and in continuous assignments.
 */
`default_nettype none

module pe_engine #(
    parameter [1:0] ENGINE_ID = 2'd0
) (
    input  wire        clk,
    input  wire        rst_n,
    // configuration registers (pe_block), layout in docs/pe_isa.md section 6
    input  wire [31:0] cfg_clkdiv,
    input  wire [31:0] cfg_execctrl,
    input  wire [31:0] cfg_shiftctrl,
    input  wire [31:0] cfg_pinctrl,
    input  wire [31:0] cfg_assist,
    input  wire [31:0] cfg_crc_poly,
    input  wire [31:0] cfg_timing,
    input  wire        enabled,
    // CPU actions, applied at the clock edge after this cycle's update
    input  wire        cpu_restart,
    input  wire        cpu_div_restart,
    input  wire        cpu_force_we,
    input  wire [15:0] cpu_force_instr,
    input  wire        cpu_txf_push,
    input  wire [31:0] cpu_txf_data,
    output wire        cpu_txf_drop,
    input  wire        cpu_rxf_pop,
    output wire [31:0] cpu_rxf_data,
    input  wire        cpu_serr_clr,
    input  wire        cpu_ovf_clr,
    output wire [31:0] status,
    // shared instruction memory read port
    output wire [5:0]  imem_addr,
    input  wire [15:0] imem_data,
    // shared state, start-of-cycle snapshot
    input  wire [31:0] pins,
    input  wire [7:0]  flags,
    // this cycle's requests (pe_block resolves pad ownership)
    output reg  [31:0] out_we,
    output reg  [31:0] out_val,
    output reg  [31:0] oe_we,
    output reg  [31:0] oe_val,
    output reg  [7:0]  flag_set,
    output reg  [7:0]  flag_clr
);
  // ======================================================================
  // helpers (pure functions)
  // ======================================================================
  function [31:0] rotl;
    input [31:0] v;
    input [4:0] s;
    rotl = (v << s) | (v >> (6'd32 - {1'b0, s}));
  endfunction

  function [31:0] rotr;
    input [31:0] v;
    input [4:0] s;
    rotr = (v >> s) | (v << (6'd32 - {1'b0, s}));
  endfunction

  function [31:0] lowmask;  // n = 0..32 ones
    input [5:0] n;
    lowmask = (n >= 6'd32) ? 32'hFFFF_FFFF : ((32'd1 << n) - 32'd1);
  endfunction

  function [31:0] rev32;
    input [31:0] v;
    integer i;
    for (i = 0; i < 32; i = i + 1) rev32[i] = v[31-i];
  endfunction

  function [31:0] crc_step;
    input [31:0] crc;
    input b;
    input [5:0] width;      // 1..32
    input [31:0] poly;
    input reflect;
    reg [31:0] wm, c;
    reg fb;
    begin
      wm = lowmask(width);
      if (reflect) begin
        fb = b ^ crc[0];
        c  = crc >> 1;
      end else begin
        fb = b ^ crc[width-1];
        c  = (crc << 1) & wm;
      end
      crc_step = fb ? (c ^ (poly & wm)) : c;
    end
  endfunction

  function [2:0] flag_index;
    input [2:0] n;
    input rel;
    input [1:0] id;
    flag_index = rel ? {n[2], n[1:0] + id} : n;
  endfunction

  // ======================================================================
  // configuration fields
  // ======================================================================
  wire [15:0] c_div_int     = cfg_clkdiv[31:16];
  wire [7:0]  c_div_frac    = cfg_clkdiv[15:8];
  wire [4:0]  c_wrap_bottom = cfg_execctrl[4:0];
  wire [4:0]  c_wrap_top    = cfg_execctrl[9:5];
  wire [4:0]  c_jmp_pin     = cfg_execctrl[14:10];
  wire        c_filter      = cfg_execctrl[15];
  wire [1:0]  c_sync_edge   = cfg_execctrl[17:16];
  wire        c_autosync    = cfg_execctrl[18];
  wire [2:0]  c_side_count  = cfg_execctrl[21:19];
  wire        c_side_opt    = cfg_execctrl[22];
  wire        c_side_pindir = cfg_execctrl[23];
  wire [5:0]  c_origin      = cfg_execctrl[29:24];
  wire        c_in_right    = cfg_shiftctrl[0];
  wire        c_out_right   = cfg_shiftctrl[1];
  wire        c_autopush    = cfg_shiftctrl[2];
  wire        c_autopull    = cfg_shiftctrl[3];
  wire [5:0]  c_push_th     = (cfg_shiftctrl[8:4] == 5'd0) ? 6'd32 : {1'b0, cfg_shiftctrl[8:4]};
  wire [5:0]  c_pull_th     = (cfg_shiftctrl[13:9] == 5'd0) ? 6'd32 : {1'b0, cfg_shiftctrl[13:9]};
  wire [4:0]  c_out_base    = cfg_pinctrl[4:0];
  wire [5:0]  c_out_count   = cfg_pinctrl[10:5];
  wire [4:0]  c_set_base    = cfg_pinctrl[15:11];
  wire [2:0]  c_set_count   = cfg_pinctrl[18:16];
  wire [4:0]  c_side_base   = cfg_pinctrl[23:19];
  wire [4:0]  c_in_base     = cfg_pinctrl[28:24];
  wire [1:0]  c_coder_mode  = cfg_assist[1:0];
  wire        c_coder_diff  = cfg_assist[2];
  wire        c_coder_idle  = cfg_assist[3];
  wire [1:0]  c_stuff_mode  = cfg_assist[5:4];
  wire [3:0]  c_stuff_n     = {1'b0, cfg_assist[8:6]} + 4'd1;
  wire [4:0]  c_bit_ticks   = {1'b0, cfg_assist[12:9]} + 5'd1;
  wire [5:0]  c_crc_width   = {1'b0, cfg_assist[17:13]} + 6'd1;
  wire        c_crc_ones    = cfg_assist[18];
  wire        c_stamp_en    = cfg_assist[19];
  wire [4:0]  c_stamp_count = {1'b0, cfg_assist[23:20]} + 5'd1;
  wire        c_crc_reflect = cfg_assist[24];
  wire [15:0] c_timeout     = cfg_timing[15:0];
  wire [15:0] c_sync_phase  = cfg_timing[31:16];
  wire [16:0] c_period      = (c_div_int == 16'd0) ? 17'h10000 : {1'b0, c_div_int};

  // ======================================================================
  // state
  // ======================================================================
  reg [4:0]  pc;
  reg [31:0] x, y, isr, osr, crc;
  reg [5:0]  isr_cnt, osr_cnt;
  reg        t, serr, ovf, stalled;
  reg [4:0]  delay;
  reg [15:0] wait_cnt;
  reg        evt_pending, sync_armed;
  reg        crc_on, stuff_on, coder_on;
  reg [3:0]  run;
  reg        last, tx_lvl, rx_prev;
  reg [3:0]  man_cnt;
  reg        man_bit;
  reg [16:0] div_cnt;
  reg [7:0]  div_acc;
  reg        h1, h2, f_prev;
  reg [15:0] stamp_prev, stamp_t;
  reg        stamp_valid;
  reg        force_valid;
  reg [15:0] force_instr;

  // ---- FIFOs --------------------------------------------------------------
  // 2 words each: enough for back-to-back CPU service at 60 MHz (docs/area.md)
  localparam integer FIFO_DEPTH = 2;
  localparam integer FAW        = 1;
  reg         eng_txf_pop, eng_rxf_push;
  reg  [31:0] eng_rxf_data;
  wire [31:0] txf_head;
  wire [FAW:0] txf_lvl, rxf_lvl;
  wire [2:0]  txf_level = {{(2 - FAW){1'b0}}, txf_lvl};   // STATUS keeps 3-bit fields
  wire [2:0]  rxf_level = {{(2 - FAW){1'b0}}, rxf_lvl};
  wire        txf_empty, rxf_full;

  pe_fifo #(.WIDTH(32), .DEPTH(FIFO_DEPTH), .AW(FAW), .ALLOW_PUSH_WHEN_POP(1)) u_txf (
      .clk(clk), .rst_n(rst_n), .clear(1'b0),
      .push(cpu_txf_push), .wdata(cpu_txf_data), .push_drop(cpu_txf_drop),
      .pop(eng_txf_pop), .rdata(txf_head), .level(txf_lvl), .empty(txf_empty), .full()
  );
  pe_fifo #(.WIDTH(32), .DEPTH(FIFO_DEPTH), .AW(FAW), .ALLOW_PUSH_WHEN_POP(0)) u_rxf (
      .clk(clk), .rst_n(rst_n), .clear(1'b0),
      .push(eng_rxf_push), .wdata(eng_rxf_data), .push_drop(),
      .pop(cpu_rxf_pop), .rdata(cpu_rxf_data), .level(rxf_lvl), .empty(), .full(rxf_full)
  );

  // STATUS: [4:0] PC, [5] T, [6] SERR, [7] OVF, [11:8] TX level, [15:12] RX level, [16] stalled
  assign status = {15'h0, stalled, 1'b0, rxf_level, 1'b0, txf_level, ovf, serr, t, pc};

  assign imem_addr = c_origin + {1'b0, pc};

  // ======================================================================
  // next state
  // ======================================================================
  reg [4:0]  n_pc, n_delay;
  reg [31:0] n_x, n_y, n_isr, n_osr, n_crc;
  reg [5:0]  n_isr_cnt, n_osr_cnt;
  reg        n_t, n_serr, n_ovf, n_stalled;
  reg [15:0] n_wait_cnt;
  reg        n_evt_pending, n_sync_armed, n_crc_on, n_stuff_on, n_coder_on;
  reg [3:0]  n_run, n_man_cnt;
  reg        n_last, n_tx_lvl, n_rx_prev, n_man_bit;
  reg [16:0] n_div_cnt;
  reg [7:0]  n_div_acc;
  reg [15:0] n_stamp_prev, n_stamp_t;
  reg        n_stamp_valid, n_force_valid;

  // per-cycle temporaries
  reg        s_pin, f_pin, edge_q, qual, tick;
  reg [8:0]  acc_sum;
  reg signed [18:0] d2;
  reg        do_issue, forced;
  reg [15:0] ins;
  reg [2:0]  op, fa;
  reg [4:0]  fb5, sd;
  reg [7:0]  opnd;
  reg [5:0]  nbits;
  reg [2:0]  dbits;
  reg [4:0]  sraw;
  reg [4:0]  dly;
  reg        side_present;
  reg [2:0]  side_pins;
  reg [4:0]  side_val;
  reg [1:0]  res;                 // 0 done, 1 stall, 2 reexec
  reg        jump;
  reg [4:0]  target;
  reg [31:0] rd_pins, data, wv, wmask;
  reg [31:0] op_out_we, op_out_val, op_oe_we, op_oe_val;
  reg        cond, v;
  reg [2:0]  fi;
  reg        b, lvl, tgt, need_stuff, rx_full_now;
  reg [1:0]  cmode;
  reg [3:0]  half;
  reg [15:0] stamp_cur;
  reg        stamp_hit;
  reg [6:0]  isr_sum;
  reg [2:0]  osel;
  reg [31:0] src_val, out_src, out_data, dm, io_mask, in_isr;
  reg        lvl_rx, b_rx, out_refill;
  reg [5:0]  n_io, sh_s;
  reg [63:0] sh_a, sh_f;
  reg        win_req, win_oe;
  reg [4:0]  win_base;
  reg [5:0]  win_cnt;
  reg [31:0] win_data;

  localparam [1:0] R_DONE = 2'd0, R_STALL = 2'd1, R_REEXEC = 2'd2;

  // Tasks below update this block's next-state variables. Configuration they
  // need is passed in as arguments: always @* does not look inside tasks.

  // drive the coder output level onto OUT_BASE (and OUT_BASE+1 if differential)
  task drive_level;
    input l;
    input [4:0] base;
    input diff;
    begin
      n_tx_lvl = l;
      op_out_we[base] = 1'b1;
      op_out_val[base] = l;
      if (diff) begin
        op_out_we[base + 5'd1] = 1'b1;
        op_out_val[base + 5'd1] = ~l;
      end
    end
  endtask

  task stuff_update;
    input bit_in;
    input [1:0] mode;
    begin
      if (mode == 2'd0) begin
        n_run  = (bit_in == n_last) ? n_run + 4'd1 : 4'd1;
        n_last = bit_in;
      end else begin
        n_run = (bit_in == (mode == 2'd2 ? 1'b0 : 1'b1)) ? n_run + 4'd1 : 4'd0;
      end
    end
  endtask

  task coder_drive;
    input bit_in;
    input [1:0] mode;       // effective mode (0 when the coder assist is off)
    input [3:0] half_bit;   // BIT_TICKS / 2
    input [4:0] base;
    input diff;
    begin
      case (mode)
        2'd0: lvl = bit_in;
        2'd1: lvl = bit_in ? n_tx_lvl : ~n_tx_lvl;
        2'd2: lvl = bit_in ? ~n_tx_lvl : n_tx_lvl;
        default: begin
          if (half_bit != 4'd0) begin
            lvl       = ~bit_in;
            n_man_bit = bit_in;
            n_man_cnt = half_bit;
          end else lvl = bit_in;
        end
      endcase
      drive_level(lvl, base, diff);
    end
  endtask

  always @* begin
    // ---- defaults: hold -------------------------------------------------
    n_pc = pc; n_x = x; n_y = y; n_isr = isr; n_osr = osr; n_crc = crc;
    n_isr_cnt = isr_cnt; n_osr_cnt = osr_cnt; n_t = t; n_serr = serr; n_ovf = ovf;
    n_stalled = stalled; n_delay = delay; n_wait_cnt = wait_cnt;
    n_evt_pending = evt_pending; n_sync_armed = sync_armed;
    n_crc_on = crc_on; n_stuff_on = stuff_on; n_coder_on = coder_on;
    n_run = run; n_last = last; n_tx_lvl = tx_lvl; n_rx_prev = rx_prev;
    n_man_cnt = man_cnt; n_man_bit = man_bit; n_div_cnt = div_cnt; n_div_acc = div_acc;
    n_stamp_prev = stamp_prev; n_stamp_t = stamp_t; n_stamp_valid = stamp_valid;
    n_force_valid = force_valid;
    out_we = 32'h0; out_val = 32'h0; oe_we = 32'h0; oe_val = 32'h0;
    op_out_we = 32'h0; op_out_val = 32'h0; op_oe_we = 32'h0; op_oe_val = 32'h0;
    flag_set = 8'h0; flag_clr = 8'h0;
    eng_txf_pop = 1'b0; eng_rxf_push = 1'b0; eng_rxf_data = 32'h0;
    tick = 1'b0; res = R_DONE; jump = 1'b0; target = 5'd0;
    data = 32'h0; wv = 32'h0; wmask = 32'h0; cond = 1'b0; v = 1'b0; fi = 3'd0;
    b = 1'b0; lvl = 1'b0; tgt = 1'b0; need_stuff = 1'b0; cmode = 2'd0; half = 4'd0;
    acc_sum = 9'h0; d2 = 19'sd0; isr_sum = 7'd0;
    side_present = 1'b0; side_pins = 3'd0; side_val = 5'd0;
    stamp_cur = 16'h0; stamp_hit = 1'b0;

    // ---- 1. sync pin filter and edge -------------------------------------
    s_pin  = pins[c_jmp_pin];
    f_pin  = c_filter ? ((s_pin & h1) | (s_pin & h2) | (h1 & h2)) : s_pin;
    edge_q = f_pin ^ f_prev;
    qual   = edge_q & ((c_sync_edge == 2'd0) | ((c_sync_edge == 2'd1) & f_pin) |
                       ((c_sync_edge == 2'd2) & ~f_pin));

    // ---- 2. divider -------------------------------------------------------
    if (enabled) begin
      if (qual && sync_armed) begin                 // frame-start hard sync
        n_div_cnt    = {1'b0, c_sync_phase};
        n_div_acc    = 8'h0;
        n_sync_armed = 1'b0;
      end else if (qual && c_autosync) begin        // nearest-slot resync
        d2 = ($signed({2'b00, div_cnt}) - $signed({3'b000, c_sync_phase}) - 19'sd1) <<< 1;
        n_div_acc = 8'h0;
        if (d2 < -$signed({2'b00, c_period})) begin
          n_div_cnt = {1'b0, c_sync_phase};
          tick = 1'b1;
        end else if (d2 >= $signed({2'b00, c_period})) begin
          n_div_cnt = {1'b0, c_sync_phase} + c_period;
        end else begin
          n_div_cnt = {1'b0, c_sync_phase};
        end
      end else if (div_cnt == 17'd0) begin          // regular tick
        acc_sum   = {1'b0, div_acc} + {1'b0, c_div_frac};
        n_div_acc = acc_sum[7:0];
        n_div_cnt = c_period - 17'd1 + {16'd0, acc_sum[8]};
        tick      = 1'b1;
      end else begin
        n_div_cnt = div_cnt - 17'd1;
      end
    end

    // ---- 3./4. forced instruction or tick --------------------------------
    forced   = force_valid;
    do_issue = 1'b0;
    ins      = force_valid ? force_instr : imem_data;
    if (force_valid) begin
      do_issue = 1'b1;
    end else if (tick) begin
      if (man_cnt != 4'd0) begin                    // Manchester second half
        n_man_cnt = man_cnt - 4'd1;
        if (man_cnt == 4'd1) drive_level(man_bit, c_out_base, c_coder_diff);
      end
      if (delay != 5'd0) begin
        n_delay   = delay - 5'd1;
        n_stalled = 1'b0;
      end else do_issue = 1'b1;
    end

    // ---- 5. issue -----------------------------------------------------------
    op   = ins[15:13];
    sd   = ins[12:8];
    opnd = ins[7:0];
    fa   = ins[7:5];
    fb5  = ins[4:0];
    nbits = (fb5 == 5'd0) ? 6'd32 : {1'b0, fb5};

    // side-set / delay split
    dbits = 3'd5 - c_side_count;
    dly   = sd & ((5'd1 << dbits) - 5'd1);
    sraw  = sd >> dbits;
    if (c_side_count != 3'd0) begin
      if (c_side_opt) begin
        side_pins    = c_side_count - 3'd1;
        side_present = sraw[side_pins];
        side_val     = sraw & ((5'd1 << side_pins) - 5'd1);
      end else begin
        side_pins    = c_side_count;
        side_present = 1'b1;
        side_val     = sraw;
      end
    end

    rd_pins     = rotr(pins, c_in_base);
    rx_full_now = rxf_full;

    // ---- shared operand mux (IN / MOV / MATCH sources, MOV encoding) -------
    case (op)
      3'd2: osel = (fa == 3'd0) ? 3'd0 : (fa == 3'd1) ? 3'd1 : (fa == 3'd2) ? 3'd2 :
                   (fa == 3'd6) ? 3'd5 : (fa == 3'd7) ? 3'd6 : 3'd3;
      3'd6: osel = (fa == 3'd0) ? 3'd5 : (fa == 3'd1) ? 3'd6 : (fa == 3'd2) ? 3'd1 :
                   (fa == 3'd3) ? 3'd7 : (fa == 3'd4) ? 3'd0 : 3'd3;
      default: osel = opnd[2:0];
    endcase
    case (osel)
      3'd0: src_val = rd_pins;
      3'd1: src_val = x;
      3'd2: src_val = y;
      3'd3: src_val = 32'h0;
      3'd4: src_val = {20'h0, 1'b0, rxf_level, 1'b0, txf_level, rx_full_now, txf_empty, serr, t};
      3'd5: src_val = isr;
      3'd6: src_val = osr;
      default: src_val = crc;
    endcase

    // ---- serial RX bit: line decode (IN bit) ------------------------------
    lvl_rx = pins[c_in_base];
    case (coder_on ? c_coder_mode : 2'd0)
      2'd1: b_rx = (lvl_rx == rx_prev);
      2'd2: b_rx = (lvl_rx != rx_prev);
      default: b_rx = lvl_rx;
    endcase

    // ---- shared 64-bit funnel shifter (IN / OUT) ----------------------------
    // OUT right: {0, src} >> n        -> osr' = low half, data = src & mask
    // OUT left:  {src, 0} >> (32 - n) -> data = high half, osr' = low half
    // IN right:  {dm, isr} >> n       -> isr' = low half
    // IN left:   {isr, 0} >> (32 - n) -> isr' = low half | dm
    n_io = ((op == 3'd2 && fa == 3'd4) || (op == 3'd3 && fa == 3'd7)) ? 6'd1 : nbits;
    io_mask    = lowmask(n_io);
    out_refill = c_autopull && (osr_cnt >= c_pull_th);
    out_src    = out_refill ? txf_head : osr;
    dm         = ((fa == 3'd4) ? {31'h0, b_rx} : src_val) & io_mask;
    if (op == 3'd3) begin
      sh_a = c_out_right ? {32'h0, out_src} : {out_src, 32'h0};
      sh_s = c_out_right ? n_io : (6'd32 - n_io);
    end else begin
      sh_a = c_in_right ? {dm, isr} : {isr, 32'h0};
      sh_s = c_in_right ? n_io : (6'd32 - n_io);
    end
    sh_f     = sh_a >> sh_s;
    out_data = c_out_right ? (out_src & io_mask) : sh_f[63:32];
    in_isr   = c_in_right ? sh_f[31:0] : (sh_f[31:0] | dm);

    win_req = 1'b0; win_oe = 1'b0; win_base = 5'd0; win_cnt = 6'd0; win_data = 32'h0;

    if (do_issue) begin
      case (op)
        // ---------------- JMP -------------------------------------------------
        3'd0: begin
          case (fa)
            3'd0: cond = 1'b1;
            3'd1: cond = (x == 32'h0);
            3'd2: begin cond = (x != 32'h0); n_x = x - 32'd1; end
            3'd3: cond = (y == 32'h0);
            3'd4: begin cond = (y != 32'h0); n_y = y - 32'd1; end
            3'd5: cond = f_pin;
            3'd6: cond = (osr_cnt < c_pull_th);
            default: cond = t;
          endcase
          jump = cond;
          target = fb5;
        end
        // ---------------- WAIT ------------------------------------------------
        3'd1: begin
          case (opnd[6:5])
            2'd0: v = pins[fb5];
            2'd1: v = pins[c_in_base + fb5];
            2'd2: begin
              fi = flag_index(fb5[2:0], fb5[3], ENGINE_ID);
              v  = flags[fi];
            end
            default: v = f_pin;
          endcase
          if (v == opnd[7]) begin
            if (opnd[6:5] == 2'd2 && opnd[7] && !fb5[4]) flag_clr[fi] = 1'b1;
            if (c_timeout != 16'd0) n_t = 1'b0;
          end else if (c_timeout != 16'd0) begin
            n_wait_cnt = wait_cnt + 16'd1;
            if (n_wait_cnt >= c_timeout) n_t = 1'b1;
            else res = R_STALL;
          end else res = R_STALL;
        end
        // ---------------- IN --------------------------------------------------
        3'd2: begin
          isr_sum = {1'b0, isr_cnt} + {1'b0, n_io};
          if (c_autopush && isr_sum >= {1'b0, c_push_th} && rx_full_now) res = R_STALL;
          else begin
            if (fa == 3'd4) begin                     // serial RX bit path
              n_rx_prev  = lvl_rx;
              need_stuff = stuff_on && (run >= c_stuff_n);
              tgt = (c_stuff_mode == 2'd0) ? last : (c_stuff_mode == 2'd2 ? 1'b0 : 1'b1);
              if (need_stuff && (b_rx == ~tgt)) begin
                stuff_update(b_rx, c_stuff_mode);
                res = R_REEXEC;
              end else begin
                if (need_stuff) begin                 // stuff error
                  n_t = 1'b1; n_serr = 1'b1; n_run = 4'd0; n_last = b_rx;
                end else if (stuff_on) stuff_update(b_rx, c_stuff_mode);
                if (crc_on) n_crc = crc_step(crc, b_rx, c_crc_width, cfg_crc_poly, c_crc_reflect);
              end
            end
            if (res == R_DONE) begin
              n_isr     = in_isr;
              n_isr_cnt = (isr_sum >= 7'd32) ? 6'd32 : isr_sum[5:0];
              if (c_autopush && n_isr_cnt >= c_push_th) begin
                eng_rxf_push = 1'b1;
                eng_rxf_data = n_isr;
                n_isr = 32'h0;
                n_isr_cnt = 6'd0;
              end
            end
          end
        end
        // ---------------- OUT -------------------------------------------------
        3'd3: begin
          tgt = (c_stuff_mode == 2'd0) ? last : (c_stuff_mode == 2'd2 ? 1'b0 : 1'b1);
          if (fa == 3'd7 && stuff_on && run >= c_stuff_n) begin
            stuff_update(~tgt, c_stuff_mode);         // insert a stuff bit
            coder_drive(~tgt, coder_on ? c_coder_mode : 2'd0, c_bit_ticks[4:1], c_out_base,
                        c_coder_diff);
            res = R_REEXEC;
          end else if (out_refill && txf_empty) begin
            res = R_STALL;                            // autopull with nothing to pull
          end else begin
            if (out_refill) eng_txf_pop = 1'b1;
            data      = out_data;
            n_osr     = sh_f[31:0];
            isr_sum   = (out_refill ? 7'd0 : {1'b0, osr_cnt}) + {1'b0, n_io};
            n_osr_cnt = (isr_sum >= 7'd32) ? 6'd32 : isr_sum[5:0];
            case (fa)
              3'd0, 3'd4: begin                       // pins / pindirs
                win_req = 1'b1; win_oe = fa[2]; win_base = c_out_base; win_cnt = n_io;
                win_data = data;
              end
              3'd1: n_x = data;
              3'd2: n_y = data;
              3'd5: begin jump = 1'b1; target = data[4:0]; end
              3'd6: begin n_isr = data; n_isr_cnt = n_io; end
              3'd7: begin
                b = data[0];
                if (crc_on) n_crc = crc_step(crc, b, c_crc_width, cfg_crc_poly, c_crc_reflect);
                if (stuff_on) stuff_update(b, c_stuff_mode);
                coder_drive(b, coder_on ? c_coder_mode : 2'd0, c_bit_ticks[4:1], c_out_base,
                            c_coder_diff);
              end
              default: ;
            endcase
          end
        end
        // ---------------- PUSH / PULL / EVT / CTL ------------------------------
        3'd4: begin
          case (opnd[7:6])
            2'd0: begin                               // PUSH
              if (!(opnd[5] && isr_cnt < c_push_th)) begin
                if (rx_full_now) begin
                  if (opnd[4]) res = R_STALL;
                end else begin
                  eng_rxf_push = 1'b1;
                  eng_rxf_data = isr;
                end
                if (res == R_DONE) begin n_isr = 32'h0; n_isr_cnt = 6'd0; end
              end
            end
            2'd1: begin                               // PULL
              if (!(opnd[5] && osr_cnt < c_pull_th)) begin
                if (txf_empty) begin
                  if (opnd[4]) res = R_STALL;
                  else n_osr = x;
                end else begin
                  eng_txf_pop = 1'b1;
                  n_osr = txf_head;
                end
                if (res == R_DONE) n_osr_cnt = 6'd0;
              end
            end
            2'd2: begin                               // EVT
              fi = flag_index(opnd[2:0], opnd[3], ENGINE_ID);
              case (opnd[5:4])
                2'd0: flag_set[fi] = 1'b1;
                2'd1: flag_clr[fi] = 1'b1;
                2'd2: begin
                  if (!evt_pending) begin
                    flag_set[fi] = 1'b1;
                    n_evt_pending = 1'b1;
                    res = R_STALL;
                  end else if (flags[fi]) res = R_STALL;
                end
                default: ;
              endcase
            end
            default: begin                            // control
              case (opnd[5:4])
                2'd0: n_sync_armed = 1'b1;
                2'd1: begin
                  if (opnd[0]) n_crc_on = 1'b1;
                  if (opnd[1]) begin n_stuff_on = 1'b1; n_run = 4'd0; n_last = c_coder_idle; end
                  if (opnd[2]) begin
                    n_coder_on = 1'b1; n_tx_lvl = c_coder_idle; n_rx_prev = c_coder_idle;
                    n_man_cnt = 4'd0;
                  end
                end
                2'd2: begin
                  if (opnd[0]) n_crc_on = 1'b0;
                  if (opnd[1]) n_stuff_on = 1'b0;
                  if (opnd[2]) begin n_coder_on = 1'b0; n_man_cnt = 4'd0; end
                end
                default: n_crc = c_crc_ones ? lowmask(c_crc_width) : 32'h0;
              endcase
            end
          endcase
        end
        // ---------------- MOV -------------------------------------------------
        3'd5: begin
          data = src_val;
          if (opnd[3]) data = ~data;
          if (opnd[4]) data = rev32(data);
          case (fa)
            3'd0, 3'd3: begin                         // pins / pindirs
              win_req = 1'b1; win_oe = (fa == 3'd3); win_base = c_out_base;
              win_cnt = c_out_count; win_data = data;
            end
            3'd1: n_x = data;
            3'd2: n_y = data;
            3'd4: begin jump = 1'b1; target = data[4:0]; end
            3'd5: begin n_isr = data; n_isr_cnt = 6'd0; end
            3'd6: begin n_osr = data; n_osr_cnt = 6'd0; end
            default: n_crc = data & lowmask(c_crc_width);
          endcase
        end
        // ---------------- MATCH -----------------------------------------------
        3'd6: begin
          wmask = lowmask(nbits);
          if (fa == 3'd0 && c_in_right) wmask = wmask << (6'd32 - nbits);
          n_t = ((src_val & wmask) == (y & wmask));
        end
        // ---------------- SET -------------------------------------------------
        default: begin
          case (fa)
            3'd0, 3'd4: begin                         // pins / pindirs
              win_req = 1'b1; win_oe = fa[2]; win_base = c_set_base;
              win_cnt = {3'b000, c_set_count}; win_data = {27'h0, fb5};
            end
            3'd1: n_x = {27'h0, fb5};
            3'd2: n_y = {27'h0, fb5};
            default: ;
          endcase
        end
      endcase

      // one pin-window unit for OUT / MOV / SET
      if (win_req) begin
        wmask = rotl(lowmask(win_cnt), win_base);
        wv    = rotl(win_data, win_base);
        if (win_oe) begin
          op_oe_we  = op_oe_we | wmask;
          op_oe_val = (op_oe_val & ~wmask) | (wv & wmask);
        end else begin
          op_out_we  = op_out_we | wmask;
          op_out_val = (op_out_val & ~wmask) | (wv & wmask);
        end
      end

      // side-set: applied on every issue, wins over the op's own pin writes
      if (side_present) begin
        wmask = rotl(lowmask({3'b000, side_pins}), c_side_base);
        wv    = rotl({27'h0, side_val}, c_side_base);
        if (c_side_pindir) begin
          op_oe_we  = op_oe_we | wmask;
          op_oe_val = (op_oe_val & ~wmask) | (wv & wmask);
        end else begin
          op_out_we  = op_out_we | wmask;
          op_out_val = (op_out_val & ~wmask) | (wv & wmask);
        end
      end

      // result
      if (res == R_STALL || (res == R_REEXEC && forced)) begin
        if (!forced) n_stalled = 1'b1;
      end else if (res == R_REEXEC) begin
        n_delay   = c_bit_ticks - 5'd1;
        n_stalled = 1'b0;
      end else begin
        n_wait_cnt    = 16'd0;
        n_evt_pending = 1'b0;
        if (forced) begin
          if (jump) n_pc = target;
          n_force_valid = 1'b0;
        end else begin
          n_pc      = jump ? target : ((pc == c_wrap_top) ? c_wrap_bottom : pc + 5'd1);
          n_delay   = dly;
          n_stalled = 1'b0;
        end
      end
    end

    // ---- pin writes (Manchester half first, then the op, then side-set) -----
    out_we  = op_out_we;
    out_val = op_out_val;
    oe_we   = op_oe_we;
    oe_val  = op_oe_val;

    // ---- timestamper -----------------------------------------------------
    if (enabled && c_stamp_en) begin
      wv        = lowmask({1'b0, c_stamp_count});
      stamp_cur = rd_pins[15:0] & wv[15:0];
      n_stamp_t = (stamp_t == 16'hFFFF) ? 16'hFFFF : stamp_t + 16'd1;
      stamp_hit = !stamp_valid || (stamp_cur != stamp_prev) || (n_stamp_t == 16'hFFFF);
      if (stamp_hit) begin
        if (!eng_rxf_push && !rx_full_now) begin
          eng_rxf_push = 1'b1;
          eng_rxf_data = {(stamp_valid ? n_stamp_t : 16'h0), stamp_cur};
        end else n_ovf = 1'b1;
        n_stamp_prev  = stamp_cur;
        n_stamp_t     = 16'h0;
        n_stamp_valid = 1'b1;
      end
    end
  end

  // ======================================================================
  // commit (+ CPU actions)
  // ======================================================================
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pc <= 5'd0; x <= 32'h0; y <= 32'h0; isr <= 32'h0; osr <= 32'h0; crc <= 32'h0;
      isr_cnt <= 6'd0; osr_cnt <= 6'd32; t <= 1'b0; serr <= 1'b0; ovf <= 1'b0;
      stalled <= 1'b0; delay <= 5'd0; wait_cnt <= 16'd0; evt_pending <= 1'b0;
      sync_armed <= 1'b0; crc_on <= 1'b0; stuff_on <= 1'b0; coder_on <= 1'b0;
      run <= 4'd0; last <= 1'b0; tx_lvl <= 1'b0; rx_prev <= 1'b0; man_cnt <= 4'd0;
      man_bit <= 1'b0; div_cnt <= 17'd0; div_acc <= 8'd0; h1 <= 1'b0; h2 <= 1'b0;
      f_prev <= 1'b0; stamp_prev <= 16'h0; stamp_t <= 16'h0; stamp_valid <= 1'b0;
      force_valid <= 1'b0; force_instr <= 16'h0;
    end else begin
      pc <= n_pc; x <= n_x; y <= n_y; isr <= n_isr; osr <= n_osr; crc <= n_crc;
      isr_cnt <= n_isr_cnt; osr_cnt <= n_osr_cnt; t <= n_t;
      serr <= n_serr & ~cpu_serr_clr; ovf <= n_ovf & ~cpu_ovf_clr;
      stalled <= n_stalled; delay <= n_delay; wait_cnt <= n_wait_cnt;
      evt_pending <= n_evt_pending; sync_armed <= n_sync_armed;
      crc_on <= n_crc_on; stuff_on <= n_stuff_on; coder_on <= n_coder_on;
      run <= n_run; last <= n_last; tx_lvl <= n_tx_lvl; rx_prev <= n_rx_prev;
      man_cnt <= n_man_cnt; man_bit <= n_man_bit; div_cnt <= n_div_cnt; div_acc <= n_div_acc;
      h1 <= s_pin; h2 <= h1; f_prev <= f_pin;
      stamp_prev <= n_stamp_prev; stamp_t <= n_stamp_t; stamp_valid <= n_stamp_valid;
      force_valid <= n_force_valid;
      // CPU actions land after this cycle's update ("between cycles" in the model)
      if (cpu_restart) begin
        pc <= 5'd0; isr_cnt <= 6'd0; osr_cnt <= 6'd32; delay <= 5'd0; wait_cnt <= 16'd0;
        evt_pending <= 1'b0; sync_armed <= 1'b0; crc_on <= 1'b0; stuff_on <= 1'b0;
        coder_on <= 1'b0; run <= 4'd0; last <= 1'b0; tx_lvl <= 1'b0; rx_prev <= 1'b0;
        man_cnt <= 4'd0; man_bit <= 1'b0;
      end
      if (cpu_div_restart) begin
        div_cnt <= 17'd0;
        div_acc <= 8'd0;
      end
      if (cpu_force_we) begin
        force_valid <= 1'b1;
        force_instr <= cpu_force_instr;
      end
    end
  end
endmodule
