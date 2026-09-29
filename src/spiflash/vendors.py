"""One name per flash vendor.

Each upstream spells vendors its own way: OpenOCD abbreviates (``win``,
``mac``, ``gd``), Linux and U-Boot use lower case (and U-Boot's come from its
``CONFIG_SPI_FLASH_<VENDOR>`` blocks, some of which are families), flashrom
writes them out in full. :func:`canonical` maps every spelling found in the
data to one display name.
"""

from __future__ import annotations

_ALIASES = {
    "adesto": "Adesto",
    "alliancememory": "Alliance Memory",
    "amic": "AMIC",
    "atmel": "Atmel",
    "ato": "ATO",
    "boya": "Boya",
    "boya microelectronics": "Boya",
    "boya/bohong microelectronics": "Boya",
    "bsemi": "BSEMI",
    "cyp": "Cypress",
    "dosilicon": "Dosilicon",
    "eon": "Eon",
    "esi": "ESI",
    "esmt": "ESMT",
    "everspin": "Everspin",
    "foresee": "FORESEE",
    "fu": "Fujitsu",
    "fujitsu": "Fujitsu",
    "fudan": "Fudan",
    "fudan micro": "Fudan",
    "gd": "GigaDevice",
    "gigadevice": "GigaDevice",
    "heyangtek": "HeYangTek",
    "infineon": "Infineon",
    "intel": "Intel",
    "intel/numonyx": "Intel",  # QEMU's heading for the S33 parts
    "issi": "ISSI",
    "mac": "Macronix",
    "macronix": "Macronix",
    "microchip": "Microchip",
    "micron": "Micron",
    "micron/numonyx/st": "Micron",
    "mt35xu": "Micron",  # U-Boot's CONFIG_SPI_FLASH_MT35XU block
    "mxicy": "Macronix",  # Macronix's devicetree vendor prefix (Zephyr)
    "nantronics": "Nantronics",
    "paragon": "Paragon",
    "pmc": "PMC",
    "puya": "Puya",
    "renesas": "Renesas",
    "s28hx_t": "Infineon",  # U-Boot's CONFIG_SPI_FLASH_S28HX_T block
    "sanyo": "Sanyo",
    "siliconkaiser": "Silicon Kaiser",
    "skyhigh": "SkyHigh",
    "sp": "Spansion",
    "spansion": "Spansion",
    "sst": "SST",
    "st": "ST",
    "st microelectronics": "ST",
    "stmicro": "ST",
    "toshiba": "Toshiba",
    "win": "Winbond",
    "winbond": "Winbond",
    "xmc": "XMC",
    "xtx": "XTX",
    "xtx technology": "XTX",
    "xtx technology limited": "XTX",
    "zbit": "Zbit",
    "zbit semiconductor, inc.": "Zbit",
    "zetta": "Zetta",
    "zetta device": "Zetta",
}


def canonical(vendor: str | None) -> str | None:
    """The display name for an upstream's vendor spelling; unknown spellings
    come back unchanged."""
    if vendor is None:
        return None
    return _ALIASES.get(vendor.strip().lower(), vendor.strip())


def known() -> dict[str, str]:
    """Every spelling :func:`canonical` knows, and what it maps to."""
    return dict(_ALIASES)
