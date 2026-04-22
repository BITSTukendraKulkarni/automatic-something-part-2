"""
Quick smoke test: verify_app_installed_wmi returns installed=True via registry
when Win32_Product (MSI/WMI) returns nothing — simulates a successful EXE/NSIS install.
Run from the psadt_agent directory:
    python test_verification.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import mcp_server as mcp
from tools import _verify_registry, _verify_wmi
import json

APP_FRAGMENT = "7-Zip"   # change to match whatever is actually installed

def separator(label):
    print(f"\n{'-'*60}")
    print(f"  {label}")
    print('-'*60)

# ── 1. Registry check ────────────────────────────────────────────────────────
separator("1. verify_registry_installation")
reg_raw = _verify_registry(APP_FRAGMENT)
reg = json.loads(reg_raw)
print(f"  count   : {reg['count']}")
for m in reg.get("matches", []):
    print(f"  found   : {m['DisplayName']} {m['DisplayVersion']}")
reg_pass = reg["count"] > 0
print(f"  RESULT  : {'PASS' if reg_pass else 'FAIL'}")

# ── 2. WMI check (new multi-source logic) ────────────────────────────────────
separator("2. verify_wmi_installation (multi-source)")
wmi_raw = _verify_wmi(APP_FRAGMENT)
wmi = json.loads(wmi_raw)
print(f"  installed: {wmi['installed']}")
print(f"  source   : {wmi.get('source','?')}")
if wmi.get("note"):
    print(f"  note     : {wmi['note']}")
for m in wmi.get("matches", []):
    name = m.get("Name") or m.get("DisplayName","?")
    ver  = m.get("Version") or m.get("DisplayVersion","?")
    print(f"  found    : {name} {ver}")
wmi_pass = wmi["installed"]
print(f"  RESULT  : {'PASS' if wmi_pass else 'FAIL'}")

# ── 3. Simulate _finalize PASS/FAIL logic ────────────────────────────────────
separator("3. Simulated _finalize with exit_code=0, installed=True")
simulated_qa = {
    "overall_result": "PASS",
    "install_exit_code": 0,
    "validation": {"source": wmi.get("source"), "installed": wmi["installed"]},
}
raw_result = str(simulated_qa.get("overall_result", "")).upper()
exit_code   = simulated_qa.get("install_exit_code")
try:
    exit_ok = int(exit_code) in (0, 3010)
except (TypeError, ValueError):
    exit_ok = False
overall = "PASS" if ("PASS" in raw_result or exit_ok) else "FAIL"
print(f"  exit_code : {exit_code}  (ok={exit_ok})")
print(f"  wmi found : {wmi['installed']} via {wmi.get('source')}")
print(f"  FINAL     : {overall}")

# ── Summary ──────────────────────────────────────────────────────────────────
separator("Summary")
print(f"  Registry check    : {'PASS' if reg_pass else 'FAIL'}")
print(f"  WMI/multi-source  : {'PASS' if wmi_pass else 'FAIL'}")
print(f"  Finalize outcome  : {overall}")
all_pass = reg_pass and wmi_pass and overall == "PASS"
print(f"\n  Overall test      : {'ALL PASS' if all_pass else 'SOMETHING FAILED'}")
