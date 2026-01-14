# CNC_PCB — PCB Milling G-code Generator (GRBL)

CNC_PCB is a desktop CAM utility that converts PCB fabrication exports (Gerber RS-274X + Excellon drill) into **GRBL-compatible G-code** for PCB milling workflows such as:

- **Top copper isolation routing** (offset toolpaths)
- **Drilling** (with optional multi-drill planning)
- **Board outline / slots / larger milled holes**
- **Top silkscreen engraving**
- **Top pad clearing from soldermask layer** (optional / niche)

The application is general-purpose for common Gerber/Excellon sources, with practical conventions geared toward **KiCad** and **EasyEDA** exports (including EasyEDA ZIPs with `.gtl/.gbl/.gto/...` extensions that are normalized internally).

---

## Features

- **Gerber RS-274X parsing** (polarity, apertures, flashes, regions) for geometry extraction
- **Excellon (DRL) parsing** including units/format headers and slot support
- **Multi-operation output**
  - One combined file with tool-change pauses **or**
  - Separate `.nc` per operation
- **Drill planner**
  - AUTO: selects up to *N* drill sizes to cover holes (within tolerance)
  - MANUAL: explicit drill size selection
  - De-duplication of near-duplicate holes (XY tolerance)
- **Safety & machine policies**
  - Safe/Travel/Toolchange Z, park position, spindle warmup dwell
  - Optional “probe on start” custom G-code injection
- **Preview UI** to validate layer orientation, extents, and generated toolpaths before cutting
- Configuration via editable `.ini` files:
  - `bits.ini` (tool library)
  - `job_settings.ini` (job defaults)

---
