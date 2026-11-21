/*
 * Copyright (c) 2026 singhgaurav1
 * SPDX-License-Identifier: Apache-2.0
 *
 * Host SPI bridge: the boot and debug path from the RP2350 (docs/soc.md).
 *
 * SPI mode 0, MSB first, SCK <= f_clk / 6. The SPI pins are oversampled in
 * the system clock domain (2-flop synchronizers + edge detect), so there is
 * no second clock domain. MISO changes right after the bridge samples a
 * rising SCK edge, i.e. well before the host's next sampling edge.
 *
 * Commands (first byte of a CS-low frame):
 *   0x01 WR_CTRL  <ctrl8>                  ctrl[0] = CPU run (0 holds Ibex in reset)
 *   0x02 WRITE    <addr32> <data32>...     word writes, address auto-increments
 *   0x03 READ     <addr32> <dummy8> ->data32...  word reads, auto-increment
 *   0x05 STATUS   -> <status8>             {3'b0, run_refused, underrun, overrun, cpu_sleep, cpu_run}
 *   0x9F ID       -> <id32>                0x50454D55 "PEMU"
 * Bus accesses have priority over the CPU, so reads and writes also work
 * while the CPU runs (peek/poke debug).
 *
 * Boot guard: starting the CPU (run 0 -> 1) needs a WRITE to SRAM since the
 * CPU last started (or since reset). Otherwise the request is refused, the
 * CPU stays in reset and STATUS.run_refused is set (it reflects the last
 * WR_CTRL). Stopping is always allowed.
 *
 * Frame rules: the command byte decides the frame; an unknown command is
 * ignored up to CS high. Addresses are word addresses (addr[1:0] ignored). A
 * WRITE data word is written only when all 32 bits arrived (a cut word is
 * discarded). CS high ends any frame.
 */
`default_nettype none

module pemu_host_spi (
    input  wire        clk,
    input  wire        rst_n,
    // pins (asynchronous)
    input  wire        sck,
    input  wire        cs_n,
    input  wire        mosi,
    output wire        miso,
    // control
    output reg         cpu_run,
    input  wire        cpu_sleep,
    // bus master
    output reg         bus_req,
    output reg  [31:0] bus_addr,
    output reg         bus_we,
    output reg  [31:0] bus_wdata,
    input  wire        bus_gnt,
    input  wire        bus_rvalid,
    input  wire [31:0] bus_rdata
);
  localparam [7:0] CMD_WR_CTRL = 8'h01, CMD_WRITE = 8'h02, CMD_READ = 8'h03,
                   CMD_STATUS = 8'h05, CMD_ID = 8'h9F;
  localparam [31:0] ID_VALUE = 32'h50454D55;

  localparam [2:0] S_CMD = 3'd0, S_CTRL = 3'd1, S_ADDR = 3'd2, S_WDATA = 3'd3,
                   S_DUMMY = 3'd4, S_RDATA = 3'd5, S_IGNORE = 3'd6;

  // ---- pin synchronization and edge detection --------------------------
  wire sck_s, cs_n_s, mosi_s;
  cell_sync2 #(.WIDTH(3), .RESET_VALUE(3'b010)) u_sync (
      .clk(clk), .rst_n(rst_n), .d({sck, cs_n, mosi}), .q({sck_s, cs_n_s, mosi_s})
  );
  reg sck_q;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) sck_q <= 1'b0;
    else sck_q <= sck_s;
  wire rise = sck_s & ~sck_q & ~cs_n_s;

  // ---- frame state -----------------------------------------------------
  reg [2:0]  state;
  reg [4:0]  bit_cnt;      // bits received in the current field, minus one
  reg [7:0]  cmd;
  reg [31:0] sh_in;
  reg [31:0] sh_out;
  reg [31:0] rd_buf;
  reg        rd_buf_valid;
  reg        overrun, underrun;
  reg        first_word;   // WRITE: the next data word goes to the start address
  reg        loaded;       // an SRAM word was written since the CPU last started
  reg        run_refused;  // the last WR_CTRL asked to start without a load

  wire [31:0] sh_next = {sh_in[30:0], mosi_s};
  wire byte_done = rise & (bit_cnt[2:0] == 3'd7);
  wire word_done = rise & (bit_cnt == 5'd31);

  assign miso = sh_out[31];

  wire [7:0] status = {3'b0, run_refused, underrun, overrun, cpu_sleep, cpu_run};

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state        <= S_CMD;
      bit_cnt      <= 5'd0;
      cmd          <= 8'h00;
      sh_in        <= 32'h0;
      sh_out       <= 32'h0;
      rd_buf       <= 32'h0;
      rd_buf_valid <= 1'b0;
      overrun      <= 1'b0;
      underrun     <= 1'b0;
      first_word   <= 1'b0;
      loaded       <= 1'b0;
      run_refused  <= 1'b0;
      cpu_run      <= 1'b0;
      bus_req      <= 1'b0;
      bus_addr     <= 32'h0;
      bus_we       <= 1'b0;
      bus_wdata    <= 32'h0;
    end else begin
      // bus handshake: hold the request until granted; capture read data
      if (bus_req && bus_gnt) begin
        bus_req <= 1'b0;
        if (bus_we && bus_addr[31:28] == 4'h0) loaded <= 1'b1;
      end
      if (bus_rvalid && !bus_we) begin
        rd_buf       <= bus_rdata;
        rd_buf_valid <= 1'b1;
      end

      if (cs_n_s) begin
        state   <= S_CMD;
        bit_cnt <= 5'd0;
        sh_out  <= 32'h0;
      end else if (rise) begin
        sh_in   <= sh_next;
        sh_out  <= {sh_out[30:0], 1'b0};
        bit_cnt <= bit_cnt + 5'd1;
        case (state)
          S_CMD:
          if (byte_done) begin
            cmd     <= sh_next[7:0];
            bit_cnt <= 5'd0;
            case (sh_next[7:0])
              CMD_WR_CTRL: state <= S_CTRL;
              CMD_WRITE, CMD_READ: state <= S_ADDR;
              CMD_STATUS: begin
                sh_out <= {status, 24'h0};
                state  <= S_IGNORE;
              end
              CMD_ID: begin
                sh_out <= ID_VALUE;
                state  <= S_IGNORE;
              end
              default: state <= S_IGNORE;
            endcase
          end
          S_CTRL:
          if (byte_done) begin
            run_refused <= 1'b0;
            if (!sh_next[0]) cpu_run <= 1'b0;
            else if (!cpu_run) begin
              if (loaded) begin
                cpu_run <= 1'b1;
                loaded  <= 1'b0;
              end else run_refused <= 1'b1;
            end
            state <= S_IGNORE;
          end
          S_ADDR:
          if (word_done) begin
            bus_addr <= {sh_next[31:2], 2'b00};
            if (cmd == CMD_READ) begin
              bus_we       <= 1'b0;
              bus_req      <= 1'b1;
              rd_buf_valid <= 1'b0;
              state        <= S_DUMMY;
            end else begin
              first_word <= 1'b1;
              state      <= S_WDATA;
            end
          end
          S_WDATA:
          if (word_done) begin
            if (bus_req) overrun <= 1'b1;
            if (!first_word) bus_addr <= bus_addr + 32'd4;
            first_word <= 1'b0;
            bus_we    <= 1'b1;
            bus_wdata <= sh_next;
            bus_req   <= 1'b1;
          end
          S_DUMMY, S_RDATA:
          if ((state == S_DUMMY && byte_done) || (state == S_RDATA && word_done)) begin
            bit_cnt      <= 5'd0;
            state        <= S_RDATA;
            sh_out       <= rd_buf;
            if (!rd_buf_valid) underrun <= 1'b1;
            rd_buf_valid <= 1'b0;
            bus_addr     <= bus_addr + 32'd4;  // prefetch the next word
            bus_req      <= 1'b1;
          end
          default: ;
        endcase
      end
    end
  end
endmodule
