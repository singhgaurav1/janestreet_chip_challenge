// Firmware testbench: the converted Ibex (src/ibex_small.v) on its own, with a
// RAM model and a SIMULATION-ONLY control device. Owned by the firmware chat.
//
// This is NOT the chip. It exists so firmware (startup, traps, interrupts,
// riscv-tests) can be validated before the full-chip RTL lands. It only uses
// the documented Ibex top-level interface (Ibex docs, "Integration" and
// "Exceptions and Interrupts"); it does not look inside the core.
//
// Memory map (testbench only; the chip's map comes from the F1 register YAML):
//   0x0000_0000 .. RAM (64 KiB array; +ram_size=N limits the usable part so
//                   firmware is checked against the real SRAM size)
//   0x2000_0000    sim control device (see SIM_* below)
// Boot address 0x0: vector table at 0x00, reset entry at 0x80.
//
// Plusargs:
//   +img=<file>      byte hex image from objcopy -O verilog (required)
//   +ram_size=<n>    usable RAM bytes (default 65536). Accesses beyond it
//                    get a bus error and fail the test unless +allow_bus_err.
//   +max_cycles=<n>  timeout (default 2,000,000)
//   +seed=<n>        random wait-state seed (default 1)
//   +stall=<pct>     probability (0-100) of withholding a grant or a response
//                    in a given cycle (default 0 = zero wait states)
//   +single_port     instr and data share one RAM port (data has priority),
//                    as with the chip's single-port SRAM macro
//   +trace           print every data-port access
//   +dump=<addr>     at the end, print 16 words from <addr> (hex) as
//                    "MEM <addr> <word>" lines (how the host reads the
//                    firmware status block over the debug bridge)
//   +pe_stub         accept accesses to 0x1000_0000..0x1000_0FFF (the firmware's
//                    STUB PE block address) and log them as "PEW/PER <addr>
//                    <data>"; reads return 0. Lets HAL code run here so its
//                    bus sequence can be checked. NOT a model of the engines.
//   +reset_at=<n>    pulse rst_n again at cycle n WITHOUT reloading RAM
//                    (models a reset release with no image reload)
//
// Pass/fail protocol (riscv-tests "tohost" convention):
//   write 1 to SIM_TOHOST            -> PASS
//   write (n << 1) | 1 to SIM_TOHOST -> FAIL with code n
// The final line printed is "PASS ..." or "FAIL ...". Exit status is 0 either
// way; run scripts grep for the result line.

`timescale 1ns / 1ps
`default_nettype none

module tb_ibex_fw;
  localparam [3:0] MUBI_ON = 4'b0101, MUBI_OFF = 4'b1010;
  localparam int RAM_BYTES = 65536;

  // Sim device registers (byte offsets from 0x2000_0000)
  localparam logic [31:0] SIM_BASE    = 32'h2000_0000;
  localparam logic [7:0]  SIM_TOHOST  = 8'h00;  // W: end test (see above)
  localparam logic [7:0]  SIM_PUTC    = 8'h10;  // W: console character
  localparam logic [7:0]  SIM_IRQ_SET = 8'h14;  // W1S / R: interrupt lines
  localparam logic [7:0]  SIM_IRQ_CLR = 8'h18;  // W1C
  localparam logic [7:0]  SIM_CYCLES  = 8'h1C;  // R: testbench cycle count
  localparam logic [7:0]  SIM_ERRCNT  = 8'h20;  // R: bus errors so far
  localparam logic [7:0]  SIM_PUTHEX  = 8'h24;  // W: print "DBG <hex>" line
  // SIM_IRQ bits: [14:0] irq_fast_i, [15] irq_external_i, [16] irq_timer_i,
  //               [17] irq_software_i, [18] irq_nm_i

  // rst_n starts high and falls at t=1: Ibex flops use asynchronous reset and
  // its core clock is gated during reset, so a simulator only resets them on a
  // real negedge of rst_n. Holding rst_n low from t=0 leaves them at their
  // initial simulator value (found: the hart started in U-mode).
  logic clk = 0, rst_n = 1;
  always #8.333 clk = ~clk;  // 60 MHz

  logic [7:0] mem [0:RAM_BYTES-1];

  // Ibex ports
  logic        instr_req, instr_gnt, instr_rvalid, instr_err;
  logic [31:0] instr_addr, instr_rdata;
  logic        data_req, data_gnt, data_rvalid, data_we, data_err;
  logic [3:0]  data_be;
  logic [31:0] data_addr, data_wdata, data_rdata;
  logic [18:0] irq_lines = '0;
  logic        core_sleep;

  ibex_small_top dut (
    .clk_i(clk), .rst_ni(rst_n), .test_en_i(1'b0), .scan_rst_ni(1'b1),
    .ram_cfg_icache_tag_i(24'h0), .ram_cfg_icache_data_i(24'h0),
    .ram_cfg_icache_tag_o(), .ram_cfg_icache_data_o(),
    .cheriot_enable_i(MUBI_OFF), .hart_id_i(32'h0), .boot_addr_i(32'h0),
    .trvk_heap_base_addr_i(32'h0),
    .instr_req_o(instr_req), .instr_gnt_i(instr_gnt), .instr_rvalid_i(instr_rvalid),
    .instr_addr_o(instr_addr), .instr_rdata_i(instr_rdata), .instr_rdata_intg_i(7'h0),
    .instr_err_i(instr_err),
    .data_req_o(data_req), .data_gnt_i(data_gnt), .data_rvalid_i(data_rvalid),
    .data_we_o(data_we), .data_be_o(data_be), .data_addr_o(data_addr), .data_wdata_o(data_wdata),
    .data_wdata_intg_o(), .data_rdata_i(data_rdata), .data_rdata_intg_i(7'h0), .data_err_i(data_err),
    .data_tag_o(), .data_tag_i(1'b0),
    .trvk_revbm_req_o(), .trvk_revbm_gnt_i(1'b0), .trvk_revbm_rvalid_i(1'b0),
    .trvk_revbm_addr_o(), .trvk_revbm_rdata_i(32'h0), .trvk_revbm_rdata_intg_i(7'h0),
    .trvk_revbm_err_i(1'b0),
    .irq_software_i(irq_lines[17]), .irq_timer_i(irq_lines[16]),
    .irq_external_i(irq_lines[15]), .irq_fast_i(irq_lines[14:0]), .irq_nm_i(irq_lines[18]),
    .scramble_key_valid_i(1'b0), .scramble_key_i(128'h0), .scramble_nonce_i(64'h0),
    .scramble_req_o(),
    .debug_req_i(1'b0), .crash_dump_o(), .double_fault_seen_o(),
    .fetch_enable_i(MUBI_ON), .mcounteren_writable_i(MUBI_ON),
    .alert_minor_o(), .alert_major_internal_o(), .alert_major_bus_o(), .core_sleep_o(core_sleep),
    .lockstep_cmp_en_o(),
    .instr_req_shadow_o(), .instr_addr_shadow_o(), .data_req_shadow_o(), .data_we_shadow_o(),
    .data_be_shadow_o(), .data_addr_shadow_o(), .data_wdata_shadow_o(),
    .data_wdata_intg_shadow_o()
  );

  // ---------------------------------------------------------------- options
  string  img;
  int     ram_size = RAM_BYTES, max_cycles = 2000000, stall_pct = 0, seed = 1;
  int     dump_addr = -1, reset_at = -1;
  bit     single_port = 0, trace = 0, allow_bus_err = 0, pe_stub = 0;
  longint cycles = 0;
  int     bus_errors = 0;
  bit     done = 0;

  function automatic bit rnd_stall();
    return stall_pct != 0 && ($urandom % 100) < stall_pct;
  endfunction

  function automatic bit in_ram(input logic [31:0] a);
    return a < 32'(ram_size);
  endfunction

  function automatic logic [31:0] ram_rd(input logic [31:0] a);
    logic [31:0] w;
    for (int k = 0; k < 4; k++) w[8*k +: 8] = mem[32'({a[15:2], 2'b00}) + k];
    return w;
  endfunction

  // -------------------------------------------------------- response queues
  // Each port accepts a request on grant and answers in order, one or more
  // cycles later. Memory is read/written at grant time.
  typedef struct packed { logic [31:0] rdata; logic err; } resp_t;
  resp_t iq[$], dq[$];

  // Grant decisions (combinational on the current request)
  logic dgnt_ok, ignt_ok;
  always_comb begin
    dgnt_ok = data_req && dq.size() < 2;
    ignt_ok = instr_req && iq.size() < 4 && !(single_port && data_req);
  end

  logic dstall_g, istall_g, dstall_r, istall_r;
  always @(negedge clk) begin  // pick random stalls between edges
    dstall_g <= rnd_stall(); istall_g <= rnd_stall();
    dstall_r <= rnd_stall(); istall_r <= rnd_stall();
  end
  assign data_gnt  = dgnt_ok && !dstall_g;
  assign instr_gnt = ignt_ok && !istall_g;

  // ------------------------------------------------------------ sim device
  task automatic sim_write(input logic [7:0] off, input logic [31:0] v, input logic [3:0] be);
    case (off)
      SIM_TOHOST: if (!done) begin
        done = 1;
        if (v == 32'd1) $display("PASS cycles=%0d bus_errors=%0d", cycles, bus_errors);
        else            $display("FAIL code=%0d (tohost=0x%08x) cycles=%0d", v >> 1, v, cycles);
      end
      SIM_PUTC:    $write("%c", v[7:0]);
      SIM_PUTHEX:  $display("DBG 0x%08x cycle=%0d", v, cycles);
      SIM_IRQ_SET: irq_lines <= irq_lines | v[18:0];
      SIM_IRQ_CLR: irq_lines <= irq_lines & ~v[18:0];
      default: ;
    endcase
  endtask

  function automatic logic [31:0] sim_read(input logic [7:0] off);
    case (off)
      SIM_IRQ_SET: return 32'(irq_lines);
      SIM_CYCLES:  return cycles[31:0];
      SIM_ERRCNT:  return 32'(bus_errors);
      default:     return 32'h0;
    endcase
  endfunction

  task automatic bus_error(input string port, input logic [31:0] a);
    bus_errors++;
    $display("BUSERR %s addr=0x%08x cycle=%0d (ram_size=%0d)", port, a, cycles, ram_size);
    if (!allow_bus_err && !done) begin
      done = 1;
      $display("FAIL code=-1 unexpected bus error");
    end
  endtask

  // ------------------------------------------------------------- the clock
  // One block, fixed order: answer the oldest queued request first, then
  // accept this cycle's grants. Latency is therefore at least one cycle.
  always @(posedge clk) begin
    if (rst_n) cycles <= cycles + 1;

    data_rvalid  <= 1'b0;
    instr_rvalid <= 1'b0;
    if (dq.size() > 0 && !dstall_r) begin
      resp_t r = dq.pop_front();
      data_rvalid <= 1'b1; data_rdata <= r.rdata; data_err <= r.err;
    end
    if (iq.size() > 0 && !istall_r) begin
      resp_t r = iq.pop_front();
      instr_rvalid <= 1'b1; instr_rdata <= r.rdata; instr_err <= r.err;
    end

    if (data_gnt) begin
      resp_t r = '{rdata: 32'h0, err: 1'b0};
      if (in_ram(data_addr)) begin
        r.rdata = ram_rd(data_addr);
        if (data_we)
          for (int k = 0; k < 4; k++)
            if (data_be[k]) mem[32'({data_addr[15:2], 2'b00}) + k] <= data_wdata[8*k +: 8];
      end else if (data_addr[31:8] == SIM_BASE[31:8]) begin
        if (data_we) sim_write(data_addr[7:0], data_wdata, data_be);
        else r.rdata = sim_read(data_addr[7:0]);
      end else if (pe_stub && data_addr[31:12] == 20'h10000) begin
        if (data_we) $display("PEW 0x%08x 0x%08x", data_addr, data_wdata);
        else $display("PER 0x%08x 0x%08x", data_addr, 32'h0);
      end else begin
        r.err = 1'b1;
        bus_error(data_we ? "store" : "load", data_addr);
      end
      if (trace)
        $display("TRACE %0d %s addr=0x%08x be=%b wdata=0x%08x rdata=0x%08x", cycles,
                 data_we ? "W" : "R", data_addr, data_be, data_wdata, r.rdata);
      dq.push_back(r);
    end
    if (instr_gnt) begin
      resp_t r = '{rdata: 32'h0, err: 1'b0};
      if (in_ram(instr_addr)) r.rdata = ram_rd(instr_addr);
      else begin
        r.err = 1'b1;
        bus_error("fetch", instr_addr);
      end
      iq.push_back(r);
    end
  end

  // ------------------------------------------------------------- main
  initial begin
    data_rvalid = 0; instr_rvalid = 0; data_err = 0; instr_err = 0;
    data_rdata = 0; instr_rdata = 0;
    if (!$value$plusargs("img=%s", img)) begin
      $display("FAIL code=-2 no +img=<file>");
      $finish;
    end
    void'($value$plusargs("ram_size=%d", ram_size));
    void'($value$plusargs("max_cycles=%d", max_cycles));
    void'($value$plusargs("stall=%d", stall_pct));
    void'($value$plusargs("seed=%d", seed));
    void'($value$plusargs("dump=%h", dump_addr));
    void'($value$plusargs("reset_at=%d", reset_at));
    single_port   = $test$plusargs("single_port");
    trace         = $test$plusargs("trace");
    allow_bus_err = $test$plusargs("allow_bus_err");
    pe_stub       = $test$plusargs("pe_stub");
    if (ram_size > RAM_BYTES) ram_size = RAM_BYTES;
    void'($urandom(seed));
    // Fill RAM with a non-zero pattern: the SRAM macro powers up random, so
    // firmware must not rely on zeroed memory.
    for (int i = 0; i < RAM_BYTES; i++) mem[i] = 8'hA5 ^ 8'(i);
    $readmemh(img, mem);
    $display("tb_ibex_fw: img=%s ram_size=%0d stall=%0d%% seed=%0d single_port=%0d",
             img, ram_size, stall_pct, seed, single_port);
    #1 rst_n = 0;
    repeat (5) @(posedge clk);
    rst_n = 1;
    while (!done && cycles < longint'(max_cycles)) begin
      @(posedge clk);
      if (reset_at >= 0 && cycles == longint'(reset_at)) begin
        $display("tb_ibex_fw: reset pulse at cycle %0d (RAM not reloaded)", cycles);
        @(negedge clk) rst_n = 0;
        repeat (5) @(posedge clk);
        @(negedge clk) rst_n = 1;
      end
    end
    if (!done) $display("FAIL code=-3 timeout after %0d cycles", cycles);
    if (dump_addr >= 0)
      for (int i = 0; i < 16; i++)
        $display("MEM 0x%08x 0x%08x", dump_addr + 4 * i, ram_rd(32'(dump_addr + 4 * i)));
    repeat (2) @(posedge clk);
    $finish;
  end
endmodule

`default_nettype wire
