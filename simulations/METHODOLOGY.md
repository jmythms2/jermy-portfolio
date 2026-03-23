# Simulation Methodology — Smart Thermostat Energy Study

## Overview

10 annual EnergyPlus simulations: 5 climate zones × 2 thermostat strategies.
Results power the interactive dashboard at jermythomas.com.

---

## EnergyPlus Version

**EnergyPlus 25.2.0** (build cf7368216c, Linux Ubuntu 24.04)

---

## Base Building Model (IDF)

**Source file:** `AirflowNetwork_Simple_House.idf`
(from EnergyPlus 25.2.0 official example files)

This is a single-family residential building with:
- Airflow network (natural infiltration + mechanical ventilation)
- Gas furnace heating + electric DX cooling (split system)
- Residential occupancy, lighting, and plug load schedules
- Detailed envelope construction (walls, roof, windows)

The base IDF was not modified structurally. Two things were changed programmatically for each run:
1. Thermostat setpoint schedules (see below)
2. RunPeriod extended to full calendar year (Jan 1 – Dec 31)

Modified IDFs are saved in `simulations/idfs/` for reproducibility.

---

## Weather Files (EPW)

TMY (Typical Meteorological Year) weather files from the EnergyPlus weather database:

| Location Key        | City               | ASHRAE Climate Zone | File                      |
|---------------------|--------------------|---------------------|---------------------------|
| 1A_Miami            | Miami, FL          | 1A — Hot-Humid      | `1A_Miami.epw`            |
| 2B_Phoenix          | Phoenix, AZ        | 2B — Hot-Dry        | `2B_Phoenix.epw`          |
| 3C_SanFrancisco     | San Francisco, CA  | 3C — Marine         | `3C_SanFrancisco.epw`     |
| 5A_Chicago          | Chicago, IL        | 5A — Cold-Humid     | `5A_Chicago.epw`          |
| 6B_Denver           | Denver, CO         | 6B — Cold-Dry       | `6B_Denver.epw`           |

---

## Thermostat Strategies

Two `Schedule:Compact` thermostat schedules were applied to `Dual Heating Setpoints` and `Dual Cooling Setpoints` objects in the IDF.

### Flat (constant comfort setpoints)
| Period   | Heating Setpoint | Cooling Setpoint |
|----------|-----------------|-----------------|
| All day  | 22.2°C (72°F)   | 23.9°C (75°F)   |

Based on ResStock base-case setpoints.

### Stepped (night setback)
| Period             | Heating Setpoint        | Cooling Setpoint        |
|--------------------|------------------------|------------------------|
| 07:00 – 22:00      | 22.2°C (72°F) — comfort | 23.9°C (75°F) — comfort |
| 22:00 – 07:00      | 18.0°C (64.4°F) — setback | 27.0°C (80.6°F) — float up |

This matches the "combined night algorithm" from the SimBuild 2024 paper (Benne, Thomas et al., 2024).

---

## Occupancy & Load Schedules

The base IDF references a `Schedule:File` object that reads from `in.schedules.csv` (35,040 rows = 8,760 hours × 4 design days). This file provides hourly fractional schedules for:

- Occupants
- Interior lighting
- Garage lighting
- Cooking range
- Dishwasher, clothes washer, clothes dryer
- Ceiling fan
- Plug loads (other, TV)
- Hot water (dishwasher, washer, fixtures)
- Vacancy and power outage flags

Schedules are derived from ResStock residential occupancy profiles. They were not varied between thermostat strategies — only the thermostat setpoint schedules differ between flat and stepped runs.

---

## Outputs Collected

### Hourly meters (from `eplusout.csv`)
- `Electricity:Facility` — total facility electricity (J → kWh)
- `NaturalGas:Facility` — total facility gas (J → kWh equivalent)
- `Electricity:HVAC` — HVAC-only electricity (J → kWh)

### Annual tabular (from `eplustbl.htm`)
- End-use breakdown: cooling, heating (electricity + gas), fans, interior lighting, interior equipment, water systems
- Comfort: Time Not Comfortable Based on Simple ASHRAE 55-2004 (hours/year)
- Comfort: Time Setpoint Not Met During Occupied Heating/Cooling (hours/year)

---

## Post-Processing

Results extracted by `run_sims.py`:
- Annual GJ values converted to kWh (1 GJ = 277.778 kWh)
- Hourly J values converted to kWh (1 kWh = 3,600,000 J)
- Monthly aggregation by calendar month (365-day year, non-leap)
- Final output: `simulations/results/thermostat_results.json`

---

## Key Results

| Climate Zone        | Flat (kWh/yr) | Stepped (kWh/yr) | Savings |
|---------------------|--------------|-----------------|---------|
| 1A Miami            | 26,300       | 24,667          | 6.2%    |
| 2B Phoenix          | 33,236       | 30,139          | 9.3%    |
| 3C San Francisco    | 26,831       | 21,094          | 21.4%   |
| 5A Chicago          | 54,911       | 48,786          | 11.2%   |
| 6B Denver           | 45,150       | 38,847          | 14.0%   |

San Francisco's high savings (21%) reflect its mild climate where setback temperature is often met passively overnight. Cold climates (Chicago, Denver) show large absolute gas savings because heating dominates their annual load profile.

---

## Reproducibility

To re-run simulations:
1. Install EnergyPlus 25.2.0
2. Update `EP_EXE` and `BASE_IDF` paths in `run_sims.py`
3. Run: `python3 simulations/run_sims.py`

All modified IDFs are committed to `simulations/idfs/` and all EPW files to `simulations/epw/` so results can be verified without re-running.

---

*Study aligned with: Benne, K.; Thomas, J.; Ling, J.; et al. "Simulation-Driven Rating of Smart Thermostats." SimBuild 2024.*
