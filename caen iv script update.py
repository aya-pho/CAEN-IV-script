"""
IV sweep for the CAEN DT55XXE (DT5519EM) high-voltage supply.

The user is prompted for sweep settings and sample identifiers. Output is saved as:
    {LotNo}_{Type}_{Position}_{Test}_{RunNumber}.dat
e.g.  LOT123_PIN_R1C1_IV_1.dat
"""

import csv
import os
import re
import time
from datetime import datetime

import numpy as np
from caen_libs import caenhvwrapper as hv

# ----------------------------------------------------------------------------
# Fixed hardware / instrument settings (edit only if your setup changes)
# ----------------------------------------------------------------------------
SYSTEM_TYPE = hv.SystemType.DT55XXE
LINK_TYPE = hv.LinkType.USB_VCP
DEFAULT_PORT = "COM11"
SLOT, CH = 0, 2          # channels on this device are 0, 1, 2, 3
V_START = 0.0
HW_MAX_V = 800.0         # absolute upper bound accepted at the prompt (set to your module's limit)
HW_MAX_I = 1000.0        # absolute upper bound for compliance in uA
I_LIMIT_FRACTION = 0.90  # software cut-off = 90% of compliance
TRIP_TIME = 2.0          # s
RAMP_RATE = 10.0         # V/s (used for both RUp and RDwn)
V_TOLERANCE = 0.15       # V, how close VMon must be to the target
SLEEP = 1                # s, settle time after each step
SETTLE_TIMEOUT = 15      # s, max wait for VMon to reach the target
OUTPUT_DIR = "."         # where .dat files are saved


# ----------------------------------------------------------------------------
# User input helpers
# ----------------------------------------------------------------------------
def ask_float(prompt, min_val, max_val):
    """Prompt until the user enters a number in (min_val, max_val]."""
    while True:
        raw = input(prompt).strip()
        try:
            val = float(raw)
        except ValueError:
            print("  Please enter a number.")
            continue
        if not (min_val < val <= max_val):
            print(f"  Value must be greater than {min_val} and at most {max_val}.")
            continue
        return val


def clean_field(text):
    """Make a string safe for filenames; underscores are the field separator so replace them."""
    text = text.strip().replace("_", "-").replace(" ", "-")
    return re.sub(r"[^A-Za-z0-9\-.]", "", text)


def ask_text(prompt, pattern=None, error="  Invalid entry."):
    while True:
        val = clean_field(input(prompt))
        if not val:
            print("  This field cannot be empty.")
            continue
        if pattern and not re.fullmatch(pattern, val, re.IGNORECASE):
            print(error)
            continue
        return val


def next_run_number(directory, prefix):
    """Find the next unused run number for {prefix}_{n}.dat."""
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)\.dat$")
    used = [int(m.group(1)) for name in os.listdir(directory) if (m := pattern.match(name))]
    return max(used, default=0) + 1


def get_user_settings():
    print("=== IV Sweep Setup ===")
    max_v = ask_float(f"Max voltage MAX_V (V, up to {HW_MAX_V:g}): ", V_START, HW_MAX_V)
    v_step = ask_float(f"Voltage step V_STEP (V, up to {max_v:g}): ", 0, max_v)
    max_i = ask_float(f"Compliance MAX_I (uA, up to {HW_MAX_I:g}): ", 0, HW_MAX_I)

    print("\n=== Sample Info (used for the filename) ===")
    lot = ask_text("Lot No.: ")
    stype = ask_text("Type (sensor variant): ")
    position = ask_text(
        "Position (row/column, e.g. R1C1): ",
        pattern=r"R\d+C\d+",
        error="  Use the format R<row>C<col>, e.g. R1C1.",
    ).upper()
    test = ask_text("Test (e.g. IV): ")

    prefix = f"{lot}_{stype}_{position}_{test}"
    run = next_run_number(OUTPUT_DIR, prefix)
    filename = os.path.join(OUTPUT_DIR, f"{prefix}_{run}.dat")

    n_points = len(build_voltage_steps(max_v, v_step))
    print("\n--- Summary ---")
    print(f"  Sweep       : {V_START:g} V -> {max_v:g} V in {v_step:g} V steps ({n_points} points)")
    print(f"  Compliance  : {max_i:g} uA (software cut-off at {max_i * I_LIMIT_FRACTION:g} uA)")
    print(f"  Output file : {filename}")
    if input("Start measurement? [y/N]: ").strip().lower() != "y":
        raise SystemExit("Cancelled.")

    return max_v, v_step, max_i, filename


def build_voltage_steps(max_v, v_step):
    """Inclusive voltage list from V_START to max_v."""
    steps = np.arange(V_START, max_v + v_step / 2, v_step)
    return np.round(np.clip(steps, V_START, max_v), 6)


# ----------------------------------------------------------------------------
# Measurement
# ----------------------------------------------------------------------------
def run_sweep(device, writer, datafile, v_steps, limit_i):
    """Step through voltages. Returns when finished, aborted, or on compliance."""
    for v_target in v_steps:
        device.set_ch_param(SLOT, [CH], "VSet", float(v_target))
        time.sleep(SLEEP)

        t0 = time.time()
        while True:
            (v_mon,) = device.get_ch_param(SLOT, [CH], "VMon")
            (i_mon,) = device.get_ch_param(SLOT, [CH], "IMon")
            (status,) = device.get_ch_param(SLOT, [CH], "ChStatus")

            if not (status & 0x1):
                print("Hardware has turned off (channel not ON). Stopping sweep.")
                return

            if i_mon > limit_i:
                print(f"Current {i_mon:.5f} uA exceeds limit {limit_i:.3f} uA. Stopping sweep.")
                timestamp = datetime.now().strftime("%H_%M_%S")
                writer.writerow([timestamp, v_target, v_mon, i_mon, status])
                datafile.flush()
                return

            if abs(v_mon - v_target) < V_TOLERANCE:
                break

            if time.time() - t0 > SETTLE_TIMEOUT:
                print(f"Warning: VMon did not settle at {v_target} V within {SETTLE_TIMEOUT} s.")
                break

        timestamp = datetime.now().strftime("%H_%M_%S")
        writer.writerow([timestamp, v_target, v_mon, i_mon, status])
        datafile.flush()
        print(f"[{timestamp}] Set: {v_target:g} V | Mon: {v_mon:.3f} V | I: {i_mon:.5f} uA")


def safe_shutdown(device):
    """Ramp to 0 V, wait for it to get there, then switch the channel off."""
    try:
        device.set_ch_param(SLOT, [CH], "VSet", 0.0)
        t0 = time.time()
        while time.time() - t0 < SETTLE_TIMEOUT * 4:
            (v_mon,) = device.get_ch_param(SLOT, [CH], "VMon")
            if abs(v_mon) < 1.0:
                break
            time.sleep(0.5)
        device.set_ch_param(SLOT, [CH], "Pw", 0)
        try:
            device.exec_comm("ClearAlarm")
        except Exception:
            pass
        print("Shutdown complete.")
    except Exception as e:
        if "NOTCONNECTED" in str(e):
            print("Device disconnected; shutdown complete.")
        else:
            print(f"WARNING: shutdown may be incomplete ({e}). Check the HV output manually!")


def main():
    max_v, v_step, max_i, filename = get_user_settings()
    limit_i = max_i * I_LIMIT_FRACTION
    v_steps = build_voltage_steps(max_v, v_step)

    port = input(f"COM port [{DEFAULT_PORT}]: ").strip() or DEFAULT_PORT

    with hv.Device.open(SYSTEM_TYPE, LINK_TYPE, port) as device, \
            open(filename, "x", newline="") as datafile:  # "x" never overwrites an existing file
        writer = csv.writer(datafile, delimiter="\t")
        writer.writerow(["Timestamp", "VSet", "VMon", "IMon", "ChStatus"])
        print("Connection successful.")

        try:
            device.set_ch_param(SLOT, [CH], "VSet", 0)
            device.set_ch_param(SLOT, [CH], "ISet", max_i)
            device.set_ch_param(SLOT, [CH], "Trip", TRIP_TIME)
            # ImonRange = 1 for small MAX_I values, 0 for larger MAX_I values
            device.set_ch_param(SLOT, [CH], "ImonRange", 1 if max_i <= 10 else 0)
            device.set_ch_param(SLOT, [CH], "RUp", RAMP_RATE)
            device.set_ch_param(SLOT, [CH], "RDwn", RAMP_RATE)
            device.set_ch_param(SLOT, [CH], "Pw", 1)
            time.sleep(SLEEP)
            print("System on.")

            run_sweep(device, writer, datafile, v_steps, limit_i)
            print("Sweep complete. Starting shutdown.")

        except KeyboardInterrupt:
            print("\nInterrupted by user. Powering off.")
        finally:
            safe_shutdown(device)

    print(f"Data saved to {filename}")


if __name__ == "__main__":
    main()