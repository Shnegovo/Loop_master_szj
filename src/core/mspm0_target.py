"""Local pyOCD target definitions for TI MSPM0 devices used by LoopMaster.

This fallback target is intentionally read-only from LoopMaster's point of view:
it provides a memory map so pyOCD can attach and expose the MEM-AP, but it does
not include any flash algorithm.
"""

from __future__ import annotations


def register_mspm0_targets() -> None:
    """Register MSPM0G3507 aliases with pyOCD if they are not already present."""
    from pyocd.core.memory_map import MemoryMap, RamRegion, RomRegion
    from pyocd.coresight.coresight_target import CoreSightTarget
    from pyocd.target import TARGET

    class MSPM0G3507(CoreSightTarget):
        VENDOR = "Texas Instruments"
        PART_NUMBER = "MSPM0G3507"
        PART_FAMILIES = ["MSPM0G1X0X_G3X0X", "MSPM0G350X"]

        MEMORY_MAP = MemoryMap(
            RomRegion(name="IROM1", start=0x00000000, length=0x00020000, access="rx", is_boot_memory=True),
            RomRegion(name="IROM2", start=0x00400000, length=0x00020000, access="rx", alias="IROM1"),
            RamRegion(name="IRAM1", start=0x20000000, length=0x00008000, access="rwx"),
            RamRegion(name="IRAM_Parity", start=0x20100000, length=0x00008000, access="rwx", alias="IRAM1"),
            RamRegion(name="IRAM_No_Parity", start=0x20200000, length=0x00008000, access="rwx", alias="IRAM1"),
            RamRegion(name="IRAM_Parity_Code", start=0x20300000, length=0x00008000, access="rwx", alias="IRAM1"),
            RomRegion(name="NonMain_ECC", start=0x41C00000, length=0x00000200, access="r"),
            RomRegion(name="NonMain_noECC", start=0x41C10000, length=0x00000200, access="r", alias="NonMain_ECC"),
            RomRegion(name="Factory_ECC", start=0x41C40000, length=0x00000080, access="r"),
            RomRegion(name="Factory_noECC", start=0x41C50000, length=0x00000080, access="r", alias="Factory_ECC"),
        )

        def __init__(self, session):
            super().__init__(session, self.MEMORY_MAP)

    for name in ("mspm0g3507", "mspm0g350x", "lp_mspm0g3507"):
        TARGET.setdefault(name, MSPM0G3507)
