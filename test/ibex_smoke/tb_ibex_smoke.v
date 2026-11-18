// Smoke test for the converted Ibex (src/ibex_small.v): run gen_prog.py's
// program from a zero-wait-state memory and check the stored results.
`timescale 1ns / 1ps
`default_nettype none

module tb_ibex_smoke;
  localparam [3:0] MUBI_ON = 4'b0101, MUBI_OFF = 4'b1010;

  reg clk = 0, rst_n = 0;
  always #8.333 clk = ~clk;  // 60 MHz

  reg  [31:0] mem [0:511];
  wire        instr_req, data_req, data_we;
  wire [31:0] instr_addr, data_addr, data_wdata;
  wire [3:0]  data_be;
  reg         instr_rvalid = 0, data_rvalid = 0;
  reg  [31:0] instr_rdata = 0, data_rdata = 0;

  ibex_small_top dut (
    .clk_i(clk), .rst_ni(rst_n), .test_en_i(1'b0), .scan_rst_ni(1'b1),
    .ram_cfg_icache_tag_i(24'h0), .ram_cfg_icache_data_i(24'h0),
    .ram_cfg_icache_tag_o(), .ram_cfg_icache_data_o(),
    .cheriot_enable_i(MUBI_OFF), .hart_id_i(32'h0), .boot_addr_i(32'h0),
    .trvk_heap_base_addr_i(32'h0),
    .instr_req_o(instr_req), .instr_gnt_i(instr_req), .instr_rvalid_i(instr_rvalid),
    .instr_addr_o(instr_addr), .instr_rdata_i(instr_rdata), .instr_rdata_intg_i(7'h0),
    .instr_err_i(1'b0),
    .data_req_o(data_req), .data_gnt_i(data_req), .data_rvalid_i(data_rvalid),
    .data_we_o(data_we), .data_be_o(data_be), .data_addr_o(data_addr), .data_wdata_o(data_wdata),
    .data_wdata_intg_o(), .data_rdata_i(data_rdata), .data_rdata_intg_i(7'h0), .data_err_i(1'b0),
    .data_tag_o(), .data_tag_i(1'b0),
    .trvk_revbm_req_o(), .trvk_revbm_gnt_i(1'b0), .trvk_revbm_rvalid_i(1'b0),
    .trvk_revbm_addr_o(), .trvk_revbm_rdata_i(32'h0), .trvk_revbm_rdata_intg_i(7'h0),
    .trvk_revbm_err_i(1'b0),
    .irq_software_i(1'b0), .irq_timer_i(1'b0), .irq_external_i(1'b0), .irq_fast_i(15'h0),
    .irq_nm_i(1'b0),
    .scramble_key_valid_i(1'b0), .scramble_key_i(128'h0), .scramble_nonce_i(64'h0),
    .scramble_req_o(),
    .debug_req_i(1'b0), .crash_dump_o(), .double_fault_seen_o(),
    .fetch_enable_i(MUBI_ON), .mcounteren_writable_i(MUBI_ON),
    .alert_minor_o(), .alert_major_internal_o(), .alert_major_bus_o(), .core_sleep_o(),
    .lockstep_cmp_en_o(),
    .instr_req_shadow_o(), .instr_addr_shadow_o(), .data_req_shadow_o(), .data_we_shadow_o(),
    .data_be_shadow_o(), .data_addr_shadow_o(), .data_wdata_shadow_o(),
    .data_wdata_intg_shadow_o()
  );

  // Memory: grant immediately, respond the next cycle. Byte-enabled writes.
  integer k;
  always @(posedge clk) begin
    instr_rvalid <= instr_req;
    data_rvalid  <= data_req;
    if (instr_req) instr_rdata <= mem[instr_addr[10:2]];
    if (data_req) begin
      data_rdata <= mem[data_addr[10:2]];
      if (data_we)
        for (k = 0; k < 4; k = k + 1)
          if (data_be[k]) mem[data_addr[10:2]][8*k +: 8] <= data_wdata[8*k +: 8];
    end
  end

  reg [63:0] exp_tab [0:15];
  integer i, cycles, errors, nexp;
  initial begin
    $readmemh("prog.hex", mem);
    for (i = 0; i < 16; i = i + 1) exp_tab[i] = 64'hffffffff_ffffffff;
    $readmemh("expect.hex", exp_tab);
    repeat (5) @(posedge clk);
    rst_n = 1;
    cycles = 0;
    while (mem[127] !== 32'd1 && cycles < 2000) begin
      @(posedge clk);
      cycles = cycles + 1;
    end
    errors = 0;
    nexp = 0;
    for (i = 0; i < 16; i = i + 1)
      if (exp_tab[i] !== 64'hffffffff_ffffffff) begin
        nexp = nexp + 1;
        if (mem[exp_tab[i][40:32]] !== exp_tab[i][31:0]) begin
          $display("FAIL word 0x%03x: got %08x expected %08x",
                   exp_tab[i][40:32] * 4, mem[exp_tab[i][40:32]], exp_tab[i][31:0]);
          errors = errors + 1;
        end
      end
    if (errors == 0 && nexp > 0) $display("PASS: %0d checks, program done in %0d cycles", nexp, cycles);
    else $display("FAIL: %0d errors", errors);
    $finish;
  end
endmodule
