"""
FastMCP Server — PSADT Agentic AI
Provides tools for: File I/O, Shell execution, Registry querying,
Log parsing, and System validation.
"""

import os
import json
import subprocess
import winreg
import re
import glob as _glob
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastmcp import FastMCP

mcp = FastMCP(
    name="psadt-mcp-server",
    instructions=(
        "MCP server for PSADT automation. "
        "Provides file I/O, shell execution, registry, and log analysis tools."
    ),
)

# ---------------------------------------------------------------------------
# File I/O Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def read_file(path: str) -> dict:
    """Read a text file and return its contents."""
    try:
        p = Path(path)
        if not p.exists():
            return {"success": False, "error": f"File not found: {path}"}
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"success": True, "path": str(p), "content": content, "size_bytes": p.stat().st_size}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def write_file(path: str, content: str, overwrite: bool = False) -> dict:
    """Write content to a file. Will NOT overwrite unless overwrite=True."""
    try:
        p = Path(path)
        if p.exists() and not overwrite:
            return {"success": False, "error": f"File exists. Set overwrite=True to replace: {path}"}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"success": True, "path": str(p), "bytes_written": len(content.encode("utf-8"))}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def list_directory(path: str, pattern: str = "*") -> dict:
    """List files/folders in a directory, optionally filtered by glob pattern."""
    try:
        p = Path(path)
        if not p.exists():
            return {"success": False, "error": f"Path not found: {path}"}
        matches = list(p.glob(pattern))
        entries = [
            {
                "name": m.name,
                "type": "dir" if m.is_dir() else "file",
                "size_bytes": m.stat().st_size if m.is_file() else None,
                "modified": datetime.fromtimestamp(m.stat().st_mtime).isoformat(),
            }
            for m in sorted(matches)
        ]
        return {"success": True, "path": str(p), "count": len(entries), "entries": entries}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def create_directory(path: str) -> dict:
    """Create a directory (and any missing parents)."""
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        return {"success": True, "path": path}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def copy_directory_tree(src: str, dst: str) -> dict:
    """Copy an entire directory tree from src to dst."""
    import shutil
    try:
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return {"success": True, "src": src, "dst": dst}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def file_exists(path: str) -> dict:
    """Check whether a file or directory exists."""
    p = Path(path)
    return {"exists": p.exists(), "is_file": p.is_file(), "is_dir": p.is_dir(), "path": str(p)}


# ---------------------------------------------------------------------------
# Shell / Process Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def run_powershell(script: str, timeout_seconds: int = 120) -> dict:
    """
    Execute a PowerShell script block and return stdout, stderr, and exit code.
    The script runs with -NonInteractive -NoProfile flags.
    """
    cmd = [
        "powershell.exe",
        "-NonInteractive",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-Command", script,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "exit_code": -1, "error": f"Timed out after {timeout_seconds}s"}
    except Exception as e:
        return {"success": False, "exit_code": -1, "error": str(e)}


@mcp.tool()
def run_command(command: str, working_dir: Optional[str] = None, timeout_seconds: int = 120) -> dict:
    """Run an arbitrary shell command and return stdout, stderr, and exit code."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=working_dir,
            timeout=timeout_seconds,
        )
        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "exit_code": -1, "error": f"Timed out after {timeout_seconds}s"}
    except Exception as e:
        return {"success": False, "exit_code": -1, "error": str(e)}


@mcp.tool()
def get_installer_metadata(installer_path: str) -> dict:
    """
    Extract metadata from an EXE or MSI installer using PowerShell.
    Returns product name, version, manufacturer, and inferred silent switches.
    """
    path = Path(installer_path)
    if not path.exists():
        return {"success": False, "error": f"Installer not found: {installer_path}"}

    ext = path.suffix.lower()

    # Escape single quotes in path for PowerShell string embedding
    escaped_path = installer_path.replace("'", "''")

    if ext == ".msi":
        script = f"""
try {{
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $db = $installer.OpenDatabase('{escaped_path}', 0)
    $view = $db.OpenView("SELECT Property, Value FROM Property WHERE Property IN ('ProductName','ProductVersion','Manufacturer','ProductCode')")
    $view.Execute()
    $props = @{{}}
    do {{
        $record = $view.Fetch()
        if ($record -ne $null) {{ $props[$record.StringData(1)] = $record.StringData(2) }}
    }} while ($record -ne $null)
    $props | ConvertTo-Json
}} catch {{ Write-Output "ERROR: $_" }}
"""
        r = run_powershell(script)
        silent_switches = "/quiet /norestart ALLUSERS=1 REBOOT=ReallySuppress"
        try:
            meta = json.loads(r["stdout"])
            meta["silent_switches"] = silent_switches
            meta["installer_type"] = "MSI"
            return {"success": True, **meta}
        except Exception:
            return {"success": True, "installer_type": "MSI", "silent_switches": silent_switches,
                    "raw": r.get("stdout", ""), "note": "Could not parse COM metadata"}

    elif ext == ".exe":
        script = f"""
$versionInfo = (Get-Item '{escaped_path}').VersionInfo
[PSCustomObject]@{{
    ProductName    = $versionInfo.ProductName
    ProductVersion = $versionInfo.ProductVersion
    FileVersion    = $versionInfo.FileVersion
    CompanyName    = $versionInfo.CompanyName
    Description    = $versionInfo.FileDescription
}} | ConvertTo-Json
"""
        r = run_powershell(script)
        try:
            meta = json.loads(r["stdout"])
            meta["installer_type"] = "EXE"
            meta["silent_switches"] = "/S (NSIS) | /silent | /quiet | --silent (common — verify)"
            return {"success": True, **meta}
        except Exception:
            return {"success": True, "installer_type": "EXE",
                    "silent_switches": "/S | /silent | /quiet",
                    "raw": r.get("stdout", "")}

    elif ext == ".msix" or ext == ".appx":
        return {
            "success": True,
            "installer_type": "MSIX",
            "silent_switches": "Add-AppxPackage (no interactive flags needed)",
            "note": "MSIX packages are installed via Add-AppxPackage cmdlet",
        }

    return {"success": False, "error": f"Unsupported installer type: {ext}"}


# ---------------------------------------------------------------------------
# Registry Tools
# ---------------------------------------------------------------------------

_HIVE_MAP = {
    "HKLM": winreg.HKEY_LOCAL_MACHINE,
    "HKCU": winreg.HKEY_CURRENT_USER,
    "HKCR": winreg.HKEY_CLASSES_ROOT,
    "HKU":  winreg.HKEY_USERS,
    "HKCC": winreg.HKEY_CURRENT_CONFIG,
}


def _parse_reg_path(full_path: str):
    parts = full_path.split("\\", 1)
    hive_str = parts[0].upper()
    subkey = parts[1] if len(parts) > 1 else ""
    hive = _HIVE_MAP.get(hive_str)
    if hive is None:
        raise ValueError(f"Unknown registry hive: {hive_str}")
    return hive, subkey


@mcp.tool()
def registry_get_value(key_path: str, value_name: str) -> dict:
    """
    Read a single registry value.
    key_path format: HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion
    """
    try:
        hive, subkey = _parse_reg_path(key_path)
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            data, reg_type = winreg.QueryValueEx(key, value_name)
            return {"success": True, "key": key_path, "value_name": value_name,
                    "data": str(data), "reg_type": reg_type}
    except FileNotFoundError:
        return {"success": False, "error": f"Key or value not found: {key_path}\\{value_name}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def registry_list_subkeys(key_path: str) -> dict:
    """List all subkeys of a registry key."""
    try:
        hive, subkey = _parse_reg_path(key_path)
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            subkeys = []
            i = 0
            while True:
                try:
                    subkeys.append(winreg.EnumKey(key, i))
                    i += 1
                except OSError:
                    break
            return {"success": True, "key": key_path, "subkeys": subkeys}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def find_installed_app_registry(app_name_fragment: str) -> dict:
    """
    Search Uninstall registry keys for an installed application matching app_name_fragment.
    Returns display name, version, uninstall string, and quiet uninstall string.
    """
    uninstall_paths = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    results = []
    for hive_str, hive in [("HKLM", winreg.HKEY_LOCAL_MACHINE), ("HKCU", winreg.HKEY_CURRENT_USER)]:
        for path in uninstall_paths:
            try:
                with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as base:
                    i = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(base, i)
                            i += 1
                            with winreg.OpenKey(base, subkey_name, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as sub:
                                def _qv(name, _key=sub):
                                    try:
                                        return winreg.QueryValueEx(_key, name)[0]
                                    except Exception:
                                        return ""
                                display_name = _qv("DisplayName")
                                if app_name_fragment.lower() in display_name.lower():
                                    results.append({
                                        "DisplayName": display_name,
                                        "DisplayVersion": _qv("DisplayVersion"),
                                        "Publisher": _qv("Publisher"),
                                        "UninstallString": _qv("UninstallString"),
                                        "QuietUninstallString": _qv("QuietUninstallString"),
                                        "InstallLocation": _qv("InstallLocation"),
                                        "Hive": hive_str,
                                        "Key": f"{hive_str}\\{path}\\{subkey_name}",
                                    })
                        except OSError:
                            break
            except Exception:
                continue
    return {"success": True, "matches": results, "count": len(results)}


# ---------------------------------------------------------------------------
# Log Analysis Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def parse_psadt_log(log_path: Optional[str] = None, app_name: Optional[str] = None) -> dict:
    """
    Parse PSADT/MSI install logs from C:\\Windows\\Logs\\Software.
    Returns success/failure determination, exit codes, and key events.
    """
    log_dir = Path(r"C:\Windows\Logs\Software")
    if log_path:
        log_files = [Path(log_path)]
    elif app_name:
        log_files = sorted(log_dir.glob(f"*{app_name}*"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    else:
        log_files = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]

    if not log_files:
        return {"success": False, "error": "No log files found", "log_dir": str(log_dir)}

    all_results = []
    for lf in log_files:
        try:
            text = lf.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()

            errors = [l for l in lines if re.search(r"\b(error|fail|1603|1618|1619|0x8)\b", l, re.I)]
            warnings = [l for l in lines if re.search(r"\bwarn(ing)?\b", l, re.I)]
            success_markers = [l for l in lines if re.search(r"\b(success|complete|installed|0x0+\b|exit code 0)\b", l, re.I)]
            exit_codes = re.findall(r"[Ee]xit [Cc]ode[:\s]+(-?\d+)", text)

            # PSADT-specific: look for [Installation Phase] and final status
            phase_markers = [l for l in lines if re.search(r"\[(Pre|Post|Installation)\]", l)]
            final_status = "UNKNOWN"
            if any("exit code 0" in l.lower() or "installation successful" in l.lower() for l in lines):
                final_status = "SUCCESS"
            elif errors:
                final_status = "FAILURE"

            all_results.append({
                "file": str(lf),
                "final_status": final_status,
                "exit_codes_found": exit_codes,
                "error_lines": errors[:10],
                "warning_count": len(warnings),
                "success_markers": success_markers[:5],
                "phase_markers": phase_markers[:10],
                "total_lines": len(lines),
            })
        except Exception as e:
            all_results.append({"file": str(lf), "error": str(e)})

    overall = "SUCCESS" if all(r.get("final_status") == "SUCCESS" for r in all_results if "final_status" in r) else "FAILURE"
    return {"success": True, "overall_status": overall, "log_analyses": all_results}


@mcp.tool()
def get_recent_event_log_errors(source: str = "Application", hours: int = 1) -> dict:
    """Retrieve recent error/warning events from the Windows Event Log."""
    script = f"""
$since = (Get-Date).AddHours(-{hours})
Get-WinEvent -LogName '{source}' -ErrorAction SilentlyContinue |
  Where-Object {{ $_.TimeCreated -ge $since -and $_.LevelDisplayName -in 'Error','Warning' }} |
  Select-Object -First 20 TimeCreated, LevelDisplayName, ProviderName, Id, Message |
  ConvertTo-Json -Depth 3
"""
    r = run_powershell(script)
    try:
        events = json.loads(r["stdout"]) if r["stdout"] else []
        return {"success": True, "events": events if isinstance(events, list) else [events]}
    except Exception:
        return {"success": True, "events": [], "raw": r.get("stdout", "")[:2000]}


# ---------------------------------------------------------------------------
# System Validation Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def verify_app_installed_wmi(app_name_fragment: str) -> dict:
    """
    Verify application installation state using three sources in order:
      1. Win32_Product  — MSI installs only; unreliable when run non-elevated,
                          can silently return nothing even for installed MSIs,
                          and never lists EXE/NSIS installs.
      2. Get-Package    — PackageManagement; broader coverage across providers.
      3. Registry       — HKLM/HKCU Uninstall keys; the most reliable source
                          and the only one that catches EXE/NSIS installs like 7-Zip.
    Callers should treat installed=True from any source as a valid PASS.
    """
    safe_fragment = app_name_fragment.replace("'", "''")

    # 1. Win32_Product — MSI installs only
    wmi_script = f"""
try {{
    $r = Get-CimInstance -ClassName Win32_Product -Filter "Name LIKE '%{safe_fragment}%'" -ErrorAction Stop |
         Select-Object Name, Version, Vendor, InstallDate
    if ($r) {{ $r | ConvertTo-Json -Depth 2 }} else {{ '[]' }}
}} catch {{ '[]' }}
"""
    r = run_powershell(wmi_script, timeout_seconds=60)
    try:
        wmi_data = json.loads(r["stdout"]) if r["stdout"] else []
        if isinstance(wmi_data, dict):
            wmi_data = [wmi_data]
    except Exception:
        wmi_data = []

    if wmi_data:
        return {"success": True, "installed": True, "source": "Win32_Product", "matches": wmi_data}

    # 2. Get-Package — covers more package managers
    pkg_script = f"Get-Package -Name '*{safe_fragment}*' -ErrorAction SilentlyContinue | Select-Object Name, Version, ProviderName | ConvertTo-Json -Depth 2"
    r2 = run_powershell(pkg_script, timeout_seconds=30)
    try:
        pkg_data = json.loads(r2["stdout"]) if r2["stdout"] else []
        if isinstance(pkg_data, dict):
            pkg_data = [pkg_data]
    except Exception:
        pkg_data = []

    if pkg_data:
        return {"success": True, "installed": True, "source": "Get-Package", "matches": pkg_data}

    # 3. Registry uninstall keys — works for EXE/NSIS and MSI installs
    reg_result = find_installed_app_registry(app_name_fragment)
    reg_matches = reg_result.get("matches", [])
    if reg_matches:
        return {
            "success": True, "installed": True, "source": "registry",
            "matches": [{"Name": m["DisplayName"], "Version": m["DisplayVersion"], "Publisher": m["Publisher"]} for m in reg_matches],
            "note": "Not in Win32_Product (EXE/NSIS install) — found in registry uninstall keys",
        }

    return {
        "success": True, "installed": False,
        "source": "all_checked",
        "note": f"Not found in Win32_Product, Get-Package, or registry for fragment: '{app_name_fragment}'",
    }


@mcp.tool()
def verify_app_installed_get_package(app_name_fragment: str) -> dict:
    """Verify installation using PowerShell Get-Package (PackageManagement)."""
    script = f"Get-Package -Name '*{app_name_fragment}*' -ErrorAction SilentlyContinue | ConvertTo-Json -Depth 2"
    r = run_powershell(script)
    try:
        data = json.loads(r["stdout"]) if r["stdout"] else []
        if isinstance(data, dict):
            data = [data]
        return {"success": True, "installed": len(data) > 0, "packages": data}
    except Exception:
        return {"success": True, "installed": False, "raw": r.get("stdout", "")}


@mcp.tool()
def verify_file_exists_on_system(path: str) -> dict:
    """Check whether a specific file path exists on the system."""
    p = Path(path)
    return {
        "success": True,
        "exists": p.exists(),
        "is_file": p.is_file(),
        "size_bytes": p.stat().st_size if p.is_file() else None,
        "path": str(p),
    }


@mcp.tool()
def get_system_info() -> dict:
    """Return basic OS and hardware information."""
    script = """
[PSCustomObject]@{
    OS            = (Get-CimInstance Win32_OperatingSystem).Caption
    OSVersion     = (Get-CimInstance Win32_OperatingSystem).Version
    Architecture  = $env:PROCESSOR_ARCHITECTURE
    ComputerName  = $env:COMPUTERNAME
    Username      = $env:USERNAME
    TempDir       = $env:TEMP
    SystemDrive   = $env:SystemDrive
    PSVersion     = $PSVersionTable.PSVersion.ToString()
    FreeDiskGB    = [math]::Round((Get-PSDrive C).Free / 1GB, 2)
} | ConvertTo-Json
"""
    r = run_powershell(script)
    try:
        return {"success": True, **json.loads(r["stdout"])}
    except Exception:
        return {"success": False, "raw": r.get("stdout", ""), "error": r.get("stderr", "")}


# ---------------------------------------------------------------------------
# Windows Sandbox Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def check_sandbox_available() -> dict:
    """Check whether Windows Sandbox feature is available and enabled."""
    script = """
$feature = Get-WindowsOptionalFeature -Online -FeatureName 'Containers-DisposableClientVM' -ErrorAction SilentlyContinue
if ($feature) {
    [PSCustomObject]@{ Available=$true; State=$feature.State } | ConvertTo-Json
} else {
    [PSCustomObject]@{ Available=$false; State='NotFound' } | ConvertTo-Json
}
"""
    r = run_powershell(script)
    try:
        return {"success": True, **json.loads(r["stdout"])}
    except Exception:
        return {"success": False, "error": r.get("stderr", "")}


@mcp.tool()
def launch_windows_sandbox(wsb_config_path: str) -> dict:
    """
    Launch Windows Sandbox with a given .wsb configuration file.
    Returns immediately; sandbox runs async.
    """
    if not Path(wsb_config_path).exists():
        return {"success": False, "error": f".wsb config not found: {wsb_config_path}"}
    result = run_command(f'start "" "{wsb_config_path}"')
    return {"success": True, "message": "Sandbox launch initiated", "config": wsb_config_path}


if __name__ == "__main__":
    mcp.run(transport="stdio")
