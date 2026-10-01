"""One name per flash vendor.

Each upstream spells vendors its own way: OpenOCD abbreviates (``win``,
``mac``, ``gd``), Linux and U-Boot use lower case (and U-Boot's come from its
``CONFIG_SPI_FLASH_<VENDOR>`` blocks, some of which are families), flashrom
writes them out in full. :func:`canonical` maps every spelling found in the
data to one display name.
"""

from __future__ import annotations

_ALIASES = {
    "ace": "ACE",
    "adesto": "Adesto",
    "adesto tech": "Adesto",
    "alliance memory": "Alliance Memory",
    "alliancememory": "Alliance Memory",
    "altera": "Altera",
    "amic": "AMIC",
    "atmel": "Atmel",
    "ato": "ATO",
    "ato solution": "ATO",
    "bergmicro": "BergMicro",
    "biwin": "BIWIN",
    "boya": "Boya",
    "boya microelectronics": "Boya",
    "boya/bohong microelectronics": "Boya",
    "boyamicro": "Boya",
    "brightmoonsl": "Bright Moon",  # Bright Moon Semiconductor (Dediprog's spelling)
    "bsemi": "BSEMI",
    "byte semiconductor": "Boya",  # Boya's BY25Q parts, with its 0x68 id
    "cfeon/eon": "Eon",
    "chuangfeixin": "Chuangfeixin",
    "cxf": "CXF",
    "cyp": "Cypress",
    "cypress": "Cypress",
    "dosilicon": "Dosilicon",
    "douqi": "Douqi",
    "eon": "Eon",
    "esi": "ESI",
    "esmt": "ESMT",
    "etron": "Etron",
    "everspin": "Everspin",
    "excelsemi": "ESI",  # Excel Semiconductor Inc.
    "fidelix": "Fidelix",
    "foresee": "FORESEE",
    "fremont": "Fremont",
    "fu": "Fujitsu",
    "fujitsu": "Fujitsu",
    "fudan": "Fudan",
    "fudan micro": "Fudan",
    "fudan microelectronics": "Fudan",
    "gd": "GigaDevice",
    "generalplus": "Generalplus",
    "genitop": "Genitop",
    "giantec": "Giantec",
    "giantec semiconductor": "Giantec",
    "gigadevice": "GigaDevice",
    "hed": "HED",
    "hefei": "Hefei Core Storage",  # Dediprog's name for it; its site is hfcorestorage.com
    "heyangtek": "HeYangTek",
    "huahong": "Huahong",
    "infineon": "Infineon",
    "intel": "Intel",
    "intel/numonyx": "Intel",  # QEMU's heading for the S33 parts
    "issi": "ISSI",
    "kh": "Macronix",  # Dediprog's heading for Macronix's KH25 parts
    "lrc": "LRC",
    "mac": "Macronix",
    "macronix": "Macronix",
    "macronix_mxic": "Macronix",
    "memoritek": "Memoritek",
    "microchip": "Microchip",
    "micron": "Micron",
    "micron(numonyx)": "Micron",
    "micron/numonyx/st": "Micron",
    "mt35xu": "Micron",  # U-Boot's CONFIG_SPI_FLASH_MT35XU block
    "mxic": "Macronix",
    "mxicy": "Macronix",  # Macronix's devicetree vendor prefix (Zephyr)
    "nantronics": "Nantronics",
    "nantronix": "Nantronics",
    "neumem": "NeuMem",
    "nor-mem": "Nor-Mem",
    "on semiconductor": "ON Semiconductor",
    "onsemi": "ON Semiconductor",
    "paragon": "Paragon",
    "pct": "PCT",
    "pflash": "PMC",  # PMC's name for its Pm25 serial flash
    "pmc": "PMC",
    "puya": "Puya",
    "renesas": "Renesas",
    "s28hx_t": "Infineon",  # U-Boot's CONFIG_SPI_FLASH_S28HX_T block
    "sanyo": "Sanyo",
    "silicon kaiser": "Silicon Kaiser",
    "siliconkaiser": "Silicon Kaiser",
    "skyhigh": "SkyHigh",
    "sp": "Spansion",
    "spansion": "Spansion",
    "sst": "SST",
    "st": "ST",
    "st microelectronics": "ST",
    "stmicro": "ST",
    "terra semiconductor": "Terra Semiconductor",
    "toshiba": "Toshiba",
    "tsingteng": "Tsingteng",
    "ucundata": "UCUNDATA",
    "unionchip": "UnionChip",
    "westberry": "Westberry",
    "win": "Winbond",
    "winbond": "Winbond",
    "xioxia": "Kioxia",  # Dediprog's misspelling; its site is kioxia.com
    "xitc": "XITC",
    "xmc": "XMC",
    "xtx": "XTX",
    "xtx technology": "XTX",
    "xtx technology limited": "XTX",
    "yuchuang": "Yuchuang",
    "yxsc": "YXSC",
    "zbit": "Zbit",
    "zbit semiconductor": "Zbit",
    "zbit semiconductor, inc.": "Zbit",
    "zetta": "Zetta",
    "zetta device": "Zetta",
}


#: Each flash maker's successor: the company its flash parts went to, by
#: acquisition or a change of name, so that one part's sources may name
#: either. Atmel's serial flash went to Adesto (2012), Adesto to Dialog
#: (2020) and Dialog to Renesas (2021); ST's and Intel's NOR flash to
#: Numonyx (2008), and Numonyx to Micron (2010); Spansion merged into
#: Cypress (2015), and Cypress into Infineon (2020); SST went to Microchip
#: (2010), Sanyo's semiconductors to ON Semiconductor (2011), Toshiba's
#: memory became Kioxia (2019), and PMC (Chingis) became part of ISSI.
SUCCESSORS = {
    "Atmel": "Adesto",
    "Adesto": "Dialog",
    "Dialog": "Renesas",
    "ST": "Numonyx",
    "Intel": "Numonyx",
    "Numonyx": "Micron",
    "Spansion": "Cypress",
    "Cypress": "Infineon",
    "SST": "Microchip",
    "Sanyo": "ON Semiconductor",
    "Toshiba": "Kioxia",
    "PMC": "ISSI",
}


def company(vendor: str | None) -> str | None:
    """The company a maker's flash parts are with now: ``vendor`` (a
    :func:`canonical` name) followed through :data:`SUCCESSORS`
    (``"Atmel"`` is ``"Renesas"``); another name comes back unchanged."""
    seen = set()
    while vendor in SUCCESSORS and vendor not in seen:
        seen.add(vendor)
        vendor = SUCCESSORS[vendor]
    return vendor


def canonical(vendor: str | None) -> str | None:
    """The display name for an upstream's vendor spelling; unknown spellings
    come back unchanged."""
    if vendor is None:
        return None
    return _ALIASES.get(vendor.strip().lower(), vendor.strip())


def known() -> dict[str, str]:
    """Every spelling :func:`canonical` knows, and what it maps to."""
    return dict(_ALIASES)
