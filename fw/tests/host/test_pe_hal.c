/*
 * Host unit tests for the PE HAL. Expected register values are written out
 * by hand from docs/pe_isa.md section 6, not computed from the HAL's own
 * field table, so a transcription slip in either place shows up.
 *
 *   test_pe_hal                 run the tests
 *   test_pe_hal --encode        read "field=value ..." lines on stdin, print
 *                               the 7 register words (or EINVAL) per line;
 *                               used by tests/test_hal_crosscheck.py
 *   test_pe_hal --tick SYS TICK print "INT FRAC" for pe_cfg_set_tick_hz
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mock.h"
#include "pe_hal.h"

static int failures, checks;
#define EXPECT(cond) do { checks++; if (!(cond)) { failures++; \
    fprintf(stderr, "%s:%d: FAILED: %s\n", __FILE__, __LINE__, #cond); } } while (0)
#define EXPECT_EQ(a, b) do { unsigned long long a_ = (a), b_ = (b); checks++; if (a_ != b_) { \
    failures++; fprintf(stderr, "%s:%d: FAILED: %s == %s (0x%llx != 0x%llx)\n", \
    __FILE__, __LINE__, #a, #b, a_, b_); } } while (0)

#define EB(pe, off) (FW_PE_BASE + 0x100u * (pe) + (off))
#define SH(off) (FW_PE_BASE + 0x400u + (off))

static const struct { const char *name; pe_field_t f; } names[] = {
    {"clkdiv_int", PE_F_CLKDIV_INT}, {"clkdiv_frac", PE_F_CLKDIV_FRAC},
    {"wrap_bottom", PE_F_WRAP_BOTTOM}, {"wrap_top", PE_F_WRAP_TOP}, {"jmp_pin", PE_F_JMP_PIN},
    {"filter", PE_F_FILTER}, {"sync_edge", PE_F_SYNC_EDGE}, {"autosync", PE_F_AUTOSYNC},
    {"side_count", PE_F_SIDE_COUNT}, {"side_opt", PE_F_SIDE_OPT},
    {"side_pindirs", PE_F_SIDE_PINDIR}, {"origin", PE_F_ORIGIN},
    {"in_shift_right", PE_F_IN_SHIFT_RIGHT}, {"out_shift_right", PE_F_OUT_SHIFT_RIGHT},
    {"autopush", PE_F_AUTOPUSH}, {"autopull", PE_F_AUTOPULL},
    {"push_thresh", PE_F_PUSH_THRESH}, {"pull_thresh", PE_F_PULL_THRESH},
    {"out_base", PE_F_OUT_BASE}, {"out_count", PE_F_OUT_COUNT}, {"set_base", PE_F_SET_BASE},
    {"set_count", PE_F_SET_COUNT}, {"side_base", PE_F_SIDE_BASE}, {"in_base", PE_F_IN_BASE},
    {"coder_mode", PE_F_CODER_MODE}, {"coder_diff", PE_F_CODER_DIFF},
    {"coder_idle", PE_F_CODER_IDLE}, {"stuff_mode", PE_F_STUFF_MODE}, {"stuff_n", PE_F_STUFF_N},
    {"bit_ticks", PE_F_BIT_TICKS}, {"crc_width", PE_F_CRC_WIDTH},
    {"crc_init_ones", PE_F_CRC_INIT_ONES}, {"stamp_en", PE_F_STAMP_EN},
    {"stamp_count", PE_F_STAMP_COUNT}, {"crc_reflect", PE_F_CRC_REFLECT},
    {"crc_poly", PE_F_CRC_POLY}, {"timeout", PE_F_TIMEOUT}, {"sync_phase", PE_F_SYNC_PHASE},
};

/* One field at a value on an all-zero image -> the 7 register words. */
static void one(pe_field_t f, uint32_t v, uint32_t r[7])
{
    pe_cfg_t c = {0};
    EXPECT_EQ(pe_cfg_set(&c, f, v), PE_OK);
    r[0] = c.clkdiv; r[1] = c.execctrl; r[2] = c.shiftctrl; r[3] = c.pinctrl;
    r[4] = c.assist; r[5] = c.crc_poly; r[6] = c.timing;
}

static void expect_field(pe_field_t f, uint32_t v, int reg, uint32_t word)
{
    uint32_t r[7];
    one(f, v, r);
    for (int i = 0; i < 7; i++)
        EXPECT_EQ(r[i], i == reg ? word : 0u);
    pe_cfg_t c = {0};
    pe_cfg_set(&c, f, v);
    EXPECT_EQ(pe_cfg_get(&c, f), v);
}

static void test_field_positions(void)
{
    enum { CLK, EXE, SHF, PIN, AST, POLY, TIM };
    /* pe_isa.md 6, field by field */
    expect_field(PE_F_CLKDIV_INT, 0xFFFF, CLK, 0xFFFF0000u);
    expect_field(PE_F_CLKDIV_FRAC, 0xFF, CLK, 0x0000FF00u);
    expect_field(PE_F_WRAP_BOTTOM, 31, EXE, 0x0000001Fu);
    expect_field(PE_F_WRAP_TOP, 31, EXE, 0x000003E0u);
    expect_field(PE_F_JMP_PIN, 31, EXE, 0x00007C00u);
    expect_field(PE_F_FILTER, 1, EXE, 0x00008000u);
    expect_field(PE_F_SYNC_EDGE, 2, EXE, 0x00020000u);
    expect_field(PE_F_AUTOSYNC, 1, EXE, 0x00040000u);
    expect_field(PE_F_SIDE_COUNT, 5, EXE, 0x00280000u);
    expect_field(PE_F_SIDE_OPT, 1, EXE, 0x00400000u);
    expect_field(PE_F_SIDE_PINDIR, 1, EXE, 0x00800000u);
    expect_field(PE_F_ORIGIN, 63, EXE, 0x3F000000u);
    expect_field(PE_F_IN_SHIFT_RIGHT, 1, SHF, 0x1u);
    expect_field(PE_F_OUT_SHIFT_RIGHT, 1, SHF, 0x2u);
    expect_field(PE_F_AUTOPUSH, 1, SHF, 0x4u);
    expect_field(PE_F_AUTOPULL, 1, SHF, 0x8u);
    expect_field(PE_F_PUSH_THRESH, 31, SHF, 0x1F0u);
    expect_field(PE_F_PUSH_THRESH, 32, SHF, 0x0u);       /* 0 = 32 */
    expect_field(PE_F_PULL_THRESH, 31, SHF, 0x3E00u);
    expect_field(PE_F_PULL_THRESH, 32, SHF, 0x0u);
    expect_field(PE_F_OUT_BASE, 31, PIN, 0x1Fu);
    expect_field(PE_F_OUT_COUNT, 32, PIN, 0x400u);
    expect_field(PE_F_SET_BASE, 31, PIN, 0xF800u);
    expect_field(PE_F_SET_COUNT, 5, PIN, 0x50000u);
    expect_field(PE_F_SIDE_BASE, 31, PIN, 0xF80000u);
    expect_field(PE_F_IN_BASE, 31, PIN, 0x1F000000u);
    expect_field(PE_F_CODER_MODE, 3, AST, 0x3u);
    expect_field(PE_F_CODER_DIFF, 1, AST, 0x4u);
    expect_field(PE_F_CODER_IDLE, 1, AST, 0x8u);
    expect_field(PE_F_STUFF_MODE, 2, AST, 0x20u);
    expect_field(PE_F_STUFF_N, 8, AST, 0x1C0u);          /* N-1 */
    expect_field(PE_F_STUFF_N, 1, AST, 0x0u);
    expect_field(PE_F_BIT_TICKS, 16, AST, 0x1E00u);      /* -1 */
    expect_field(PE_F_CRC_WIDTH, 32, AST, 0x3E000u);     /* -1 */
    expect_field(PE_F_CRC_INIT_ONES, 1, AST, 0x40000u);
    expect_field(PE_F_STAMP_EN, 1, AST, 0x80000u);
    expect_field(PE_F_STAMP_COUNT, 16, AST, 0xF00000u);  /* -1 */
    expect_field(PE_F_CRC_REFLECT, 1, AST, 0x1000000u);
    expect_field(PE_F_CRC_POLY, 0xEDB88320u, POLY, 0xEDB88320u);
    expect_field(PE_F_TIMEOUT, 0xFFFF, TIM, 0xFFFFu);
    expect_field(PE_F_SYNC_PHASE, 0xFFFF, TIM, 0xFFFF0000u);
}

static void test_ranges(void)
{
    pe_cfg_t c = {0};
    static const struct { pe_field_t f; uint32_t bad; } bad[] = {
        {PE_F_CLKDIV_INT, 65536}, {PE_F_CLKDIV_FRAC, 256}, {PE_F_WRAP_TOP, 32},
        {PE_F_JMP_PIN, 32}, {PE_F_SYNC_EDGE, 3}, {PE_F_SIDE_COUNT, 6}, {PE_F_ORIGIN, 64},
        {PE_F_PUSH_THRESH, 0}, {PE_F_PUSH_THRESH, 33}, {PE_F_OUT_COUNT, 33},
        {PE_F_SET_COUNT, 6}, {PE_F_IN_BASE, 32}, {PE_F_CODER_MODE, 4}, {PE_F_STUFF_MODE, 3},
        {PE_F_STUFF_N, 0}, {PE_F_STUFF_N, 9}, {PE_F_BIT_TICKS, 0}, {PE_F_BIT_TICKS, 17},
        {PE_F_CRC_WIDTH, 0}, {PE_F_CRC_WIDTH, 33}, {PE_F_STAMP_COUNT, 0},
        {PE_F_STAMP_COUNT, 17}, {PE_F_TIMEOUT, 65536}, {PE_F_SYNC_PHASE, 65536},
        {PE_F_FILTER, 2},
    };
    for (unsigned i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        EXPECT_EQ(pe_cfg_set(&c, bad[i].f, bad[i].bad), (unsigned long long)PE_EINVAL);
        EXPECT_EQ(c.clkdiv | c.execctrl | c.shiftctrl | c.pinctrl | c.assist | c.timing, 0u);
    }
    EXPECT_EQ(pe_cfg_set(&c, PE_F_COUNT, 0), (unsigned long long)PE_EINVAL);
    EXPECT_EQ(pe_cfg_set(NULL, PE_F_ORIGIN, 0), (unsigned long long)PE_EINVAL);
}

static void test_defaults_and_check(void)
{
    pe_cfg_t c;
    pe_cfg_init(&c);
    /* Same images peasm emits for a program with no overrides (cf. the
     * UART_TX_* defaults in its generated header). */
    EXPECT_EQ(c.clkdiv, 0x00010000u);
    EXPECT_EQ(c.execctrl, 0x000003E0u);
    EXPECT_EQ(c.shiftctrl, 0x00000003u);
    EXPECT_EQ(c.pinctrl, 0u);
    EXPECT_EQ(c.assist, 0x0001E140u);
    EXPECT_EQ(c.crc_poly, 0u);
    EXPECT_EQ(c.timing, 0u);
    EXPECT_EQ(pe_cfg_check(&c), PE_OK);

    /* The UART TX image peasm generated from examples/pe/uart.pe */
    pe_cfg_t u;
    pe_cfg_from_regs(&u, 0x00010000u, 0x00500060u, 0x00000003u, 0x00000020u, 0x0001e140u, 0, 0);
    EXPECT_EQ(pe_cfg_check(&u), PE_OK);
    EXPECT_EQ(pe_cfg_get(&u, PE_F_SIDE_COUNT), 2u);  /* 1 pin + opt enable */
    EXPECT_EQ(pe_cfg_get(&u, PE_F_SIDE_OPT), 1u);
    EXPECT_EQ(pe_cfg_get(&u, PE_F_WRAP_TOP), 3u);
    EXPECT_EQ(pe_cfg_get(&u, PE_F_OUT_COUNT), 1u);

    pe_cfg_t r = c;
    r.execctrl |= 1u << 31;                          /* reserved bit */
    EXPECT_EQ(pe_cfg_check(&r), (unsigned long long)PE_EINVAL);
    r = c;
    r.pinctrl |= 6u << 16;                           /* SET_COUNT 6: field fits, value illegal */
    EXPECT_EQ(pe_cfg_check(&r), (unsigned long long)PE_EINVAL);
    r = c;
    r.pinctrl |= 33u << 5;                           /* OUT_COUNT 33 */
    EXPECT_EQ(pe_cfg_check(&r), (unsigned long long)PE_EINVAL);
    r = c;
    r.execctrl |= 3u << 16;                          /* SYNC_EDGE 3 */
    EXPECT_EQ(pe_cfg_check(&r), (unsigned long long)PE_EINVAL);
    r = c;
    pe_cfg_set(&r, PE_F_CLKDIV_INT, 8);
    pe_cfg_set(&r, PE_F_SYNC_PHASE, 8);              /* must be < INT */
    EXPECT_EQ(pe_cfg_check(&r), (unsigned long long)PE_EINVAL);
    pe_cfg_set(&r, PE_F_SYNC_PHASE, 7);
    EXPECT_EQ(pe_cfg_check(&r), PE_OK);
    pe_cfg_set(&r, PE_F_CLKDIV_INT, 0);              /* 65536 */
    pe_cfg_set(&r, PE_F_SYNC_PHASE, 65535);
    EXPECT_EQ(pe_cfg_check(&r), PE_OK);
    r = c;
    pe_cfg_set(&r, PE_F_SIDE_OPT, 1);                /* opt with no side-set bit */
    EXPECT_EQ(pe_cfg_check(&r), (unsigned long long)PE_EINVAL);
}

static void test_tick(void)
{
    pe_cfg_t c;
    pe_cfg_init(&c);
    EXPECT_EQ(pe_cfg_set_tick_hz(&c, 60000000, 921600), PE_OK);  /* 65.1042 */
    EXPECT_EQ(pe_cfg_get(&c, PE_F_CLKDIV_INT), 65u);
    EXPECT_EQ(pe_cfg_get(&c, PE_F_CLKDIV_FRAC), 27u);
    EXPECT_EQ(pe_cfg_set_tick_hz(&c, 60000000, 60000000), PE_OK);
    EXPECT_EQ(pe_cfg_get(&c, PE_F_CLKDIV_INT), 1u);
    EXPECT_EQ(pe_cfg_get(&c, PE_F_CLKDIV_FRAC), 0u);
    EXPECT_EQ(pe_cfg_set_tick_hz(&c, 60000000, 61000000), (unsigned long long)PE_EINVAL);
    EXPECT_EQ(pe_cfg_set_tick_hz(&c, 60000000, 900), (unsigned long long)PE_EINVAL);  /* > 65535 */
    EXPECT_EQ(pe_cfg_set_tick_hz(&c, 60000000, 0), (unsigned long long)PE_EINVAL);
    /* 3069/1024 = 2.99707 -> 767.25/256 -> 2 + 255/256 */
    EXPECT_EQ(pe_cfg_set_tick_hz(&c, 3069, 1024), PE_OK);
    EXPECT_EQ(pe_cfg_get(&c, PE_F_CLKDIV_INT), 2u);
    EXPECT_EQ(pe_cfg_get(&c, PE_F_CLKDIV_FRAC), 255u);
    /* 2.999 -> 767.744 -> 768/256: rounding carries into INT */
    EXPECT_EQ(pe_cfg_set_tick_hz(&c, 2999, 1000), PE_OK);
    EXPECT_EQ(pe_cfg_get(&c, PE_F_CLKDIV_INT), 3u);
    EXPECT_EQ(pe_cfg_get(&c, PE_F_CLKDIV_FRAC), 0u);
}

static void test_configure_start_stop(void)
{
    mock_reset();
    pe_hal_init();
    pe_cfg_t c;
    pe_cfg_init(&c);
    pe_cfg_set(&c, PE_F_ORIGIN, 40);
    int n0 = mock.nlog;
    EXPECT_EQ(pe_configure(2, &c), PE_OK);
    static const uint32_t offs[7] = {0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C};
    const uint32_t vals[7] = {c.clkdiv, c.execctrl, c.shiftctrl, c.pinctrl, c.assist, c.crc_poly, c.timing};
    EXPECT_EQ(mock.nlog - n0, 7);
    for (int i = 0; i < 7; i++) {
        EXPECT_EQ(mock.log[n0 + i].addr, EB(2, offs[i]));
        EXPECT_EQ(mock.log[n0 + i].value, vals[i]);
    }
    n0 = mock.nlog;
    EXPECT_EQ(pe_start(2), PE_OK);
    EXPECT_EQ(mock.nlog - n0, 2);
    EXPECT_EQ(mock.log[n0].addr, EB(2, 0));
    EXPECT_EQ(mock.log[n0].value, 0x6u);                 /* RESTART | DIV_RESTART */
    EXPECT_EQ(mock.log[n0 + 1].value, 0x1u);             /* EN */
    EXPECT_EQ(pe_enabled_mask(), 0x4u);
    n0 = mock.nlog;
    EXPECT_EQ(pe_configure(2, &c), (unsigned long long)PE_EBUSY);
    EXPECT_EQ(mock.nlog, n0);                            /* nothing written */
    pe_cfg_t bad = c;
    bad.timing = 0xFFFFFFFFu;                            /* SYNC_PHASE >= INT */
    EXPECT_EQ(pe_configure(1, &bad), (unsigned long long)PE_EINVAL);
    EXPECT_EQ(pe_configure(4, &c), (unsigned long long)PE_EINVAL);
    EXPECT_EQ(pe_configure(0, NULL), (unsigned long long)PE_EINVAL);
    EXPECT_EQ(mock.nlog, n0);
    EXPECT_EQ(pe_stop(2), PE_OK);
    EXPECT_EQ(mock.log[mock.nlog - 1].value, 0u);
    EXPECT_EQ(pe_enabled_mask(), 0u);
    EXPECT_EQ(pe_start(9), (unsigned long long)PE_EINVAL);
}

static void test_load_program(void)
{
    mock_reset();
    pe_hal_init();
    uint16_t code[8] = {0x1111, 0x2222, 0x3333, 0x4444, 0x5555, 0x6666, 0x7777, 0x8888};
    int n0 = mock.nlog;
    EXPECT_EQ(pe_load_program(60, code, 4), PE_OK);
    EXPECT_EQ(mock.nlog - n0, 4);
    EXPECT_EQ(mock.log[n0].addr, SH(4u * 60));
    EXPECT_EQ(mock.log[n0 + 3].value, 0x4444u);
    EXPECT_EQ(pe_load_program(61, code, 4), (unsigned long long)PE_EINVAL);  /* past 63 */
    EXPECT_EQ(pe_load_program(64, code, 1), (unsigned long long)PE_EINVAL);
    EXPECT_EQ(pe_load_program(0, code, 0), (unsigned long long)PE_EINVAL);
    EXPECT_EQ(pe_load_program(0, NULL, 1), (unsigned long long)PE_EINVAL);

    /* engine 1 runs at ORIGIN 40: its window is 40..63 and 0..7 */
    pe_cfg_t c;
    pe_cfg_init(&c);
    pe_cfg_set(&c, PE_F_ORIGIN, 40);
    pe_configure(1, &c);
    pe_start(1);
    n0 = mock.nlog;
    EXPECT_EQ(pe_load_program(0, code, 4), (unsigned long long)PE_EBUSY);   /* wrapped part */
    EXPECT_EQ(pe_load_program(63, code, 1), (unsigned long long)PE_EBUSY);
    EXPECT_EQ(pe_load_program(36, code, 8), (unsigned long long)PE_EBUSY);  /* 36..43 */
    EXPECT_EQ(mock.nlog, n0);
    EXPECT_EQ(pe_load_program(8, code, 8), PE_OK);                          /* 8..15 */
    EXPECT_EQ(pe_load_program(32, code, 8), PE_OK);                         /* 32..39 */
    pe_stop(1);
    EXPECT_EQ(pe_load_program(0, code, 4), PE_OK);
}

static void test_fifos(void)
{
    mock_reset();
    pe_hal_init();
    uint32_t w = 0;
    /* pe_isa.md 1.1: FIFOs are 2 x 32. TX level 2 is full. Levels above the
     * depth cannot happen; treat them as full too (never write blind). */
    for (uint32_t lvl = 2; lvl <= 15; lvl++) {
        mock.status[0] = lvl << 8;
        EXPECT_EQ(pe_tx_try(0, 0xAB), (unsigned long long)PE_EAGAIN);
    }
    EXPECT_EQ(mock_count_writes(EB(0, 0x24)), 0);
    int n0 = mock.nlog;
    mock.status[0] = 1u << 8;
    EXPECT_EQ(pe_tx_try(0, 0xAB), PE_OK);
    EXPECT_EQ(mock_count_writes(EB(0, 0x24)), 1);
    mock.status[3] = 0;                                  /* RX empty */
    EXPECT_EQ(pe_rx_try(3, &w), (unsigned long long)PE_EAGAIN);
    EXPECT_EQ(mock.rx_pops[3], 0u);                      /* never read an empty FIFO */
    mock.status[3] = 2u << 12;
    mock.rx_value[3] = 0xCAFEF00Du;
    EXPECT_EQ(pe_rx_try(3, &w), PE_OK);
    EXPECT_EQ(w, 0xCAFEF00Du);
    EXPECT_EQ(pe_rx_try(3, NULL), (unsigned long long)PE_EINVAL);
    EXPECT_EQ(pe_tx_try(4, 0), (unsigned long long)PE_EINVAL);
    (void)n0;

    /* blocking calls time out (cycle counter advances 10 per read) */
    mock.status[1] = 2u << 8;                            /* TX full */
    uint64_t t0 = mock.cycles;
    EXPECT_EQ(pe_tx_put(1, 1, 500), (unsigned long long)PE_ETIMEDOUT);
    EXPECT(mock.cycles - t0 >= 500 && mock.cycles - t0 < 700);
    mock.status[1] = 0;
    EXPECT_EQ(pe_rx_get(1, &w, 300), (unsigned long long)PE_ETIMEDOUT);
    EXPECT_EQ(pe_rx_get(1, &w, 0), (unsigned long long)PE_ETIMEDOUT);  /* 0 = try once */
    mock.status[1] = 1u << 12;
    EXPECT_EQ(pe_rx_get(1, &w, 300), PE_OK);

    n0 = mock.nlog;
    EXPECT_EQ(pe_clear_errors(2), PE_OK);
    EXPECT_EQ(mock.log[n0].addr, EB(2, 0x2C));
    EXPECT_EQ(mock.log[n0].value, 0xC0u);                /* SERR | OVF, W1C */
}

static void test_exec(void)
{
    mock_reset();
    mock.status[0] = 1u << 16;                           /* stalled */
    mock.stall_reads[0] = 3;
    EXPECT_EQ(pe_exec(0, 0xE001, 1000), PE_OK);
    uint32_t v;
    EXPECT(mock_last_write(EB(0, 0x20), &v) >= 0 && v == 0xE001u);
    mock.status[0] = 1u << 16;
    mock.stall_reads[0] = 0;                             /* never completes */
    EXPECT_EQ(pe_exec(0, 0xE001, 1000), (unsigned long long)PE_ETIMEDOUT);
}

static void test_events_and_irq(void)
{
    mock_reset();
    pe_hal_init();
    EXPECT_EQ(pe_evt_irq_enable(0x05), PE_OK);
    uint32_t v = 0;
    EXPECT(mock_last_write(SH(0x10C), &v) >= 0 && v == 0x05u);
    EXPECT_EQ(mock.mie, (1u << 16) | (1u << 18));        /* FW_IRQ_ID_PE_EVT(0), (2) */
    EXPECT_EQ(pe_evt_irq_enable(0x80), PE_OK);
    EXPECT(mock_last_write(SH(0x10C), &v) >= 0 && v == 0x85u);
    EXPECT_EQ(pe_evt_irq_disable(0x01), PE_OK);
    EXPECT(mock_last_write(SH(0x10C), &v) >= 0 && v == 0x84u);
    EXPECT_EQ(mock.mie, (1u << 18) | (1u << 23));
    EXPECT_EQ(pe_evt_irq_enable(0x100), (unsigned long long)PE_EINVAL);
    pe_evt_set(0x1FF);
    EXPECT(mock_last_write(SH(0x104), &v) >= 0 && v == 0xFFu);
    pe_evt_clear(0x3);
    EXPECT(mock_last_write(SH(0x108), &v) >= 0 && v == 0x3u);
    mock.evt = 0xFFFFFF42u;
    EXPECT_EQ(pe_evt_read(), 0x42u);
}

static void test_pads(void)
{
    mock_reset();
    pe_hal_init();
    EXPECT_EQ(pe_pad_set_owner(7, PE_OWNER_CPU), (unsigned long long)PE_EINVAL);  /* input pad */
    EXPECT_EQ(pe_pad_set_owner(24, PE_OWNER_CPU), (unsigned long long)PE_EINVAL);
    EXPECT_EQ(pe_pad_set_owner(9, PE_OWNER_SYSTEM), (unsigned long long)PE_EINVAL);
    EXPECT_EQ(pe_pad_set_owner(9, 6), (unsigned long long)PE_EINVAL);
    EXPECT_EQ(pe_pad_set_owner(9, 8), (unsigned long long)PE_EINVAL);
    EXPECT_EQ(pe_pad_set_owner(9, PE_OWNER_PE(3)), PE_OK);
    uint32_t v;
    EXPECT(mock_last_write(SH(0x140 + 4 * (9 - 8)), &v) >= 0 && v == 3u);
    EXPECT_EQ(pe_pad_set_od(16, 1), (unsigned long long)PE_EINVAL);  /* uo_out: no OD */
    EXPECT_EQ(pe_pad_set_od(9, 1), PE_OK);
    EXPECT_EQ(pe_pad_set_od(15, 1), PE_OK);
    EXPECT(mock_last_write(SH(0x118), &v) >= 0 && v == 0x82u);
    EXPECT_EQ(pe_gpio_set_oe(23, 1), (unsigned long long)PE_EINVAL);
    EXPECT_EQ(pe_gpio_write(23, 1), PE_OK);
    EXPECT(mock_last_write(SH(0x11C), &v) >= 0 && v == 0x8000u);
    EXPECT_EQ(pe_gpio_write(23, 2), (unsigned long long)PE_EINVAL);
}

static int idx_of(int from, uint32_t addr)
{
    for (int i = from; i < mock.nlog; i++)
        if (mock.log[i].op == 'W' && mock.log[i].addr == addr)
            return i;
    return -1;
}

/* Must run before any other test touches HAL state: the HAL's shadow is
 * then all zero, as after .bss is cleared at boot. A panic at that point
 * must not take any pad (some may belong to the host bridge). */
static void test_safe_state_before_init(void)
{
    mock_reset();
    pe_safe_state();
    for (uint32_t pad = 8; pad <= 23; pad++)
        EXPECT_EQ(mock_count_writes(SH(0x140 + 4 * (pad - 8))), 0);
    for (uint32_t pe = 0; pe < 4; pe++)
        EXPECT_EQ(mock_count_writes(EB(pe, 0)), 1);    /* engines still stopped */
}

static void test_safe_state(void)
{
    mock_reset();
    pe_hal_init();
    pe_cfg_t c;
    pe_cfg_init(&c);
    pe_configure(1, &c);
    pe_start(1);
    pe_pad_set_owner(9, PE_OWNER_PE(1));     /* I2C-style OD pad */
    pe_pad_set_od(9, 1);
    pe_pad_set_owner(18, PE_OWNER_PE(0));    /* uo_out pad */
    pe_pad_set_owner(11, PE_OWNER_NONE);     /* released by firmware: leave alone */
    /* pad 12 never assigned (could be the host bridge's): leave alone */
    int n0 = mock.nlog;
    pe_safe_state();
    int ctrl_last = -1;
    for (uint32_t pe = 0; pe < 4; pe++) {
        int i = idx_of(n0, EB(pe, 0));
        EXPECT(i >= 0 && mock.log[i].value == 0);
        if (i > ctrl_last)
            ctrl_last = i;
    }
    int irq = idx_of(n0, SH(0x10C));
    int own9 = idx_of(n0, SH(0x140 + 4 * 1)), own18 = idx_of(n0, SH(0x140 + 4 * 10));
    int oe = idx_of(n0, SH(0x120)), od = idx_of(n0, SH(0x118)), out = idx_of(n0, SH(0x11C));
    EXPECT(irq >= 0 && mock.log[irq].value == 0);
    EXPECT(own9 > ctrl_last && mock.log[own9].value == PE_OWNER_CPU);   /* engines stopped first */
    EXPECT(own18 > ctrl_last && mock.log[own18].value == PE_OWNER_CPU);
    EXPECT_EQ(idx_of(n0, SH(0x140 + 4 * 3)), -1);                       /* pad 11 */
    EXPECT_EQ(idx_of(n0, SH(0x140 + 4 * 4)), -1);                       /* pad 12 */
    EXPECT(oe > own9 && oe > own18 && mock.log[oe].value == 0);         /* OE off while still OD */
    EXPECT(od > oe && mock.log[od].value == 0);                         /* then leave OD */
    EXPECT(out > od && mock.log[out].value == 0);
    EXPECT_EQ(pe_enabled_mask(), 0u);
}

static int encode_mode(void)
{
    char line[2048];
    while (fgets(line, sizeof line, stdin)) {
        pe_cfg_t c;
        pe_cfg_init(&c);
        int ok = 1;
        for (char *tok = strtok(line, " \t\n"); tok; tok = strtok(NULL, " \t\n")) {
            char *eq = strchr(tok, '=');
            if (!eq) { ok = 0; break; }
            *eq = 0;
            unsigned i;
            for (i = 0; i < sizeof names / sizeof names[0]; i++)
                if (!strcmp(names[i].name, tok))
                    break;
            if (i == sizeof names / sizeof names[0]) {
                fprintf(stderr, "unknown field %s\n", tok);
                return 2;
            }
            if (pe_cfg_set(&c, names[i].f, (uint32_t)strtoul(eq + 1, NULL, 0)) != PE_OK)
                ok = 0;
        }
        if (ok && pe_cfg_check(&c) != PE_OK)
            ok = 0;
        if (ok)
            printf("%08x %08x %08x %08x %08x %08x %08x\n", c.clkdiv, c.execctrl, c.shiftctrl,
                   c.pinctrl, c.assist, c.crc_poly, c.timing);
        else
            printf("EINVAL\n");
        fflush(stdout);
    }
    return 0;
}

int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "--encode"))
        return encode_mode();
    if (argc > 3 && !strcmp(argv[1], "--tick")) {
        pe_cfg_t c;
        pe_cfg_init(&c);
        if (pe_cfg_set_tick_hz(&c, (uint32_t)strtoul(argv[2], NULL, 0),
                               (uint32_t)strtoul(argv[3], NULL, 0)) != PE_OK)
            printf("EINVAL\n");
        else
            printf("%u %u\n", pe_cfg_get(&c, PE_F_CLKDIV_INT), pe_cfg_get(&c, PE_F_CLKDIV_FRAC));
        return 0;
    }
    test_safe_state_before_init();   /* first: needs untouched HAL state */
    test_field_positions();
    test_ranges();
    test_defaults_and_check();
    test_tick();
    test_configure_start_stop();
    test_load_program();
    test_fifos();
    test_exec();
    test_events_and_irq();
    test_pads();
    test_safe_state();
    printf("test_pe_hal: %d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
