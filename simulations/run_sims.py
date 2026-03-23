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
BASE_IDF = Path("/tmp/base_model.idf")
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
    1. Fix ZoneHVAC:EquipmentList bug (remove AirLoopHVAC:UnitarySystem from zone list)
    2. Set flat or stepped thermostat schedules
    3. Add output meters
    """
    text = base_idf.read_text()

    # ── FIX 1: Remove AirLoopHVAC:UnitarySystem from zone equipment list ──
    # Replace the entire ZoneHVAC:EquipmentList block — keep only Equipment 1 (ADU)
    old_eql = r"""ZoneHVAC:EquipmentList,
  conditioned space Equipment List,       !- Name
  SequentialLoad,                         !- Load Distribution Scheme
  ZoneHVAC:AirDistributionUnit,           !- Zone Equipment Object Type 1
  ADU central ac terminal,                !- Zone Equipment Name 1
  1,                                      !- Zone Equipment Cooling Sequence 1
  1,                                      !- Zone Equipment Heating or No-Load Sequence 1
  Sequential Fraction Schedule 1,         !- Zone Equipment Sequential Cooling Fraction Schedule Name 1
  Sequential Fraction Schedule,           !- Zone Equipment Sequential Heating Fraction Schedule Name 1
  AirLoopHVAC:UnitarySystem,              !- Zone Equipment Object Type 2
  unit heater unitary system,             !- Zone Equipment Name 2
  2,                                      !- Zone Equipment Cooling Sequence 2
  2,                                      !- Zone Equipment Heating or No-Load Sequence 2
  Sequential Fraction Schedule 3,         !- Zone Equipment Sequential Cooling Fraction Schedule Name 2
  Sequential Fraction Schedule 2;         !- Zone Equipment Sequential Heating Fraction Schedule Name 2"""

    new_eql = """ZoneHVAC:EquipmentList,
  conditioned space Equipment List,       !- Name
  SequentialLoad,                         !- Load Distribution Scheme
  ZoneHVAC:AirDistributionUnit,           !- Zone Equipment Object Type 1
  ADU central ac terminal,                !- Zone Equipment Name 1
  1,                                      !- Zone Equipment Cooling Sequence 1
  1,                                      !- Zone Equipment Heating or No-Load Sequence 1
  ,                                       !- Zone Equipment Sequential Cooling Fraction Schedule Name 1
  ;                                       !- Zone Equipment Sequential Heating Fraction Schedule Name 1"""

    text = text.replace(old_eql, new_eql)

    # ── FIX 2: Replace thermostat schedules ──
    hd = tstat["heat_day"]
    hn = tstat["heat_night"]
    cd = tstat["cool_day"]
    cn = tstat["cool_night"]

    if hn == hd:
        # Flat heating: constant all day
        new_heat_day = f"""Schedule:Day:Interval,
  heating setpoint allday1,               !- Name
  Temperature,                            !- Schedule Type Limits Name
  No,                                     !- Interpolate to Timestep
  24:00,                                  !- Time 1 {{hh:mm}}
  {hd:.4f};                                !- Value Until Time 1"""
    else:
        # Stepped: setback 10pm–7am
        new_heat_day = f"""Schedule:Day:Interval,
  heating setpoint allday1,               !- Name
  Temperature,                            !- Schedule Type Limits Name
  No,                                     !- Interpolate to Timestep
  07:00,                                  !- Time 1 {{hh:mm}}
  {hn:.4f},                               !- Value Until Time 1
  22:00,                                  !- Time 2 {{hh:mm}}
  {hd:.4f},                               !- Value Until Time 2
  24:00,                                  !- Time 3 {{hh:mm}}
  {hn:.4f};                               !- Value Until Time 3"""

    if cn == cd:
        new_cool_day = f"""Schedule:Day:Interval,
  cooling setpoint allday1,               !- Name
  Temperature,                            !- Schedule Type Limits Name
  No,                                     !- Interpolate to Timestep
  24:00,                                  !- Time 1 {{hh:mm}}
  {cd:.4f};                                !- Value Until Time 1"""
    else:
        new_cool_day = f"""Schedule:Day:Interval,
  cooling setpoint allday1,               !- Name
  Temperature,                            !- Schedule Type Limits Name
  No,                                     !- Interpolate to Timestep
  07:00,                                  !- Time 1 {{hh:mm}}
  {cn:.4f},                               !- Value Until Time 1
  22:00,                                  !- Time 2 {{hh:mm}}
  {cd:.4f},                               !- Value Until Time 2
  24:00,                                  !- Time 3 {{hh:mm}}
  {cn:.4f};                               !- Value Until Time 3"""

    # Replace the day schedule blocks
    text = re.sub(
        r"Schedule:Day:Interval,\s+heating setpoint allday1,.*?;",
        new_heat_day, text, flags=re.DOTALL, count=1
    )
    text = re.sub(
        r"Schedule:Day:Interval,\s+cooling setpoint allday1,.*?;",
        new_cool_day, text, flags=re.DOTALL, count=1
    )

    # ── FIX 3: Remove existing output meters and add ours ──
    text = re.sub(r"Output:Meter,[^\n]*\n", "", text)
    text = re.sub(r"Output:Variable,[^\n]*\n", "", text)

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
    """Parse EnergyPlus msgpack output. Returns annual end-uses, hourly HVAC, and comfort metrics."""
    import msgpack

    mp_file = run_dir / "eplusout.msgpack"
    if not mp_file.exists():
        raise FileNotFoundError(f"No eplusout.msgpack in {run_dir}")

    with open(mp_file, "rb") as f:
        data = msgpack.unpack(f, raw=False)

    GJ_TO_KWH = 277.778

    # ── Annual end-uses from TabularReports ──
    end_uses = {}
    comfort = {}
    for report in data.get("TabularReports", []):
        if report.get("ReportName") == "AnnualBuildingUtilityPerformanceSummary":
            for table in report.get("Tables", []):
                if table.get("TableName") == "End Uses":
                    rows = table.get("Rows", {})
                    # Electricity column is index 0
                    def gj_to_kwh(row_name):
                        try:
                            return float(rows.get(row_name, ["0"])[0]) * GJ_TO_KWH
                        except Exception:
                            return 0.0
                    end_uses = {
                        "cooling_kwh":   gj_to_kwh("Cooling"),
                        "heating_kwh":   gj_to_kwh("Heating"),
                        "fans_kwh":      gj_to_kwh("Fans"),
                        "lights_kwh":    gj_to_kwh("Interior Lighting"),
                        "equip_kwh":     gj_to_kwh("Interior Equipment"),
                        "water_kwh":     gj_to_kwh("Water Systems"),
                        "total_kwh":     gj_to_kwh("Total End Uses"),
                    }

                if table.get("TableName") == "Comfort and Setpoint Not Met Summary":
                    rows = table.get("Rows", {})
                    def hrs(row_name):
                        try:
                            return float(rows.get(row_name, ["0"])[0])
                        except Exception:
                            return 0.0
                    comfort = {
                        "setpoint_not_met_cooling_hrs": hrs("Time Setpoint Not Met During Occupied Cooling"),
                        "setpoint_not_met_heating_hrs": hrs("Time Setpoint Not Met During Occupied Heating"),
                        "ashrae55_discomfort_hrs":       hrs("Time Not Comfortable Based on Simple ASHRAE 55-2004"),
                    }

    # ── Hourly HVAC from MeterData ──
    hourly_hvac = []
    hourly_elec = []
    try:
        rows = data["MeterData"]["Hourly"]["Rows"]
        cols = data["MeterData"]["Hourly"]["Cols"]
        hvac_idx = next((i for i, c in enumerate(cols) if "HVAC" in c["Variable"]), None)
        elec_idx = next((i for i, c in enumerate(cols) if c["Variable"] == "Electricity:Facility"), None)
        J_TO_KWH = 1 / 3_600_000
        for row in rows:
            vals = list(row.values())[0]
            hourly_hvac.append(vals[hvac_idx] * J_TO_KWH if hvac_idx is not None else 0.0)
            hourly_elec.append(vals[elec_idx] * J_TO_KWH if elec_idx is not None else 0.0)
    except Exception:
        pass

    # ── Monthly aggregation from hourly ──
    monthly_hvac  = [0.0] * 12
    monthly_elec  = [0.0] * 12
    month_lengths = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    h = 0
    for m, days in enumerate(month_lengths):
        for _ in range(days * 24):
            if h < len(hourly_hvac):
                monthly_hvac[m] += hourly_hvac[h]
                monthly_elec[m] += hourly_elec[h]
                h += 1

    return {
        "annual": end_uses,
        "comfort": comfort,
        "monthly_hvac_kwh": monthly_hvac,
        "monthly_elec_kwh": monthly_elec,
        "hourly_hvac_kwh":  hourly_hvac,   # full 8760
        "hourly_elec_kwh":  hourly_elec,   # full 8760
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
                print(f"  Total: {ann.get('total_kwh',0):.0f} kWh | Cool: {ann.get('cooling_kwh',0):.0f} | Heat: {ann.get('heating_kwh',0):.0f}")
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
