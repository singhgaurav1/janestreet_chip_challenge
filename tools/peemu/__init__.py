"""peemu — Protocol Engine assembler, golden model and helpers.

    from peemu import assemble_one, PEBlock, load_engine
"""
from .asm import AsmError, Program, assemble, assemble_one, to_c_header, to_hex, to_json
from .config import EngineConfig, clkdiv_for
from .model import OWNER_CPU, OWNER_NONE, PEBlock

__all__ = ["AsmError", "Program", "assemble", "assemble_one", "to_c_header", "to_hex", "to_json",
           "EngineConfig", "clkdiv_for", "PEBlock", "OWNER_CPU", "OWNER_NONE", "load_engine"]


def load_engine(blk: PEBlock, engine: int, prog: Program, origin: int = 0,
                own: tuple = (), od: tuple = (), enable: bool = True, **cfg) -> None:
    """What the firmware HAL does: write code at ORIGIN, configure, claim pads, enable."""
    blk.load(prog.code, origin)
    e = blk.engines[engine]
    e.configure(prog.engine_config(origin=origin, **cfg))
    e.restart()
    e.div_restart()
    if own:
        blk.set_owner(own, engine)
    if od:
        blk.set_od(od)
    e.enabled = enable
