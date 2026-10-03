"""Constants and register map for the Vasco (Airios VMD-17RPS01) integration.

Kept free of Home Assistant imports so that ``client.py`` can be tested standalone.

Register map verified on a Vasco X350E (VMD-17RPS01) behind an Airios BRDG-02EM23,
see https://github.com/scabrero/pyairios/issues/13 and /issues/18.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "vasco"

CONF_UNIT_ID: Final = "unit_id"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_ALLOW_WRITES: Final = "allow_config_writes"

DEFAULT_PORT: Final = 502
DEFAULT_UNIT_ID: Final = 2
DEFAULT_SCAN_INTERVAL: Final = 30  # seconds; the RF link is slow, don't go lower
MIN_SCAN_INTERVAL: Final = 15
CONFIG_REFRESH_EVERY: Final = 20  # polls -> config block re-read every ~10 min
RELEASE_SECONDS: Final = 300  # "release connection" button

PRODUCT_ID_VMD_17RPS01: Final = 0x0001C83A

# --- Generic Airios device block (all RF nodes) -------------------------------
REG_RF_ADDRESS: Final = 40000  # U32, low word first
REG_PRODUCT_ID: Final = 40002  # U32, low word first
REG_SOFTWARE_VERSION: Final = 40004  # U16
REG_OEM_NUMBER: Final = 40005  # U16
REG_PRODUCT_NAME: Final = 40011  # ASCII, 40011..40016
REG_RF_LAST_RSSI: Final = 40109  # I16, dBm

# --- Running block (read) ------------------------------------------------------
LIVE_START: Final = 41003
LIVE_COUNT: Final = 21  # 41003 .. 41023 (41000/41002 answer exception 5)
REG_CURRENT_SPEED: Final = 41003
REG_FAN_EXHAUST: Final = 41005  # % (running block is crossed vs config block)
REG_FAN_SUPPLY: Final = 41006  # %
REG_TEMP_EXHAUST: Final = 41011  # float, word-swapped (from the house)
REG_TEMP_INLET: Final = 41013  # float, outdoor air entering the unit
REG_TEMP_OUTLET: Final = 41015  # float, air expelled outside
REG_TEMP_SUPPLY: Final = 41017  # float, air supplied to the house
REG_BYPASS_POSITION: Final = 41022  # 0 / 100

# --- Command (write only, FC16) -----------------------------------------------
REG_REQUESTED_SPEED: Final = 41500

# --- Configuration block (read/write, FC16 only) ------------------------------
REG_HIGH_A: Final = 42000  # m3/h, direction within the pair not established
REG_LOW_SUPPLY: Final = 42003  # % of high
REG_LOW_EXHAUST: Final = 42004
REG_MID_SUPPLY: Final = 42005
REG_MID_EXHAUST: Final = 42006
REG_HIGH_B: Final = 42007  # m3/h
REG_BYPASS_THRESHOLD: Final = 42008  # float, room temperature
REG_PREHEATER_ACTIVATION: Final = 42010  # float, outdoor temperature
REG_MIN_SUPPLY_TEMP: Final = 42012  # float
CONFIG_U16: Final = (
    REG_HIGH_A,
    REG_LOW_SUPPLY,
    REG_LOW_EXHAUST,
    REG_MID_SUPPLY,
    REG_MID_EXHAUST,
    REG_HIGH_B,
)
CONFIG_FLOAT: Final = (REG_BYPASS_THRESHOLD, REG_PREHEATER_ACTIVATION, REG_MIN_SUPPLY_TEMP)

# 41003 read scale -> preset name. Code 5 (after requesting boost) is a sustained
# ~70 % mode that is NOT boost; deliberately unmapped.
SPEED_CODE_TO_PRESET: Final = {0: "off", 1: "low", 2: "medium", 3: "high"}
# preset -> value written to 41500 (1 = away, which is identical to low here)
PRESET_TO_REQUEST: Final = {"off": 0, "low": 2, "medium": 3, "high": 4}
PRESETS_ON: Final = ("low", "medium", "high")
