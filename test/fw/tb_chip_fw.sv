// Full-chip firmware testbench: the Tiny Tapeout top level with a model of
// the RP2350 loader on the host SPI pins. Owned by the firmware chat.
//
// Written from docs/soc.md ("Host SPI protocol", memory map) and info.yaml
// (pinout) only. The loader does what the real RP2350 must do: check the ID,
// hold the CPU, write the image, read it back and verify, clear the result
// mailbox, start the CPU, then poll SCRATCH through the bridge while the CPU
// runs. The design is a black box behind its pins.
//
// Pins (info.yaml): ui_in[5] MOSI, ui_in[6] CS_N, ui_in[7] SCK, uo_out[7] MISO.
// GPIO wiring in this bench (documented for fw/apps/chiptest):
//   ui_in[4:0] = uo_out[4:0] ^ 5'h15
//   uio_in     = (uio_out & uio_oe) | (8'hC3 & ~uio_oe)    (external pulls)
//
// Plusargs:
//   +img=<file> +img_len=<bytes>  image (byte hex, fw/scripts/mkimage.py format)
//   +load_len=<bytes>   load only the first n bytes (truncated-load test)
//   +no_verify          skip the read-back check (needed with +load_len)
//   +restart_at=<n>     n cycles after start: clear and set CTRL.run again
//                       without reloading. docs/soc.md boot guard: the bridge
//                       refuses (STATUS.run_refused) unless SRAM was written
//                       since the last start. Prints "RESTART run_refused=..
//                       cpu_run=..".
//   +restart_poke       before that restart, write one SRAM word (the status
//                       block's last word): satisfies the boot guard without a
//                       reload, so the firmware's own stale-image check runs
//   +rerun              after the first result: hold, reload, verify, run again
//   +sck_half=<n>       SCK half period in clocks (default 3: SCK = clk/6 = 10 MHz)
//   +poll=<n>           cycles between SCRATCH polls (default 2000)
//   +max_cycles=<n>     timeout (default 3,000,000)
//   +dump=<addr>        at the end, read 16 words from addr (hex) over SPI
//   +dump2=<addr> +dump2_n=<n>, +dump3=<addr> +dump3_n=<n>
//                       then n words from each (hex address), printed as REG lines
//   +bridge_tests       while the CPU runs (fw/apps/bridgetest): peek the
//                       status-block heartbeat twice, send junk frames, check
//                       the ID, record unspecified cases (aborted and
//                       unaligned WRITE) as NOTE lines, then poke the mailbox
//   +ram=<bytes>        SRAM size for status-block addresses (default 4096)
//
// Result line (same as tb_ibex_fw): SCRATCH 1 -> "PASS", odd -> "FAIL code=n"
// (n = SCRATCH >> 1), other non-zero -> "FAIL code=-4". Loader errors are
// negative codes below -9.

`timescale 1ns / 1ps
`default_nettype none

module tb_chip_fw;
  localparam logic [31:0] ID_VALUE = 32'h50454D55;
  localparam logic [31:0] SCRATCH  = 32'h1000_0018;

  logic clk = 0, rst_n = 1;
  always #8.333 clk = ~clk;  // 60 MHz

  logic [7:0] ui_in, uo_out, uio_in, uio_out, uio_oe;
  logic sck = 0, cs_n = 1, mosi = 0;
  wire  miso = uo_out[7];

  assign ui_in  = {sck, cs_n, mosi, uo_out[4:0] ^ 5'h15};
  assign uio_in = (uio_out & uio_oe) | (8'hC3 & ~uio_oe);

  tt_um_singhgaurav1_protoemu dut (
    .ui_in(ui_in), .uo_out(uo_out), .uio_in(uio_in), .uio_out(uio_out), .uio_oe(uio_oe),
    .ena(1'b1), .clk(clk), .rst_n(rst_n)
  );

  // ---------------------------------------------------------------- options
  string img;
  int img_len = -1, load_len = -1, restart_at = -1, sck_half = 3, poll = 2000;
  int max_cycles = 3000000, dump_addr = -1, ram = 4096;
  int dump2 = -1, dump2_n = 1, dump3 = -1, dump3_n = 1;
  bit no_verify = 0, rerun = 0, bridge_tests = 0, bridge_done = 0, restart_poke = 0;
  logic [7:0] image [0:65535];
  longint cycles = 0;
  always @(posedge clk) cycles <= cycles + 1;

  // ---------------------------------------------------------------- SPI host
  // Mode 0, MSB first. The host changes MOSI while SCK is low and samples MISO
  // at the rising edge, like the RP2350's SPI controller.
  task automatic spi_byte(input logic [7:0] tx, output logic [7:0] rx);
    for (int i = 7; i >= 0; i--) begin
      @(negedge clk) mosi = tx[i];
      repeat (sck_half) @(negedge clk);
      rx[i] = miso;
      sck = 1;
      repeat (sck_half) @(negedge clk);
      sck = 0;
    end
  endtask

  task automatic cs_begin();
    @(negedge clk) cs_n = 0;
    repeat (sck_half) @(negedge clk);
  endtask

  task automatic cs_end();
    repeat (sck_half) @(negedge clk);
    cs_n = 1;
    repeat (4 * sck_half) @(negedge clk);
  endtask

  task automatic spi_word_out(input logic [31:0] w);
    logic [7:0] r;
    for (int b = 3; b >= 0; b--) spi_byte(w[8*b +: 8], r);
  endtask

  task automatic spi_word_in(output logic [31:0] w);
    logic [7:0] r;
    for (int b = 3; b >= 0; b--) begin
      spi_byte(8'h00, r);
      w[8*b +: 8] = r;
    end
  endtask

  task automatic cmd_id(output logic [31:0] id);
    logic [7:0] r;
    cs_begin(); spi_byte(8'h9F, r); spi_word_in(id); cs_end();
  endtask

  task automatic cmd_status(output logic [7:0] st);
    logic [7:0] r;
    cs_begin(); spi_byte(8'h05, r); spi_byte(8'h00, st); cs_end();
  endtask

  task automatic cmd_ctrl(input logic [7:0] v);
    logic [7:0] r;
    cs_begin(); spi_byte(8'h01, r); spi_byte(v, r); cs_end();
  endtask

  task automatic cmd_write(input logic [31:0] addr, input logic [31:0] data[$]);
    logic [7:0] r;
    cs_begin(); spi_byte(8'h02, r); spi_word_out(addr);
    foreach (data[i]) spi_word_out(data[i]);
    cs_end();
  endtask

  task automatic cmd_read(input logic [31:0] addr, input int n, output logic [31:0] data[$]);
    logic [7:0] r;
    logic [31:0] w;
    data = {};
    cs_begin(); spi_byte(8'h03, r); spi_word_out(addr); spi_byte(8'h00, r);
    for (int i = 0; i < n; i++) begin
      spi_word_in(w);
      data.push_back(w);
    end
    cs_end();
  endtask

  function automatic logic [31:0] img_word(input int i);
    return {image[4*i + 3], image[4*i + 2], image[4*i + 1], image[4*i]};
  endfunction

  // ---------------------------------------------------------------- loader
  int errors = 0;
  bit finished = 0;

  task automatic fail(input int code, input string why);
    if (!finished) $display("FAIL code=%0d %s (cycle %0d)", code, why, cycles);
    finished = 1;
  endtask

  task automatic load_image();
    logic [31:0] chunk[$], back[$];
    int words = ((load_len >= 0 ? load_len : img_len) + 3) / 4;
    cmd_ctrl(8'h00);                       // hold the CPU (already held after reset)
    for (int base = 0; base < words; base += 64) begin
      chunk = {};
      for (int i = base; i < words && i < base + 64; i++) chunk.push_back(img_word(i));
      cmd_write(32'(4 * base), chunk);
    end
    if (!no_verify)
      for (int base = 0; base < words; base += 64) begin
        int n = (words - base) < 64 ? (words - base) : 64;
        cmd_read(32'(4 * base), n, back);
        for (int i = 0; i < n; i++)
          if (back[i] !== img_word(base + i)) begin
            $display("VERIFY word %0d: read 0x%08x expected 0x%08x", base + i, back[i],
                     img_word(base + i));
            fail(-10, "image read-back mismatch");
            return;
          end
      end
    chunk = {32'h0};
    cmd_write(SCRATCH, chunk);             // clear the result mailbox
    $display("tb_chip_fw: loaded %0d words%s, cycle %0d", words,
             no_verify ? "" : " and verified", cycles);
  endtask

  // Debug access while the CPU runs (fw/apps/bridgetest). Status block:
  // heartbeat word 14, reserved (mailbox) word 15 (fw/include/fw.h).
  task automatic bridge_sequence();
    logic [31:0] d[$], hb1, hb2, id, st_base, w[$];
    logic [7:0] r;
    st_base = 32'(ram - 64);
    cmd_read(st_base + 56, 1, d); hb1 = d[0];
    repeat (20000) @(negedge clk);
    cmd_read(st_base + 56, 1, d); hb2 = d[0];
    $display("tb_chip_fw: heartbeat 0x%08x -> 0x%08x while running", hb1, hb2);
    if (hb2 <= hb1) begin fail(-20, "heartbeat did not advance (peek while running)"); return; end
    // Junk frames: unknown commands, then a frame aborted after 2 data bytes
    cs_begin(); spi_byte(8'hFF, r); for (int i = 0; i < 4; i++) spi_byte(8'h00, r); cs_end();
    cs_begin(); spi_byte(8'h00, r); for (int i = 0; i < 4; i++) spi_byte(8'hA5, r); cs_end();
    cmd_id(id);
    if (id !== ID_VALUE) begin fail(-21, $sformatf("ID 0x%08x after junk frames", id)); return; end
    // docs/soc.md frame rules: a word cut short by CS high is discarded
    cs_begin(); spi_byte(8'h02, r); spi_word_out(st_base + 60); spi_byte(8'hAB, r); spi_byte(8'hCD, r);
    cs_end();
    cmd_read(st_base + 60, 1, d);
    if (d[0] !== 32'h0) begin
      fail(-22, $sformatf("aborted WRITE changed memory: 0x%08x", d[0])); return;
    end
    // ... and addr[1:0] are ignored: a WRITE at +62 lands on the word at +60
    w = {32'h1122_3344};
    cmd_write(st_base + 62, w);
    cmd_read(st_base + 60, 1, d);
    if (d[0] !== 32'h1122_3344) begin
      fail(-23, $sformatf("unaligned WRITE: word at +60 = 0x%08x", d[0])); return;
    end
    // Poke the mailbox: the firmware finishes when it sees this
    w = {32'hC0FF_EE01};
    cmd_write(st_base + 60, w);
    $display("tb_chip_fw: mailbox poked, cycle %0d", cycles);
  endtask

  // Run until SCRATCH is non-zero; returns it (0 on timeout).
  task automatic run_and_poll(output logic [31:0] result);
    logic [31:0] d[$];
    longint t_start, t_next_restart;
    cmd_ctrl(8'h01);
    t_start = cycles;
    t_next_restart = restart_at >= 0 ? t_start + restart_at : -1;
    result = 0;
    while (cycles < longint'(max_cycles)) begin
      repeat (poll) @(negedge clk);
      if (t_next_restart >= 0 && cycles >= t_next_restart) begin
        logic [7:0] st;
        logic [31:0] w[$];
        $display("tb_chip_fw: CTRL.run 0 then 1 without reload%s, cycle %0d",
                 restart_poke ? " (one SRAM word written)" : "", cycles);
        cmd_ctrl(8'h00);
        if (restart_poke) begin
          w = {32'h0};
          cmd_write(32'(ram - 4), w);
        end
        cmd_ctrl(8'h01);
        cmd_status(st);
        $display("RESTART run_refused=%0d cpu_run=%0d", st[4], st[0]);
        t_next_restart = -1;
      end
      if (bridge_tests && !bridge_done && cycles >= t_start + 60000) begin
        bridge_done = 1;
        bridge_sequence();
        if (finished) return;
      end
      cmd_read(SCRATCH, 1, d);
      if (d[0] != 0) begin
        result = d[0];
        $display("tb_chip_fw: SCRATCH=0x%08x after %0d cycles", result, cycles - t_start);
        return;
      end
    end
  endtask

  task automatic report(input logic [31:0] result);
    if (result == 0) fail(-3, $sformatf("timeout after %0d cycles", cycles));
    else if (result == 1) begin
      if (!finished) $display("PASS cycles=%0d", cycles);
      finished = 1;
    end else if (result[0]) fail(int'(result >> 1), $sformatf("(scratch=0x%08x)", result));
    else fail(-4, $sformatf("non-protocol SCRATCH value 0x%08x", result));
  endtask

  initial begin
    logic [31:0] id, result, d[$];
    logic [7:0] st;
    if (!$value$plusargs("img=%s", img) || !$value$plusargs("img_len=%d", img_len)) begin
      $display("FAIL code=-2 need +img=<file> +img_len=<bytes>");
      $finish;
    end
    void'($value$plusargs("load_len=%d", load_len));
    void'($value$plusargs("restart_at=%d", restart_at));
    void'($value$plusargs("sck_half=%d", sck_half));
    void'($value$plusargs("poll=%d", poll));
    void'($value$plusargs("max_cycles=%d", max_cycles));
    void'($value$plusargs("dump=%h", dump_addr));
    void'($value$plusargs("dump2=%h", dump2));
    void'($value$plusargs("dump2_n=%d", dump2_n));
    void'($value$plusargs("dump3=%h", dump3));
    void'($value$plusargs("dump3_n=%d", dump3_n));
    no_verify = $test$plusargs("no_verify");
    rerun = $test$plusargs("rerun");
    restart_poke = $test$plusargs("restart_poke");
    bridge_tests = $test$plusargs("bridge_tests");
    void'($value$plusargs("ram=%d", ram));
    for (int i = 0; i < 65536; i++) image[i] = 8'h00;
    $readmemh(img, image);

    // Real reset edge (asynchronous-reset flops behind gated clocks)
    #1 rst_n = 0;
    repeat (10) @(negedge clk);
    rst_n = 1;
    repeat (10) @(negedge clk);

    cmd_id(id);
    if (id !== ID_VALUE) fail(-11, $sformatf("ID 0x%08x", id));
    cmd_status(st);
    if (st[0]) fail(-12, "CPU running after reset");

    if (!finished) load_image();
    if (!finished) begin
      run_and_poll(result);
      if (rerun && result == 1) begin
        $display("tb_chip_fw: rerun: hold, reload, run again");
        load_image();
        if (!finished) run_and_poll(result);
      end
    end

    cmd_status(st);
    if (st[3:2] != 0) fail(-13, $sformatf("bridge error status 0x%02x", st));
    $display("PINS uo_out=0x%02x uio_oe=0x%02x uio_out=0x%02x status=0x%02x",
             uo_out, uio_oe, uio_out, st);
    if (dump_addr >= 0) begin
      cmd_read(32'(dump_addr), 16, d);
      foreach (d[i]) $display("MEM 0x%08x 0x%08x", dump_addr + 4 * i, d[i]);
    end
    if (dump2 >= 0) begin
      cmd_read(32'(dump2), dump2_n, d);
      foreach (d[i]) $display("REG 0x%08x 0x%08x", dump2 + 4 * i, d[i]);
    end
    if (dump3 >= 0) begin
      cmd_read(32'(dump3), dump3_n, d);
      foreach (d[i]) $display("REG 0x%08x 0x%08x", dump3 + 4 * i, d[i]);
    end
    cmd_status(st);
    if (st[3:2] != 0) fail(-14, $sformatf("bridge error status 0x%02x after dumps", st));
    if (!finished) report(result);
    $finish;
  end
endmodule

`default_nettype wire
