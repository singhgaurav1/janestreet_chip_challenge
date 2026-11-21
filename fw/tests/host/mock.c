/* Host-test doubles for the HAL's hardware dependencies: MMIO (recorded),
 * the cycle counter (advances on every read) and Ibex interrupt enables. */
#include <stdio.h>
#include <stdlib.h>

#include "mock.h"

mock_t mock;

void mock_reset(void)
{
    mock = (mock_t){0};
    for (unsigned i = 0; i < 4; i++)
        mock.status[i] = 0;          /* TX empty, RX empty, not stalled */
    mock.cycle_step = 10;
}

static void log_op(char op, uint32_t addr, uint32_t value)
{
    if (mock.nlog >= MOCK_LOG_MAX) {
        fprintf(stderr, "mock: access log overflow\n");
        abort();
    }
    mock.log[mock.nlog++] = (mock_access_t){op, addr, value};
}

static int pe_engine_reg(uint32_t addr, uint32_t *pe, uint32_t *off)
{
    if (addr < FW_PE_BASE || addr >= FW_PE_BASE + 0x400u)
        return 0;
    *pe = (addr - FW_PE_BASE) / PE_ENGINE_STRIDE;
    *off = (addr - FW_PE_BASE) % PE_ENGINE_STRIDE;
    return 1;
}

/* Reads: STATUS and RXF are scripted per engine; EVT from mock.evt;
 * ENGINE_CTRL returns its EN field; every other register in the PE window
 * returns the last value written (enough for the HAL's RW readbacks). */
uint32_t mmio_read32(uint32_t addr)
{
    uint32_t pe, off, v;
    if (addr < FW_PE_BASE || addr >= FW_PE_BASE + 0x1000u) {
        fprintf(stderr, "mock: read outside the PE block 0x%08x\n", addr);
        abort();
    }
    if (pe_engine_reg(addr, &pe, &off) && off == PE_STATUS_OFF) {
        v = mock.status[pe];
        if (mock.stall_reads[pe] && --mock.stall_reads[pe] == 0)
            mock.status[pe] &= ~(1u << PE_STATUS_FORCE_PENDING_LSB);
    } else if (pe_engine_reg(addr, &pe, &off) && off == PE_RXF_OFF) {
        v = mock.rx_value[pe];
        mock.rx_pops[pe]++;
    } else if (addr == PE_SHARED_BASE + PE_SH_EVT_OFF) {
        v = mock.evt;
    } else if (addr == PE_SHARED_BASE + PE_SH_ENGINE_CTRL_OFF) {
        v = mock.regs[(addr - FW_PE_BASE) / 4] & 0xFu;
    } else {
        v = mock.regs[(addr - FW_PE_BASE) / 4];
    }
    log_op('R', addr, v);
    return v;
}

void mmio_write32(uint32_t addr, uint32_t value)
{
    if (addr < FW_PE_BASE || addr >= FW_PE_BASE + 0x1000u) {
        fprintf(stderr, "mock: write outside the PE block 0x%08x\n", addr);
        abort();
    }
    mock.regs[(addr - FW_PE_BASE) / 4] = value;
    uint32_t pe, off;
    /* per-engine CTRL.EN mirrors into ENGINE_CTRL[pe] */
    if (pe_engine_reg(addr, &pe, &off) && off == PE_CTRL_OFF) {
        uint32_t *ec = &mock.regs[(PE_SHARED_BASE + PE_SH_ENGINE_CTRL_OFF - FW_PE_BASE) / 4];
        *ec = (*ec & ~(1u << pe)) | ((value & 1u) << pe);
    }
    log_op('W', addr, value);
}

uint64_t fw_host_cycles(void)
{
    mock.cycles += mock.cycle_step;
    return mock.cycles;
}

int irq_enable(uint32_t id)
{
    mock.mie |= 1u << id;
    return 0;
}

/* Critical sections: count them so tests can check balance. */
uint32_t irq_save(void)
{
    mock.irq_depth++;
    return 1u;
}

void irq_restore(uint32_t saved)
{
    (void)saved;
    mock.irq_depth--;
}

int irq_disable(uint32_t id)
{
    mock.mie &= ~(1u << id);
    return 0;
}

int mock_count_writes(uint32_t addr)
{
    int n = 0;
    for (int i = 0; i < mock.nlog; i++)
        n += mock.log[i].op == 'W' && mock.log[i].addr == addr;
    return n;
}

int mock_last_write(uint32_t addr, uint32_t *value)
{
    for (int i = mock.nlog - 1; i >= 0; i--)
        if (mock.log[i].op == 'W' && mock.log[i].addr == addr) {
            *value = mock.log[i].value;
            return i;
        }
    return -1;
}

/* Runtime hook called by the HAL's fw_safe_state(); not under test here. */
void fw_platform_safe_state(void) {}
