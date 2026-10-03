# Vasco (Airios Modbus TCP) for Home Assistant

Native Home Assistant integration for a **Vasco X350(E)** recuperator with the Airios
**VMD-17RPS01** controller, reached over RF through an Airios **BRDG-02EM23** Ethernet
bridge (Modbus TCP). Local polling only, no cloud.

The register map was reverse engineered on real hardware; see
[scabrero/pyairios#13](https://github.com/scabrero/pyairios/issues/13) and
[#18](https://github.com/scabrero/pyairios/issues/18). It is **not complete**. Most notably,
the filter reset is still unknown. The integration therefore ships tools for further
reverse engineering (see below).

## Installation (HACS)

1. HACS → Custom repositories → `https://github.com/PiotrOrman/Vasco`, type *Integration*.
2. Install **Vasco (Airios Modbus TCP)** and restart Home Assistant.
3. Settings → Devices & services → Add integration → *Vasco*. Enter the bridge IP, port `502` and
   unit id `2`.

Requirements:

- The bridge must have **no cloud URL** set (registers 41034–41090 on unit 1). Otherwise it only
  listens on 502 for 10 minutes after power-up.
- The bridge accepts **one TCP client at a time**. While Home Assistant is connected, the Vasco app
  and your own scripts cannot connect. Press **Release connection for 5 min** to free the slot,
  and **Reconnect now** to take it back.

## Entities

| Entity | Register | Notes |
|---|---|---|
| Fan (device) | read 41003, write 41500 | presets low / medium / high, off, 3-step percentage |
| Outdoor / supply / extract / exhaust temperature | 41013 / 41017 / 41011 / 41015 | float, low word first |
| Supply / extract fan % | 41006 / 41005 | |
| Bypass | 41022 | 0 = closed, 100 = open |
| Heat recovery efficiency | computed | (supply − outdoor) / (extract − outdoor); only with the bypass closed and ΔT ≥ 3 K |
| Speed code (raw) | 41003 | diagnostic; shows the unexplained code 5 |
| RF signal | 40109 | diagnostic, every ~10 min |
| Bridge connection | – | diagnostic, with the last error |
| Low / medium supply & extract | 42003–42006 | config, % of high |
| High airflow A / B | 42000 / 42007 | config, m³/h; which one is supply is not established |
| Bypass threshold | 42008 | config, float, room temperature |
| Pre-heater activation | 42010 | config, float, outdoor temperature |
| Minimum supply temperature | 42012 | config, float |
| Register 41001, 41004, 41007 (float), 41009 (float), 41019–41021, 41023, 42014–42019 | | diagnostic; readable but not understood yet |

**Writing configuration is disabled by default.** Enable *Allow writing configuration* in the
integration options. Fan speed can always be set.

## Register map (VMD-17RPS01, unit id 2)

Addresses are literal (no −1 offset). Writes must use **FC16**; the bridge rejects FC06.
Floats are IEEE 754 with the **low word first**.

| Address | R/W | Meaning |
|---|---|---|
| 40000–40001 | R | RF address, U32 low word first |
| 40002–40003 | R | product id `0x0001C83A`, U32 low word first |
| 40004 | R | software version |
| 40005 | R | OEM number (`0x66` = Vasco) |
| 40011–40016 | R | product name `VMD-17RPS01` |
| 40109 | R | last RSSI, I16 dBm |
| 41000, 41002 | ? | always exception 5: probably write-only |
| 41003 | R | current speed: 0 off, 1 low, 2 mid, 3 high, 5 = sustained ~70 % (not boost) |
| 41005 / 41006 | R | extract / supply fan % |
| 41007, 41009 | R | float, meaning unknown |
| 41011 / 41013 / 41015 / 41017 | R | extract / outdoor / exhaust / supply temperature, float |
| 41022 | R | bypass position 0 / 100 |
| 41500 | W | requested speed: 0 off, 1 away (= low), 2 low, 3 mid, 4 high (7 → reads back as 5) |
| 42000, 42007 | RW | high airflow pair, m³/h |
| 42003–42006 | RW | low supply/extract, mid supply/extract, % of high |
| 42008 | RW | bypass threshold, float |
| 42010 | RW | pre-heater activation, float |
| 42012 | RW | minimum supply temperature, float |
| 42014–42019 | R | unknown (182, 425, 20, 20, 75, 75 on the reference unit) |

The bypass opens when the room temperature is above the threshold, the outdoor temperature is
above ~15 °C, and the outdoor temperature is at least 3 K below the threshold.

Absent (exception 2): everything ≥ 41024 in the running block, 41501–41503, 41550, 42001–42002,
and everything > 42019.

Bridge behaviour handled by the client:

- It waits 0.5 s after connecting, because the bridge drops a request sent right after the
  accept.
- Exception 5 (ACKNOWLEDGE) means "value not cached yet, fetching over RF", so the client
  retries after 5 s.
- On a dropped connection it reconnects with back-off (5 s → 5 min).

## Reverse-engineering tools

Three services use the integration's own connection, so you don't have to release the slot.

- **`vasco.read_registers`** reads `count` registers from `address` and returns them.
- **`vasco.scan_registers`** reads `start..end` one by one (max 200), skipping absent registers.
  It remembers the last scan of the same range and returns **what changed** since then.
- **`vasco.write_registers`** does a raw FC16 write. It only works when writes are enabled in the
  options.

Example of hunting for the filter reset (Developer tools → Actions):

```yaml
action: vasco.scan_registers
data: {start: 41000, end: 41199}
```

1. Run the scan once (and also for `40000–40129`, `41490–41559` and `41990–42079`).
2. Reset the filter on the panel or in the Vasco app (release the connection first if you use the
   app).
3. Run the same scans again. `changed` lists the registers that moved.

The diagnostic *Register …* sensors also keep history, so a change shows up in the logbook and
graphs.

## License

MIT
