"""
Run EnergyPlus simulations for thermostat portfolio demo.
Same IDF, same building, 5 climate zones × 2 thermostat strategies = 10 runs.

Thermostat strategies (matching SimBuild 2024 paper):
  - flat:    constant comfort setpoints all day
  - stepped: night setback (10pm–7am) — combined night algorithm

Results saved as JSON for web visualization.
"""

import json
import shutil
import subprocess
import re
from pathlib import Path

# ── CONFIG ──────────────────────────────────────────────────────────────────
EP_EXE   = Path("/tmp/EnergyPlus-25.2.0-cf7368216c-Linux-Ubuntu24.04-x86_64/energyplus")
BASE_IDF = Path("/tmp/EnergyPlus-25.2.0-cf7368216c-Linux-Ubuntu24.04-x86_64/ExampleFiles/AirflowNetwork_Simple_House.idf")
SIM_DIR  = Path(__file__).parent
EPW_DIR  = SIM_DIR / "epw"
IDF_DIR  = SIM_DIR / "idfs"
OUT_DIR  = SIM_DIR / "results"

IDF_DIR.mkdir(exist_ok=True)
OUT_DIR.mkdir(exist_ok=True)

# Heating/cooling setpoints in °C
# Flat: constant 22.2°C heat / 23.9°C cool (ResStock base)
# Stepped: setback to 18°C heat / 27°C cool during 10pm–7am
THERMOSTATS = {
    "flat": {
        "heat_day": 22.22,   "heat_night": 22.22,
        "cool_day": 23.89,   "cool_night": 23.89,
    },
    "stepped": {
        "heat_day": 22.22,   "heat_night": 18.00,   # 4°C setback
        "cool_day": 23.89,   "cool_night": 27.00,   # 3°C setback
    },
}

LOCATIONS = {
    "1A_Miami":         {"city": "Miami, FL",          "cz": "1A", "type": "Hot-Humid"},
    "2B_Phoenix":       {"city": "Phoenix, AZ",        "cz": "2B", "type": "Hot-Dry"},
    "3C_SanFrancisco":  {"city": "San Francisco, CA",  "cz": "3C", "type": "Marine"},
    "5A_Chicago":       {"city": "Chicago, IL",        "cz": "5A", "type": "Cold-Humid"},
    "6B_Denver":        {"city": "Denver, CO",         "cz": "6B", "type": "Cold-Dry"},
}

OUTPUT_METERS = """
Output:Meter,Electricity:Facility,Hourly;
Output:Meter,NaturalGas:Facility,Hourly;
Output:Meter,Electricity:HVAC,Hourly;
Output:Meter,Electricity:Cooling,Hourly;
Output:Meter,Electricity:Heating,Hourly;
Output:Meter,Electricity:InteriorLights,Hourly;
Output:Meter,Electricity:InteriorEquipment,Hourly;
Output:Meter,WaterSystems:NaturalGas,Hourly;
"""


def fix_idf_and_set_thermostat(base_idf: Path, out_idf: Path, tstat: dict):
    """
    Modify AirflowNetwork_Simple_House.idf:
    1. Set flat or stepped thermostat schedules (Schedule:Compact format)
    2. Add output meters
    """
    text = base_idf.read_text()

    hd = tstat["heat_day"]
    hn = tstat["heat_night"]
    cd = tstat["cool_day"]
    cn = tstat["cool_night"]

    # ── Replace heating setpoint Schedule:Compact ──
    if hn == hd:
        new_heat = (
            f"  Schedule:Compact,\n"
            f"    Dual Heating Setpoints,  !- Name\n"
            f"    Temperature,             !- Schedule Type Limits Name\n"
            f"    Through: 12/31,          !- Field 1\n"
            f"    For: AllDays,            !- Field 2\n"
            f"    Until: 24:00,{hd:.1f};       !- Field 3"
        )
    else:
        new_heat = (
            f"  Schedule:Compact,\n"
            f"    Dual Heating Setpoints,  !- Name\n"
            f"    Temperature,             !- Schedule Type Limits Name\n"
            f"    Through: 12/31,          !- Field 1\n"
            f"    For: AllDays,            !- Field 2\n"
            f"    Until: 07:00,{hn:.1f},      !- Field 3  (night setback)\n"
            f"    Until: 22:00,{hd:.1f},      !- Field 4  (comfort)\n"
            f"    Until: 24:00,{hn:.1f};      !- Field 5  (night setback)"
        )

    # ── Replace cooling setpoint Schedule:Compact ──
    if cn == cd:
        new_cool = (
            f"  Schedule:Compact,\n"
            f"    Dual Cooling Setpoints,  !- Name\n"
            f"    Temperature,             !- Schedule Type Limits Name\n"
            f"    Through: 12/31,          !- Field 1\n"
            f"    For: AllDays,            !- Field 2\n"
            f"    Until: 24:00,{cd:.1f};       !- Field 3"
        )
    else:
        new_cool = (
            f"  Schedule:Compact,\n"
            f"    Dual Cooling Setpoints,  !- Name\n"
            f"    Temperature,             !- Schedule Type Limits Name\n"
            f"    Through: 12/31,          !- Field 1\n"
            f"    For: AllDays,            !- Field 2\n"
            f"    Until: 07:00,{cn:.1f},      !- Field 3  (night float up)\n"
            f"    Until: 22:00,{cd:.1f},      !- Field 4  (comfort)\n"
            f"    Until: 24:00,{cn:.1f};      !- Field 5  (night float up)"
        )

    text = re.sub(
        r"Schedule:Compact,\s+Dual Heating Setpoints,.*?Until: 24:00,[^;]+;",
        new_heat, text, flags=re.DOTALL, count=1
    )
    text = re.sub(
        r"Schedule:Compact,\s+Dual Cooling Setpoints,.*?Until: 24:00,[^;]+;",
        new_cool, text, flags=re.DOTALL, count=1
    )

    # ── Enable full-year weather simulation ──
    text = re.sub(
        r"(No|Yes)(,?\s*!- Run Simulation for Weather File Run Periods)",
        r"Yes\2",
        text, count=1
    )
    # Replace the two single-day RunPeriods with a full-year run
    text = re.sub(
        r"  RunPeriod,.*?;",
        "",
        text, flags=re.DOTALL,
    )
    text = text + """
  RunPeriod,
    Annual Simulation,       !- Name
    1,                       !- Begin Month
    1,                       !- Begin Day of Month
    ,                        !- Begin Year
    12,                      !- End Month
    31,                      !- End Day of Month
    ,                        !- End Year
    Sunday,                  !- Day of Week for Start Day
    Yes,                     !- Use Weather File Holidays and Special Days
    Yes,                     !- Use Weather File Daylight Saving Period
    No,                      !- Apply Weekend Holiday Rule
    Yes,                     !- Use Weather File Rain Indicators
    Yes;                     !- Use Weather File Snow Indicators
"""

    # ── Remove existing meter/variable outputs and add ours ──
    text = re.sub(r"\s*Output:Meter:MeterFileOnly,[^\n]+\n", "\n", text)
    text = re.sub(r"\s*Output:Variable,[^\n]+\n", "\n", text)
    text += "\n" + OUTPUT_METERS

    out_idf.write_text(text)


def parse_csv_output(ep_out_dir: Path) -> dict:
    """Legacy stub — replaced by extract_monthly."""
    return {}


def run_ep(idf: Path, epw: Path, run_dir: Path) -> Path:
    """Run EnergyPlus. Returns output directory."""
    run_dir.mkdir(parents=True, exist_ok=True)
    # Copy IDD file
    idd_src = EP_EXE.parent / "Energy+.idd"
    if idd_src.exists():
        shutil.copy(idd_src, run_dir / "Energy+.idd")
    # Copy schedules file (required by Schedule:File objects in IDF)
    schedules_src = SIM_DIR / "in.schedules.csv"
    if schedules_src.exists():
        shutil.copy(schedules_src, run_dir / "in.schedules.csv")

    cmd = [
        str(EP_EXE),
        "-w", str(epw),
        "-d", str(run_dir),
        "-r", str(idf),
    ]
    print(f"  Running: energyplus -w {epw.name} ...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(run_dir))
    if result.returncode != 0:
        err_file = run_dir / "eplusout.err"
        if err_file.exists():
            print("  EnergyPlus errors:")
            print(err_file.read_text()[-3000:])
        else:
            print("  STDOUT:", result.stdout[-1000:])
            print("  STDERR:", result.stderr[-1000:])
        raise RuntimeError(f"EnergyPlus failed (rc={result.returncode})")
    return run_dir


def extract_results(run_dir: Path) -> dict:
    """Parse EnergyPlus HTML + CSV output. Returns annual end-uses, hourly data, and comfort metrics."""
    import csv as csv_mod
    import re

    GJ_TO_KWH = 277.778
    J_TO_KWH  = 1 / 3_600_000

    # ── Annual end-uses + comfort from HTML tabular report ──
    htm_file = run_dir / "eplustbl.htm"
    if not htm_file.exists():
        raise FileNotFoundError(f"No eplustbl.htm in {run_dir}")
    html = htm_file.read_text()

    def find_row(html, row_label):
        """Extract first numeric cell after a row label in an HTML table."""
        pattern = (r'<td[^>]*>\s*' + re.escape(row_label) +
                   r'\s*</td>\s*<td[^>]*>\s*([\d.]+)\s*</td>')
        m = re.search(pattern, html)
        return float(m.group(1)) if m else 0.0

    def find_two_cols(html, row_label):
        """Extract first two numeric cells after a row label."""
        pattern = (r'<td[^>]*>\s*' + re.escape(row_label) +
                   r'\s*</td>\s*<td[^>]*>\s*([\d.]+)\s*</td>\s*<td[^>]*>\s*([\d.]+)\s*</td>')
        m = re.search(pattern, html)
        return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)

    # Heating: electricity + gas
    heat_elec_gj, heat_gas_gj = find_two_cols(html, "Heating")
    cool_elec_gj, _ = find_two_cols(html, "Cooling")
    fans_elec_gj, _ = find_two_cols(html, "Fans")
    lights_gj, _ = find_two_cols(html, "Interior Lighting")
    equip_gj, _ = find_two_cols(html, "Interior Equipment")
    water_elec_gj, water_gas_gj = find_two_cols(html, "Water Systems")
    total_elec_gj, total_gas_gj = find_two_cols(html, "Total End Uses")

    end_uses = {
        "cooling_kwh":      cool_elec_gj * GJ_TO_KWH,
        "heating_elec_kwh": heat_elec_gj * GJ_TO_KWH,
        "heating_gas_kwh":  heat_gas_gj  * GJ_TO_KWH,
        "heating_kwh":      (heat_elec_gj + heat_gas_gj) * GJ_TO_KWH,
        "fans_kwh":         fans_elec_gj * GJ_TO_KWH,
        "lights_kwh":       lights_gj    * GJ_TO_KWH,
        "equip_kwh":        equip_gj     * GJ_TO_KWH,
        "water_kwh":        (water_elec_gj + water_gas_gj) * GJ_TO_KWH,
        "total_elec_kwh":   total_elec_gj * GJ_TO_KWH,
        "total_gas_kwh":    total_gas_gj  * GJ_TO_KWH,
        "total_kwh":        (total_elec_gj + total_gas_gj) * GJ_TO_KWH,
    }

    comfort = {
        "setpoint_not_met_heating_hrs": find_row(html, "Time Setpoint Not Met During Occupied Heating"),
        "setpoint_not_met_cooling_hrs": find_row(html, "Time Setpoint Not Met During Occupied Cooling"),
        "ashrae55_discomfort_hrs":      find_row(html, "Time Not Comfortable Based on Simple ASHRAE 55-2004"),
    }

    # ── Hourly data from CSV (skip design-day rows; annual starts at 01/01) ──
    csv_file = run_dir / "eplusout.csv"
    if not csv_file.exists():
        raise FileNotFoundError(f"No eplusout.csv in {run_dir}")

    hourly_elec = []
    hourly_gas  = []
    hourly_hvac = []
    in_annual   = False

    with open(csv_file, newline="") as f:
        reader = csv_mod.reader(f)
        next(reader)  # header
        for row in reader:
            date_str = row[0].strip()
            if not in_annual:
                if date_str.startswith("01/01"):
                    in_annual = True
                else:
                    continue
            hourly_elec.append(float(row[1]) * J_TO_KWH)
            hourly_gas.append( float(row[2]) * J_TO_KWH)
            hourly_hvac.append(float(row[3]) * J_TO_KWH)

    # ── Monthly aggregation ──
    monthly_hvac = [0.0] * 12
    monthly_elec = [0.0] * 12
    monthly_gas  = [0.0] * 12
    month_lengths = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    h = 0
    for m, days in enumerate(month_lengths):
        for _ in range(days * 24):
            if h < len(hourly_hvac):
                monthly_hvac[m] += hourly_hvac[h]
                monthly_elec[m] += hourly_elec[h]
                monthly_gas[m]  += hourly_gas[h]
                h += 1

    return {
        "annual":           end_uses,
        "comfort":          comfort,
        "monthly_hvac_kwh": monthly_hvac,
        "monthly_elec_kwh": monthly_elec,
        "monthly_gas_kwh":  monthly_gas,
        "hourly_hvac_kwh":  hourly_hvac,
        "hourly_elec_kwh":  hourly_elec,
        "hourly_gas_kwh":   hourly_gas,
    }


def main():
    results = {}

    for loc_key, loc_info in LOCATIONS.items():
        epw = EPW_DIR / f"{loc_key}.epw"
        if not epw.exists():
            print(f"SKIP {loc_key}: EPW not found")
            continue

        results[loc_key] = {"meta": loc_info, "thermostats": {}}

        for tstat_name, tstat in THERMOSTATS.items():
            print(f"\n{'='*60}")
            print(f"  {loc_info['city']}  |  {tstat_name} thermostat")
            print(f"{'='*60}")

            idf_out = IDF_DIR / f"{loc_key}_{tstat_name}.idf"
            fix_idf_and_set_thermostat(BASE_IDF, idf_out, tstat)

            run_dir = SIM_DIR / "ep_runs" / f"{loc_key}_{tstat_name}"
            try:
                run_ep(idf_out, epw, run_dir)
                res = extract_results(run_dir)
                results[loc_key]["thermostats"][tstat_name] = res
                ann = res["annual"]
                cft = res["comfort"]
                print(f"  Total: {ann.get('total_kwh',0):.0f} kWh | Cool(elec): {ann.get('cooling_kwh',0):.0f} | Heat(gas): {ann.get('heating_gas_kwh',0):.0f}")
                print(f"  Discomfort: {cft.get('ashrae55_discomfort_hrs',0):.0f} hrs | Setpoint missed heat: {cft.get('setpoint_not_met_heating_hrs',0):.0f} hrs")
            except Exception as e:
                import traceback
                print(f"  ERROR: {e}")
                traceback.print_exc()
                results[loc_key]["thermostats"][tstat_name] = {"error": str(e)}

    # Save results
    out_file = OUT_DIR / "thermostat_results.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n\nResults saved to {out_file}")
    print(f"File size: {out_file.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
