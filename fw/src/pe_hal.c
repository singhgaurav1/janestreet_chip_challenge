/* Protocol Engine HAL. See pe_hal.h. Written from docs/pe_isa.md only. */
#include <stdint.h>

#include "csr.h"
#include "fw.h"
#include "irq.h"
#include "mmio.h"
#include "pe_hal.h"
#include "pe_regs.h"
#include "platform.h"

/* ------------------------------------------------------------- field table */
enum { R_CLKDIV, R_EXECCTRL, R_SHIFTCTRL, R_PINCTRL, R_ASSIST, R_CRC_POLY, R_TIMING, R_NUM };
enum { ENC_ID, ENC_M1, ENC_Z32 };  /* value, value-1, 32 stored as 0 */

/* One packed word per field (4 bytes instead of 12; firmware RAM is tight):
 *   [2:0] register  [7:3] lsb  [13:8] width  [15:14] encoding
 *   [21:16] max override (0 = derived: (1 << width) for N-1 and 0=32
 *           encodings, else all ones in width)
 * The minimum is 1 for the N-1 and 0=32 encodings, else 0. */
typedef uint32_t field_t;
#define FD(reg, lsb, width, enc, maxov) \
    ((uint32_t)(reg) | (uint32_t)(lsb) << 3 | (uint32_t)(width) << 8 | (uint32_t)(enc) << 14 | \
     (uint32_t)(maxov) << 16)
#define FD_REG(f)   ((f) & 7u)
#define FD_LSB(f)   ((f) >> 3 & 31u)
#define FD_WIDTH(f) ((f) >> 8 & 63u)
#define FD_ENC(f)   ((f) >> 14 & 3u)
#define FD_MAXOV(f) ((f) >> 16 & 63u)

static const field_t fields[PE_F_COUNT] = {
    [PE_F_CLKDIV_INT] = FD(R_CLKDIV, PE_CLKDIV_INT_LSB, PE_CLKDIV_INT_WIDTH, ENC_ID, 0),
    [PE_F_CLKDIV_FRAC] = FD(R_CLKDIV, PE_CLKDIV_FRAC_LSB, PE_CLKDIV_FRAC_WIDTH, ENC_ID, 0),
    [PE_F_WRAP_BOTTOM] = FD(R_EXECCTRL, PE_EXECCTRL_WRAP_BOTTOM_LSB, PE_EXECCTRL_WRAP_BOTTOM_WIDTH, ENC_ID, 0),
    [PE_F_WRAP_TOP] = FD(R_EXECCTRL, PE_EXECCTRL_WRAP_TOP_LSB, PE_EXECCTRL_WRAP_TOP_WIDTH, ENC_ID, 0),
    [PE_F_JMP_PIN] = FD(R_EXECCTRL, PE_EXECCTRL_JMP_PIN_LSB, PE_EXECCTRL_JMP_PIN_WIDTH, ENC_ID, 0),
    [PE_F_FILTER] = FD(R_EXECCTRL, PE_EXECCTRL_FILTER_LSB, PE_EXECCTRL_FILTER_WIDTH, ENC_ID, 0),
    [PE_F_SYNC_EDGE] = FD(R_EXECCTRL, PE_EXECCTRL_SYNC_EDGE_LSB, PE_EXECCTRL_SYNC_EDGE_WIDTH, ENC_ID, 2),
    [PE_F_AUTOSYNC] = FD(R_EXECCTRL, PE_EXECCTRL_AUTOSYNC_LSB, PE_EXECCTRL_AUTOSYNC_WIDTH, ENC_ID, 0),
    [PE_F_SIDE_COUNT] = FD(R_EXECCTRL, PE_EXECCTRL_SIDE_COUNT_LSB, PE_EXECCTRL_SIDE_COUNT_WIDTH, ENC_ID, 5),
    [PE_F_SIDE_OPT] = FD(R_EXECCTRL, PE_EXECCTRL_SIDE_OPT_LSB, PE_EXECCTRL_SIDE_OPT_WIDTH, ENC_ID, 0),
    [PE_F_SIDE_PINDIR] = FD(R_EXECCTRL, PE_EXECCTRL_SIDE_PINDIR_LSB, PE_EXECCTRL_SIDE_PINDIR_WIDTH, ENC_ID, 0),
    [PE_F_ORIGIN] = FD(R_EXECCTRL, PE_EXECCTRL_ORIGIN_LSB, PE_EXECCTRL_ORIGIN_WIDTH, ENC_ID, 0),
    [PE_F_IN_SHIFT_RIGHT] = FD(R_SHIFTCTRL, PE_SHIFTCTRL_IN_SHIFTDIR_LSB, PE_SHIFTCTRL_IN_SHIFTDIR_WIDTH, ENC_ID, 0),
    [PE_F_OUT_SHIFT_RIGHT] = FD(R_SHIFTCTRL, PE_SHIFTCTRL_OUT_SHIFTDIR_LSB, PE_SHIFTCTRL_OUT_SHIFTDIR_WIDTH, ENC_ID, 0),
    [PE_F_AUTOPUSH] = FD(R_SHIFTCTRL, PE_SHIFTCTRL_AUTOPUSH_LSB, PE_SHIFTCTRL_AUTOPUSH_WIDTH, ENC_ID, 0),
    [PE_F_AUTOPULL] = FD(R_SHIFTCTRL, PE_SHIFTCTRL_AUTOPULL_LSB, PE_SHIFTCTRL_AUTOPULL_WIDTH, ENC_ID, 0),
    [PE_F_PUSH_THRESH] = FD(R_SHIFTCTRL, PE_SHIFTCTRL_PUSH_THRESH_LSB, PE_SHIFTCTRL_PUSH_THRESH_WIDTH, ENC_Z32, 0),
    [PE_F_PULL_THRESH] = FD(R_SHIFTCTRL, PE_SHIFTCTRL_PULL_THRESH_LSB, PE_SHIFTCTRL_PULL_THRESH_WIDTH, ENC_Z32, 0),
    [PE_F_OUT_BASE] = FD(R_PINCTRL, PE_PINCTRL_OUT_BASE_LSB, PE_PINCTRL_OUT_BASE_WIDTH, ENC_ID, 0),
    [PE_F_OUT_COUNT] = FD(R_PINCTRL, PE_PINCTRL_OUT_COUNT_LSB, PE_PINCTRL_OUT_COUNT_WIDTH, ENC_ID, 32),
    [PE_F_SET_BASE] = FD(R_PINCTRL, PE_PINCTRL_SET_BASE_LSB, PE_PINCTRL_SET_BASE_WIDTH, ENC_ID, 0),
    [PE_F_SET_COUNT] = FD(R_PINCTRL, PE_PINCTRL_SET_COUNT_LSB, PE_PINCTRL_SET_COUNT_WIDTH, ENC_ID, 5),
    [PE_F_SIDE_BASE] = FD(R_PINCTRL, PE_PINCTRL_SIDE_BASE_LSB, PE_PINCTRL_SIDE_BASE_WIDTH, ENC_ID, 0),
    [PE_F_IN_BASE] = FD(R_PINCTRL, PE_PINCTRL_IN_BASE_LSB, PE_PINCTRL_IN_BASE_WIDTH, ENC_ID, 0),
    [PE_F_CODER_MODE] = FD(R_ASSIST, PE_ASSIST_CODER_MODE_LSB, PE_ASSIST_CODER_MODE_WIDTH, ENC_ID, 0),
    [PE_F_CODER_DIFF] = FD(R_ASSIST, PE_ASSIST_CODER_DIFF_LSB, PE_ASSIST_CODER_DIFF_WIDTH, ENC_ID, 0),
    [PE_F_CODER_IDLE] = FD(R_ASSIST, PE_ASSIST_CODER_IDLE_LSB, PE_ASSIST_CODER_IDLE_WIDTH, ENC_ID, 0),
    [PE_F_STUFF_MODE] = FD(R_ASSIST, PE_ASSIST_STUFF_MODE_LSB, PE_ASSIST_STUFF_MODE_WIDTH, ENC_ID, 2),
    [PE_F_STUFF_N] = FD(R_ASSIST, PE_ASSIST_STUFF_N_LSB, PE_ASSIST_STUFF_N_WIDTH, ENC_M1, 0),
    [PE_F_BIT_TICKS] = FD(R_ASSIST, PE_ASSIST_BIT_TICKS_LSB, PE_ASSIST_BIT_TICKS_WIDTH, ENC_M1, 0),
    [PE_F_CRC_WIDTH] = FD(R_ASSIST, PE_ASSIST_CRC_WIDTH_LSB, PE_ASSIST_CRC_WIDTH_WIDTH, ENC_M1, 0),
    [PE_F_CRC_INIT_ONES] = FD(R_ASSIST, PE_ASSIST_CRC_INIT_ONES_LSB, PE_ASSIST_CRC_INIT_ONES_WIDTH, ENC_ID, 0),
    [PE_F_STAMP_EN] = FD(R_ASSIST, PE_ASSIST_STAMP_EN_LSB, PE_ASSIST_STAMP_EN_WIDTH, ENC_ID, 0),
    [PE_F_STAMP_COUNT] = FD(R_ASSIST, PE_ASSIST_STAMP_COUNT_LSB, PE_ASSIST_STAMP_COUNT_WIDTH, ENC_M1, 0),
    [PE_F_CRC_REFLECT] = FD(R_ASSIST, PE_ASSIST_CRC_REFLECT_LSB, PE_ASSIST_CRC_REFLECT_WIDTH, ENC_ID, 0),
    [PE_F_CRC_POLY] = FD(R_CRC_POLY, PE_CRC_POLY_LSB, PE_CRC_POLY_WIDTH, ENC_ID, 0),
    [PE_F_TIMEOUT] = FD(R_TIMING, PE_TIMING_TIMEOUT_LSB, PE_TIMING_TIMEOUT_WIDTH, ENC_ID, 0),
    [PE_F_SYNC_PHASE] = FD(R_TIMING, PE_TIMING_SYNC_PHASE_LSB, PE_TIMING_SYNC_PHASE_WIDTH, ENC_ID, 0),
};


static uint32_t fd_min(field_t f) { return FD_ENC(f) == ENC_ID ? 0u : 1u; }
static uint32_t fd_max(field_t f)
{
    if (FD_MAXOV(f))
        return FD_MAXOV(f);
    return FD_ENC(f) == ENC_ID ? FW_FIELD_MASK(FD_WIDTH(f)) : 1u << FD_WIDTH(f);
}

static uint32_t *reg_ptr(pe_cfg_t *c, uint32_t r)
{
    uint32_t *regs[R_NUM] = {&c->clkdiv, &c->execctrl, &c->shiftctrl, &c->pinctrl,
                             &c->assist, &c->crc_poly, &c->timing};
    return regs[r];
}

static uint32_t reg_val(const pe_cfg_t *c, uint32_t r)
{
    return *reg_ptr((pe_cfg_t *)c, r);
}

static uint32_t decode(field_t f, uint32_t raw)
{
    if (FD_ENC(f) == ENC_M1)
        return raw + 1u;
    if (FD_ENC(f) == ENC_Z32)
        return raw ? raw : 32u;
    return raw;
}

int pe_cfg_set(pe_cfg_t *cfg, pe_field_t field, uint32_t value)
{
    if (!cfg || (uint32_t)field >= PE_F_COUNT)
        return PE_EINVAL;
    field_t f = fields[field];
    if (value < fd_min(f) || value > fd_max(f))
        return PE_EINVAL;
    uint32_t raw = value;
    if (FD_ENC(f) == ENC_M1)
        raw = value - 1u;
    else if (FD_ENC(f) == ENC_Z32)
        raw = value & 31u;
    uint32_t mask = FW_FIELD_MASK(FD_WIDTH(f)) << FD_LSB(f);
    uint32_t *r = reg_ptr(cfg, FD_REG(f));
    *r = (*r & ~mask) | ((raw << FD_LSB(f)) & mask);
    return PE_OK;
}

uint32_t pe_cfg_get(const pe_cfg_t *cfg, pe_field_t field)
{
    if (!cfg || (uint32_t)field >= PE_F_COUNT)
        return 0;
    field_t f = fields[field];
    return decode(f, FW_FIELD_GET(reg_val(cfg, FD_REG(f)), FD_LSB(f), FD_WIDTH(f)));
}

void pe_cfg_init(pe_cfg_t *cfg)
{
    *cfg = (pe_cfg_t){0};
    (void)pe_cfg_set(cfg, PE_F_CLKDIV_INT, 1);
    (void)pe_cfg_set(cfg, PE_F_WRAP_TOP, 31);
    (void)pe_cfg_set(cfg, PE_F_IN_SHIFT_RIGHT, 1);
    (void)pe_cfg_set(cfg, PE_F_OUT_SHIFT_RIGHT, 1);
    (void)pe_cfg_set(cfg, PE_F_PUSH_THRESH, 32);
    (void)pe_cfg_set(cfg, PE_F_PULL_THRESH, 32);
    (void)pe_cfg_set(cfg, PE_F_STUFF_N, 6);
    (void)pe_cfg_set(cfg, PE_F_BIT_TICKS, 1);
    (void)pe_cfg_set(cfg, PE_F_CRC_WIDTH, 16);
    (void)pe_cfg_set(cfg, PE_F_STAMP_COUNT, 1);
}

void pe_cfg_from_regs(pe_cfg_t *cfg, uint32_t clkdiv, uint32_t execctrl, uint32_t shiftctrl,
                      uint32_t pinctrl, uint32_t assist, uint32_t crc_poly, uint32_t timing)
{
    *cfg = (pe_cfg_t){clkdiv, execctrl, shiftctrl, pinctrl, assist, crc_poly, timing};
}

int pe_cfg_check(const pe_cfg_t *cfg)
{
    if (!cfg)
        return PE_EINVAL;
    uint32_t defined[R_NUM] = {0};
    for (uint32_t i = 0; i < PE_F_COUNT; i++) {
        field_t f = fields[i];
        defined[FD_REG(f)] |= FW_FIELD_MASK(FD_WIDTH(f)) << FD_LSB(f);
        uint32_t v = pe_cfg_get(cfg, (pe_field_t)i);
        if (v < fd_min(f) || v > fd_max(f))
            return PE_EINVAL;
    }
    for (uint32_t r = 0; r < R_NUM; r++)
        if (reg_val(cfg, r) & ~defined[r])
            return PE_EINVAL;               /* reserved bits set */
    uint32_t div = pe_cfg_get(cfg, PE_F_CLKDIV_INT);
    if (div != 0 && pe_cfg_get(cfg, PE_F_SYNC_PHASE) >= div)
        return PE_EINVAL;                   /* pe_isa.md 5.4: SYNC_PHASE < INT */
    if (pe_cfg_get(cfg, PE_F_SIDE_OPT) && pe_cfg_get(cfg, PE_F_SIDE_COUNT) == 0)
        return PE_EINVAL;                   /* the enable bit needs a side-set bit */
    return PE_OK;
}

int pe_cfg_set_tick_hz(pe_cfg_t *cfg, uint32_t sys_hz, uint32_t tick_hz)
{
    if (!cfg || tick_hz == 0 || sys_hz < tick_hz || tick_hz > 0x7FFFFFFFu)
        return PE_EINVAL;
    /* q + f/256 = sys_hz / tick_hz, rounded to nearest; long division in
     * 32 bits (r < tick_hz < 2^31, so 2r never overflows). */
    uint32_t q = sys_hz / tick_hz, r = sys_hz % tick_hz, frac = 0;
    for (int i = 0; i < 8; i++) {
        r <<= 1;
        frac <<= 1;
        if (r >= tick_hz) {
            r -= tick_hz;
            frac |= 1u;
        }
    }
    if (2u * r >= tick_hz && ++frac == 256u) {
        frac = 0;
        q++;
    }
    if (q < 1u || q > 65535u)
        return PE_EINVAL;
    (void)pe_cfg_set(cfg, PE_F_CLKDIV_INT, q);
    (void)pe_cfg_set(cfg, PE_F_CLKDIV_FRAC, frac);
    return PE_OK;
}

/* ------------------------------------------------------------- state */
/* Owner shadow holds owner + 1; 0 = never set by the HAL (also the value
 * after .bss is zeroed, so a panic before pe_hal_init() takes no pads). */
#define OWNER_UNKNOWN 0u

static struct {
    uint8_t enabled;                          /* engine bitmask */
    uint8_t origin[PE_NUM_ENGINES];
    uint8_t irq_en;
    uint8_t pad_od;                           /* pads 8..15 */
    uint8_t gpio_oe;                          /* pads 8..15 */
    uint16_t gpio_out;                        /* pads 8..23 */
    uint8_t owner[PE_PAD_LAST_DRIVEABLE - PE_PAD_FIRST_DRIVEABLE + 1];  /* owner + 1 */
} st;

static uint32_t ebase(uint32_t pe) { return PE_ENGINE_BASE(pe); }
static uint32_t sh(uint32_t off) { return PE_SHARED_BASE + off; }

void pe_hal_init(void)
{
    for (uint32_t pe = 0; pe < PE_NUM_ENGINES; pe++)
        mmio_write32(ebase(pe) + PE_CTRL_OFF, 0);
    mmio_write32(sh(PE_SH_IRQ_EN_OFF), 0);
    mmio_write32(sh(PE_SH_EVT_OFF), 0);
    mmio_write32(sh(PE_SH_GPIO_OE_OFF), 0);
    mmio_write32(sh(PE_SH_PAD_OD_OFF), 0);
    mmio_write32(sh(PE_SH_GPIO_OUT_OFF), 0);
    st.enabled = 0;
    st.irq_en = 0;
    st.pad_od = 0;
    st.gpio_oe = 0;
    st.gpio_out = 0;
    for (uint32_t i = 0; i < PE_NUM_ENGINES; i++)
        st.origin[i] = 0;
    for (uint32_t i = 0; i < sizeof st.owner; i++)
        st.owner[i] = OWNER_UNKNOWN;
}

/* ------------------------------------------------------------- engines */
static int window_overlaps(uint32_t origin_e, uint32_t lo, uint32_t len)
{
    /* engine window: origin_e .. origin_e+31 (mod 64); write: lo .. lo+len-1 */
    for (uint32_t a = lo; a < lo + len; a++)
        if (((a - origin_e) & (PE_IMEM_WORDS - 1u)) < PE_PC_WORDS)
            return 1;
    return 0;
}

int pe_load_program(uint32_t origin, const uint16_t *code, uint32_t len)
{
    if (!code || len == 0 || origin >= PE_IMEM_WORDS || len > PE_IMEM_WORDS - origin)
        return PE_EINVAL;
    for (uint32_t pe = 0; pe < PE_NUM_ENGINES; pe++)
        if ((st.enabled >> pe & 1u) && window_overlaps(st.origin[pe], origin, len))
            return PE_EBUSY;
    for (uint32_t i = 0; i < len; i++)
        mmio_write32(sh(PE_SH_IMEM_OFF) + 4u * (origin + i), code[i]);
    return PE_OK;
}

int pe_configure(uint32_t pe, const pe_cfg_t *cfg)
{
    if (pe >= PE_NUM_ENGINES || pe_cfg_check(cfg) != PE_OK)
        return PE_EINVAL;
    if (st.enabled >> pe & 1u)
        return PE_EBUSY;
    uint32_t b = ebase(pe);
    mmio_write32(b + PE_CLKDIV_OFF, cfg->clkdiv);
    mmio_write32(b + PE_EXECCTRL_OFF, cfg->execctrl);
    mmio_write32(b + PE_SHIFTCTRL_OFF, cfg->shiftctrl);
    mmio_write32(b + PE_PINCTRL_OFF, cfg->pinctrl);
    mmio_write32(b + PE_ASSIST_OFF, cfg->assist);
    mmio_write32(b + PE_CRC_POLY_OFF, cfg->crc_poly);
    mmio_write32(b + PE_TIMING_OFF, cfg->timing);
    st.origin[pe] = (uint8_t)pe_cfg_get(cfg, PE_F_ORIGIN);
    return PE_OK;
}

int pe_start(uint32_t pe)
{
    if (pe >= PE_NUM_ENGINES)
        return PE_EINVAL;
    uint32_t b = ebase(pe) + PE_CTRL_OFF;
    /* Restart while still disabled, then enable: two writes, so the result
     * does not depend on how RTL orders EN against RESTART in one write. */
    mmio_write32(b, (1u << PE_CTRL_RESTART_LSB) | (1u << PE_CTRL_DIV_RESTART_LSB));
    mmio_write32(b, 1u << PE_CTRL_EN_LSB);
    st.enabled = (uint8_t)(st.enabled | (1u << pe));
    return PE_OK;
}

int pe_stop(uint32_t pe)
{
    if (pe >= PE_NUM_ENGINES)
        return PE_EINVAL;
    mmio_write32(ebase(pe) + PE_CTRL_OFF, 0);
    st.enabled = (uint8_t)(st.enabled & ~(1u << pe));
    return PE_OK;
}

uint32_t pe_enabled_mask(void) { return st.enabled; }

uint32_t pe_status(uint32_t pe)
{
    return pe < PE_NUM_ENGINES ? mmio_read32(ebase(pe) + PE_STATUS_OFF) : 0u;
}

int pe_exec(uint32_t pe, uint16_t instr, uint32_t timeout_cycles)
{
    if (pe >= PE_NUM_ENGINES)
        return PE_EINVAL;
    mmio_write32(ebase(pe) + PE_INSTR_OFF, instr);
    uint32_t t0 = fw_cycles32();
    while (pe_status(pe) & (1u << PE_STATUS_STALLED_LSB))
        if (timeout_cycles != PE_WAIT_FOREVER && fw_elapsed(t0, timeout_cycles))
            return PE_ETIMEDOUT;
    return PE_OK;
}

/* ------------------------------------------------------------- FIFOs */
int pe_tx_try(uint32_t pe, uint32_t word)
{
    if (pe >= PE_NUM_ENGINES)
        return PE_EINVAL;
    if (PE_STATUS_TXLEVEL(pe_status(pe)) >= PE_FIFO_DEPTH)
        return PE_EAGAIN;   /* never write a full FIFO: the spec does not say what happens */
    mmio_write32(ebase(pe) + PE_TXF_OFF, word);
    return PE_OK;
}

int pe_rx_try(uint32_t pe, uint32_t *word)
{
    if (pe >= PE_NUM_ENGINES || !word)
        return PE_EINVAL;
    if (PE_STATUS_RXLEVEL(pe_status(pe)) == 0)
        return PE_EAGAIN;   /* never read an empty FIFO: unspecified result */
    *word = mmio_read32(ebase(pe) + PE_RXF_OFF);
    return PE_OK;
}

int pe_tx_put(uint32_t pe, uint32_t word, uint32_t timeout_cycles)
{
    uint32_t t0 = fw_cycles32();
    for (;;) {
        int r = pe_tx_try(pe, word);
        if (r != PE_EAGAIN)
            return r;
        if (timeout_cycles != PE_WAIT_FOREVER && fw_elapsed(t0, timeout_cycles))
            return PE_ETIMEDOUT;
    }
}

int pe_rx_get(uint32_t pe, uint32_t *word, uint32_t timeout_cycles)
{
    uint32_t t0 = fw_cycles32();
    for (;;) {
        int r = pe_rx_try(pe, word);
        if (r != PE_EAGAIN)
            return r;
        if (timeout_cycles != PE_WAIT_FOREVER && fw_elapsed(t0, timeout_cycles))
            return PE_ETIMEDOUT;
    }
}

int pe_clear_errors(uint32_t pe)
{
    if (pe >= PE_NUM_ENGINES)
        return PE_EINVAL;
    mmio_write32(ebase(pe) + PE_STATUS_OFF, (1u << PE_STATUS_SERR_LSB) | (1u << PE_STATUS_OVF_LSB));
    return PE_OK;
}

/* ------------------------------------------------------------- events */
uint32_t pe_evt_read(void) { return mmio_read32(sh(PE_SH_EVT_OFF)) & 0xFFu; }
void pe_evt_set(uint32_t mask) { mmio_write32(sh(PE_SH_EVT_SET_OFF), mask & 0xFFu); }
void pe_evt_clear(uint32_t mask) { mmio_write32(sh(PE_SH_EVT_CLR_OFF), mask & 0xFFu); }

int pe_evt_irq_enable(uint32_t mask)
{
    if (mask & ~0xFFu)
        return PE_EINVAL;
    st.irq_en = (uint8_t)(st.irq_en | mask);
    mmio_write32(sh(PE_SH_IRQ_EN_OFF), st.irq_en);
    for (uint32_t n = 0; n < 8; n++)
        if (mask >> n & 1u)
            (void)irq_enable(FW_IRQ_ID_PE_EVT(n));
    return PE_OK;
}

int pe_evt_irq_disable(uint32_t mask)
{
    if (mask & ~0xFFu)
        return PE_EINVAL;
    for (uint32_t n = 0; n < 8; n++)
        if (mask >> n & 1u)
            (void)irq_disable(FW_IRQ_ID_PE_EVT(n));
    st.irq_en = (uint8_t)(st.irq_en & ~mask);
    mmio_write32(sh(PE_SH_IRQ_EN_OFF), st.irq_en);
    return PE_OK;
}

/* ------------------------------------------------------------- pads */
static int driveable(uint32_t pad) { return pad >= PE_PAD_FIRST_DRIVEABLE && pad <= PE_PAD_LAST_DRIVEABLE; }
static int bidir(uint32_t pad) { return pad >= PE_PAD_FIRST_DRIVEABLE && pad <= PE_PAD_LAST_BIDIR; }

int pe_pad_set_owner(uint32_t pad, uint32_t owner)
{
    if (!driveable(pad) || !(owner < PE_NUM_ENGINES || owner == PE_OWNER_CPU || owner == PE_OWNER_NONE))
        return PE_EINVAL;
    mmio_write32(sh(PE_SH_PAD_OWNER_OFF(pad)), owner);
    st.owner[pad - PE_PAD_FIRST_DRIVEABLE] = (uint8_t)(owner + 1u);
    return PE_OK;
}

int pe_pad_set_od(uint32_t pad, uint32_t on)
{
    if (!bidir(pad) || on > 1u)
        return PE_EINVAL;
    uint32_t bit = 1u << (pad - PE_PAD_FIRST_DRIVEABLE);
    st.pad_od = (uint8_t)(on ? (st.pad_od | bit) : (st.pad_od & ~bit));
    mmio_write32(sh(PE_SH_PAD_OD_OFF), st.pad_od);
    return PE_OK;
}

int pe_gpio_write(uint32_t pad, uint32_t level)
{
    if (!driveable(pad) || level > 1u)
        return PE_EINVAL;
    uint32_t bit = 1u << (pad - PE_PAD_FIRST_DRIVEABLE);
    st.gpio_out = (uint16_t)(level ? (st.gpio_out | bit) : (st.gpio_out & ~bit));
    mmio_write32(sh(PE_SH_GPIO_OUT_OFF), st.gpio_out);
    return PE_OK;
}

int pe_gpio_set_oe(uint32_t pad, uint32_t on)
{
    if (!bidir(pad) || on > 1u)
        return PE_EINVAL;
    uint32_t bit = 1u << (pad - PE_PAD_FIRST_DRIVEABLE);
    st.gpio_oe = (uint8_t)(on ? (st.gpio_oe | bit) : (st.gpio_oe & ~bit));
    mmio_write32(sh(PE_SH_GPIO_OE_OFF), st.gpio_oe);
    return PE_OK;
}

/* ------------------------------------------------------------- safety */
/* Order matters (pe_isa.md 2.2-2.3): stop the engines so nothing changes;
 * take each pad the firmware assigned to an engine or the CPU; turn its
 * output enable off while still in open-drain mode (so an OD pad never
 * drives high); then leave open-drain mode (pad now floats); finally set
 * uo_out levels. Plain writes from constants plus the owner shadow; the
 * shadow is validated because RAM may be corrupt when this runs. */
void pe_safe_state(void)
{
    for (uint32_t pe = 0; pe < PE_NUM_ENGINES; pe++)
        mmio_write32(ebase(pe) + PE_CTRL_OFF, 0);
    st.enabled = 0;
    mmio_write32(sh(PE_SH_IRQ_EN_OFF), 0);
    st.irq_en = 0;
    for (uint32_t pad = PE_PAD_FIRST_DRIVEABLE; pad <= PE_PAD_LAST_DRIVEABLE; pad++) {
        uint32_t o = st.owner[pad - PE_PAD_FIRST_DRIVEABLE];
        if (o >= 1u && o <= PE_OWNER_CPU + 1u) {  /* an engine or the CPU */
            mmio_write32(sh(PE_SH_PAD_OWNER_OFF(pad)), PE_OWNER_CPU);
            st.owner[pad - PE_PAD_FIRST_DRIVEABLE] = (uint8_t)(PE_OWNER_CPU + 1u);
        }
    }
    mmio_write32(sh(PE_SH_GPIO_OE_OFF), 0);
    st.gpio_oe = 0;
    mmio_write32(sh(PE_SH_PAD_OD_OFF), 0);
    st.pad_od = 0;
    st.gpio_out = FW_SAFE_UO_LEVEL ? 0xFF00u : 0u;
    mmio_write32(sh(PE_SH_GPIO_OUT_OFF), st.gpio_out);
}

/* Strong definition of the runtime hook (weak no-op in runtime.c). */
void fw_safe_state(void)
{
    pe_safe_state();
    fw_platform_safe_state();
}
