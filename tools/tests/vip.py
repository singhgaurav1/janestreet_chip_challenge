"""Independent protocol models for checking the engines.

Written from the protocol specifications, sharing no code with peemu.model,
so a passing test means the engine program and the model agree with an
outside reference, not with themselves. All models work on per-cycle
sampled line levels.
"""
from __future__ import annotations


# ---- UART -------------------------------------------------------------------

def uart_encode(data: bytes, cycles_per_bit: float, idle_before: int = 50, gap_bits: float = 0,
                stop_ok=lambda i: True) -> list[int]:
    """8N1 waveform, LSB first, one entry per system cycle."""
    bits = []
    for i, b in enumerate(data):
        bits += [0] + [(b >> k) & 1 for k in range(8)] + [1 if stop_ok(i) else 0]
        bits += [1] * int(gap_bits)
    wave = [1] * idle_before
    t = 0.0
    for b in bits:
        t_next = t + cycles_per_bit
        wave += [b] * (round(t_next) - round(t))
        t = t_next
    return wave + [1] * int(3 * cycles_per_bit)


def uart_decode(wave: list[int], cycles_per_bit: float) -> list[int]:
    """Classic receiver: find a falling edge, sample each bit at its centre."""
    out, i, n = [], 1, len(wave)
    while i < n:
        if wave[i - 1] == 1 and wave[i] == 0:
            centre = lambda k: int(i + (k + 0.5) * cycles_per_bit)
            if centre(9) >= n:
                break
            assert wave[centre(0)] == 0, f"start bit glitch at {i}"
            byte = sum(wave[centre(1 + k)] << k for k in range(8))
            assert wave[centre(9)] == 1, f"framing error at {i}"
            out.append(byte)
            i = centre(9)
        i += 1
    return out


# ---- SPI --------------------------------------------------------------------

class SpiTarget:
    """SPI target for any mode. Call step() once per cycle with the line levels;
    read .miso after the call."""

    def __init__(self, mode: int, tx: bytes):
        self.cpol, self.cpha = mode >> 1, mode & 1
        self.tx = list(tx)
        self.rx: list[int] = []
        self.prev_sck = self.cpol
        self.prev_cs = 1
        self.miso = 1
        self.bit = self.sh_in = self.cur = 0   # first byte is loaded when CS asserts

    def _reset(self):
        self.bit = 0
        self.sh_in = 0
        self.cur = self.tx.pop(0) if self.tx else 0

    def _present(self):
        self.miso = (self.cur >> (7 - self.bit)) & 1

    def step(self, cs: int, sck: int, mosi: int):
        if cs:
            self.miso = 1
        elif self.prev_cs:  # CS just asserted
            self._reset()
            if self.cpha == 0:
                self._present()
        if not cs and sck != self.prev_sck:
            leading = sck != self.cpol
            sample = leading if self.cpha == 0 else not leading
            if sample:
                self.sh_in = (self.sh_in << 1) | mosi
                self.bit += 1
                if self.bit == 8:
                    self.rx.append(self.sh_in & 0xFF)
                    self.sh_in, self.bit = 0, 0
                    self.cur = self.tx.pop(0) if self.tx else 0
            else:   # the other edge shifts out the next bit
                self._present()
        self.prev_sck, self.prev_cs = sck, cs


# ---- I2C --------------------------------------------------------------------

class I2CTarget:
    """Behavioural I2C target with optional clock stretching after its address ACK.

    sda_out/scl_out: 1 = released, 0 = pulling low.
    """

    def __init__(self, addr: int, read_data: bytes = b"", stretch: int = 0):
        self.addr, self.read_data, self.stretch = addr, list(read_data), stretch
        self.received: list[int] = []
        self.log: list[str] = []
        self.state = "idle"
        self.sda_out = 1
        self.hold = 0
        self.prev = (1, 1)
        self.bits = self.sh = 0

    @property
    def scl_out(self) -> int:
        return 0 if self.hold > 0 else 1

    def _rd_present(self):
        self.sda_out = (self.rbyte >> self.rbit) & 1

    def step(self, scl: int, sda: int):
        pscl, psda = self.prev
        if self.hold:
            self.hold -= 1
        if scl and pscl and sda != psda:
            if sda == 0:
                self.log.append("S")
                self.state, self.bits, self.sh, self.sda_out = "addr", 0, 0, 1
            else:
                self.log.append("P")
                self.state, self.sda_out = "idle", 1
        elif scl and not pscl:          # rising edge: sample
            if self.state in ("addr", "wdata"):
                self.sh = (self.sh << 1) | sda
                self.bits += 1
            elif self.state == "rack":
                self.ack = sda
        elif not scl and pscl:          # falling edge: drive
            st = self.state
            if st == "addr" and self.bits == 8:
                if self.sh >> 1 == self.addr:
                    self.rw = self.sh & 1
                    self.log.append(f"A{self.sh >> 1:02x}{'R' if self.rw else 'W'}")
                    self.sda_out, self.state = 0, "aack"
                else:
                    self.state = "idle"
            elif st == "aack":
                self.hold = self.stretch
                if self.rw:
                    self._load_read()
                else:
                    self.sda_out, self.state, self.bits, self.sh = 1, "wdata", 0, 0
            elif st == "wdata" and self.bits == 8:
                self.received.append(self.sh & 0xFF)
                self.sda_out, self.state = 0, "wack"
            elif st == "wack":
                self.sda_out, self.state, self.bits, self.sh = 1, "wdata", 0, 0
            elif st == "rdata":
                self.rbit -= 1
                if self.rbit >= 0:
                    self._rd_present()
                else:
                    self.sda_out, self.state = 1, "rack"
            elif st == "rack":
                if self.ack == 0:
                    self._load_read()
                else:
                    self.sda_out, self.state = 1, "idle"
        self.prev = (scl, sda)

    def _load_read(self):
        self.rbyte = self.read_data.pop(0) if self.read_data else 0xFF
        self.rbit = 7
        self.state = "rdata"
        self._rd_present()


class I2CController:
    """Scripted I2C controller: produces SDA/SCL levels per cycle and checks ACKs.

    ops: ("start",), ("byte", value) -> expects ACK, ("stop",)
    """

    def __init__(self, ops, half: int):
        self.seq: list[tuple[int, int, str | None]] = []   # (scl, sda, tag)
        h = half
        self.seq += [(1, 1, None)] * (4 * h)
        idle = True
        for op in ops:
            if op[0] == "start":
                if not idle:   # repeated START: raise SDA, then SCL, first
                    self.seq += [(0, 1, None)] * h + [(1, 1, None)] * h
                self.seq += [(1, 0, None)] * h + [(0, 0, None)] * h
                idle = False
            elif op[0] == "stop":
                self.seq += [(0, 0, None)] * h + [(1, 0, None)] * h + [(1, 1, None)] * (8 * h)
                idle = True
            else:
                v = op[1]
                for k in range(7, -1, -1):
                    b = (v >> k) & 1
                    self.seq += [(0, b, None)] * h + [(1, b, None)] * h
                self.seq += [(0, 1, None)] * h + [(1, 1, "ack")] * h
        self.seq += [(1, 1, None)] * (4 * h)
        self.i = 0
        self.acks: list[int] = []

    def levels(self) -> tuple[int, int]:
        scl, sda, _ = self.seq[min(self.i, len(self.seq) - 1)]
        return scl, sda

    def sample(self, sda_bus: int):
        """Call once per cycle with the resolved SDA; advances the script."""
        if self.i < len(self.seq):
            _, _, tag = self.seq[self.i]
            nxt = self.seq[self.i + 1][2] if self.i + 1 < len(self.seq) else None
            if tag == "ack" and nxt != "ack":   # sample at the end of the ACK high phase
                self.acks.append(sda_bus)
        self.i += 1

    @property
    def done(self) -> bool:
        return self.i >= len(self.seq)


class I2CBusMonitor:
    """Checks bus timing: minimum SCL low/high widths while a transfer runs."""

    def __init__(self):
        self.prev_scl = 1
        self.run = 0   # the idle period before the first edge is not measured
        self.lows: list[int] = []
        self.highs: list[int] = []

    def step(self, scl: int):
        if scl == self.prev_scl:
            self.run += 1
        else:
            if self.run:
                (self.lows if self.prev_scl == 0 else self.highs).append(self.run)
            self.run = 1
        self.prev_scl = scl


# ---- NRZI / bit-stuffed bitstream (USB-shaped) ------------------------------

def bitstream_decode(dp: list[int], dm: list[int], cycles_per_bit: int):
    """Decode a differential NRZI stream with ones-stuffing (N=6) up to SE0.

    Returns (data bits after the 8-bit SYNC, number of SE0 bit times, saw idle J after SE0).
    """
    start = next(i for i in range(1, len(dp)) if dp[i - 1] == 1 and dp[i] == 0)
    levels = []
    t = start + cycles_per_bit // 2
    while t < len(dp):
        levels.append((dp[t], dm[t]))
        t += cycles_per_bit
    bits, prev, ones, se0, j_after = [], 1, 0, 0, False
    for k, (p, m) in enumerate(levels):
        if p == 0 and m == 0:
            se0 += 1
            continue
        if se0:
            j_after = (p, m) == (1, 0)
            break
        assert p != m, f"bad differential state at bit {k}"
        b = 1 if p == prev else 0
        prev = p
        if ones == 6:
            assert b == 0, "missing stuff bit"
            ones = 0
            continue
        ones = ones + 1 if b else 0
        bits.append(b)
    sync, data = bits[:8], bits[8:]
    assert sync == [0, 0, 0, 0, 0, 0, 0, 1], f"bad SYNC {sync}"
    return data, se0, j_after


def bits_to_bytes_lsb(bits: list[int]) -> bytes:
    return bytes(sum(bits[i + k] << k for k in range(8)) for i in range(0, len(bits) - 7, 8))
