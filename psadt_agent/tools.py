"""
CrewAI-compatible tool wrappers around MCP server functions.
Each tool calls the corresponding mcp_server.py function directly
(in-process) so we don't need a running HTTP MCP server for local use.
"""

import json
from pathlib import Path
from typing import Optional, Type
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

# Import MCP tool functions directly
import sys
sys.path.insert(0, str(Path(__file__).parent))
import mcp_server as mcp
from psadt_template import PackageSpec, build_package_structure, generate_deploy_script
from utils import (
    get_package_history,
    list_all_packaged_apps,
    save_package_record,
    explain_exit_code,
    get_logger,
)
from config import PSADT_TEMPLATE_PATH, TEST_MODE

log = get_logger("psadt-tools")


# ---------------------------------------------------------------------------
# Pydantic schemas for tool inputs
# ---------------------------------------------------------------------------

class InstallerPathInput(BaseModel):
    installer_path: str = Field(description="Absolute path to the installer file (EXE/MSI/MSIX)")

class SearchSwitchesInput(BaseModel):
    installer_path: str = Field(description="Absolute path to the installer")
    app_name: str = Field(default="", description="Application name for web search context")

class DependenciesInput(BaseModel):
    installer_path: str = Field(description="Absolute path to the installer")
    app_name: str = Field(description="Application name")
    app_version: str = Field(default="", description="Application version")

class TemplatePathInput(BaseModel):
    template_path: str = Field(default=PSADT_TEMPLATE_PATH, description="Path to PSADT template directory")

class BuildFolderInput(BaseModel):
    spec_json: str = Field(description="JSON-serialized PackageSpec fields")
    template_path: str = Field(default=PSADT_TEMPLATE_PATH, description="PSADT template directory path")

class HistoryInput(BaseModel):
    app_name: str = Field(description="Application name to look up history for")

class GenerateScriptInput(BaseModel):
    spec_json: str = Field(description="JSON-serialized PackageSpec fields")

class ExecuteTestInput(BaseModel):
    package_dir: str = Field(description="Path to the built PSADT package directory")
    deployment_type: str = Field(default="Install", description="Install | Uninstall | Repair")
    test_mode: str = Field(default=TEST_MODE, description="host | sandbox")

class ParseLogsInput(BaseModel):
    app_name: str = Field(default="", description="App name fragment to filter logs")
    log_path: str = Field(default="", description="Specific log file path (optional)")

class VerifyRegistryInput(BaseModel):
    app_name_fragment: str = Field(description="Partial app name to search in uninstall registry keys")

class VerifyFileInput(BaseModel):
    file_path: str = Field(description="Full path to a file that should exist after installation")

class VerifyWmiInput(BaseModel):
    app_name_fragment: str = Field(description="Partial app name to search via WMI Win32_Product")

class CleanupInput(BaseModel):
    app_name: str = Field(description="Application name to uninstall post-test")
    product_code: str = Field(default="", description="MSI product GUID if known")
    uninstall_string: str = Field(default="", description="Raw uninstall command if known")

class PowerShellInput(BaseModel):
    script: str = Field(description="PowerShell script block to execute")
    timeout_seconds: int = Field(default=120, description="Execution timeout in seconds")


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _get_installer_metadata(installer_path: str) -> str:
    result = mcp.get_installer_metadata(installer_path)
    return json.dumps(result, indent=2)


def _search_silent_switches(installer_path: str, app_name: str = "") -> str:
    """
    First check installer metadata for type, then return documented silent switches
    with guidance for verification.
    """
    meta = mcp.get_installer_metadata(installer_path)
    itype = meta.get("installer_type", "EXE").upper()

    switch_guides = {
        "MSI": {
            "recommended": "/quiet /norestart ALLUSERS=1 REBOOT=ReallySuppress",
            "alternatives": ["/qn /norestart", "/passive /norestart"],
            "notes": "MSI switches are standardized. /quiet = fully silent. ALLUSERS=1 installs for all users.",
        },
        "MSIX": {
            "recommended": "Add-AppxProvisionedPackage -Online -SkipLicense (no CLI flags)",
            "alternatives": ["Add-AppxPackage for current user only"],
            "notes": "MSIX packages do not use command-line silent switches. Use PowerShell cmdlets.",
        },
        "EXE": {
            "recommended": "Depends on installer framework",
            "alternatives": [
                "NSIS: /S",
                "InnoSetup: /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-",
                "InstallShield: /s /v\"/qn /norestart\"",
                "WiX Burn: /quiet /norestart",
                "Squirrel: --silent",
                "Advanced Installer: /qn",
            ],
            "notes": (
                f"Product name from metadata: {meta.get('ProductName','N/A')}. "
                "Check installer with 7-Zip or innounp to identify the framework. "
                "Run with /? or --help to see embedded help if not deployable."
            ),
        },
    }
    guide = switch_guides.get(itype, switch_guides["EXE"])
    return json.dumps({"installer_type": itype, "metadata": meta, "switch_guide": guide}, indent=2)


def _analyze_dependencies(installer_path: str, app_name: str, app_version: str = "") -> str:
    """
    Analyze the installer for likely runtime dependencies using metadata and heuristics.
    """
    meta = mcp.get_installer_metadata(installer_path)
    product_name = (meta.get("ProductName") or app_name).lower()

    # Heuristic dependency map — extend as needed
    dep_hints = []
    if any(k in product_name for k in [".net", "dotnet", "wpf", "winforms"]):
        dep_hints.append("Microsoft .NET Runtime — download from https://dotnet.microsoft.com/download")
    if "vcredist" in product_name or "visual c++" in product_name:
        dep_hints.append("Visual C++ Redistributable — already a dependency package itself")
    if any(k in product_name for k in ["java", "jre", "jdk"]):
        dep_hints.append("Java Runtime Environment — verify target JRE version")
    if "sql" in product_name:
        dep_hints.append("SQL Server Native Client / ODBC Driver — check app documentation")
    if "webview" in product_name or "edge" in product_name:
        dep_hints.append("Microsoft Edge WebView2 Runtime")

    # Check if VCRedist DLLs are bundled
    files_dir = Path(installer_path).parent
    bundled = [f.name for f in files_dir.glob("vcredist*.exe")] if files_dir.exists() else []

    return json.dumps({
        "app": app_name,
        "version": app_version,
        "installer_type": meta.get("installer_type"),
        "inferred_dependencies": dep_hints if dep_hints else ["No common dependencies detected — verify with app vendor documentation"],
        "bundled_prerequisites": bundled,
        "recommendation": (
            "Include any required redistributables in the PSADT Files folder and install them "
            "in the Pre-Installation phase before the main installer runs."
            if dep_hints else
            "No dependencies automatically detected. Confirm with vendor release notes."
        ),
    }, indent=2)


def _read_psadt_template(template_path: str = PSADT_TEMPLATE_PATH) -> str:
    """Read and validate a PSADT template directory, extracting version info."""
    tp = Path(template_path)
    if not tp.exists():
        return json.dumps({"success": False, "error": f"Template path not found: {template_path}"})

    # Look for AppDeployToolkitMain.ps1 to extract version
    main_script = tp / "AppDeployToolkit" / "AppDeployToolkitMain.ps1"
    version_info = "Unknown"
    available_functions = []

    if main_script.exists():
        content = main_script.read_text(encoding="utf-8", errors="replace")
        # Extract version
        import re
        ver_match = re.search(r'\$appDeployToolkitVersion\s*=\s*[\'"]([^\'"]+)[\'"]', content)
        if ver_match:
            version_info = ver_match.group(1)
        # Extract exported functions
        funcs = re.findall(r'^Function\s+([\w-]+)', content, re.MULTILINE)
        available_functions = funcs[:50]  # cap for readability

    # List structure
    structure = mcp.list_directory(template_path, "**/*")

    return json.dumps({
        "success": True,
        "template_path": str(tp),
        "psadt_version": version_info,
        "main_script_found": main_script.exists(),
        "available_functions": available_functions,
        "structure": structure.get("entries", []),
    }, indent=2)


def _build_folder_structure(spec_json: str, template_path: str = PSADT_TEMPLATE_PATH) -> str:
    try:
        spec_data = json.loads(spec_json)
        spec = PackageSpec(**spec_data)
        pkg_dir = build_package_structure(spec, template_path)
        return json.dumps({"success": True, "package_dir": pkg_dir}, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


def _get_package_history(app_name: str) -> str:
    records = get_package_history(app_name)
    all_apps = list_all_packaged_apps()
    return json.dumps({
        "app_name": app_name,
        "history_count": len(records),
        "records": records[:10],  # last 10
        "all_packaged_apps": all_apps,
    }, indent=2)


def _generate_script(spec_json: str) -> str:
    try:
        spec_data = json.loads(spec_json)
        spec = PackageSpec(**spec_data)
        script_content = generate_deploy_script(spec)
        # Write to package dir if set
        if spec.package_dir:
            script_path = Path(spec.package_dir) / "Deploy-Application.ps1"
            script_path.write_text(script_content, encoding="utf-8")
            spec.script_path = str(script_path)
            log.info(f"Script written → {script_path}")
            return json.dumps({
                "success": True,
                "script_path": str(script_path),
                "preview": script_content[:500] + "...[truncated]",
            }, indent=2)
        return json.dumps({"success": True, "script_content": script_content}, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


def _execute_install_test(package_dir: str, deployment_type: str = "Install", test_mode: str = TEST_MODE) -> str:
    script_path = Path(package_dir) / "Deploy-Application.ps1"
    if not script_path.exists():
        return json.dumps({"success": False, "error": f"Deploy-Application.ps1 not found in {package_dir}"})

    if test_mode == "sandbox":
        from psadt_template import generate_wsb_config
        wsb = generate_wsb_config(package_dir)
        launch = mcp.launch_windows_sandbox(wsb)
        return json.dumps({"success": True, "mode": "sandbox", "wsb_config": wsb, "launch_result": launch})

    # Host execution
    ps_cmd = (
        f'& "{script_path}" '
        f'-DeploymentType {deployment_type} '
        f'-DeployMode Silent '
        f'-AllowRebootPassThru:$false'
    )
    result = mcp.run_powershell(ps_cmd, timeout_seconds=300)
    exit_code = result.get("exit_code", -1)
    explanation = explain_exit_code(exit_code)

    return json.dumps({
        "success": result["success"],
        "exit_code": exit_code,
        "exit_code_meaning": explanation,
        "stdout": result.get("stdout", "")[:2000],
        "stderr": result.get("stderr", "")[:1000],
        "mode": "host",
    }, indent=2)


def _parse_logs(app_name: str = "", log_path: str = "") -> str:
    result = mcp.parse_psadt_log(
        log_path=log_path if log_path else None,
        app_name=app_name if app_name else None,
    )
    return json.dumps(result, indent=2)


def _verify_registry(app_name_fragment: str) -> str:
    result = mcp.find_installed_app_registry(app_name_fragment)
    return json.dumps(result, indent=2)


def _verify_file(file_path: str) -> str:
    result = mcp.verify_file_exists_on_system(file_path)
    return json.dumps(result, indent=2)


def _verify_wmi(app_name_fragment: str) -> str:
    result = mcp.verify_app_installed_wmi(app_name_fragment)
    return json.dumps(result, indent=2)


def _cleanup_test_install(app_name: str, product_code: str = "", uninstall_string: str = "") -> str:
    """
    Uninstall the test app after a successful test to restore system state.
    """
    log.info(f"[Cleanup] Initiating post-test removal of '{app_name}'")

    if product_code:
        ps = f'Start-Process msiexec.exe -ArgumentList "/x {product_code} /quiet /norestart" -Wait -PassThru | Select-Object ExitCode | ConvertTo-Json'
    elif uninstall_string:
        ps = f'$r = Start-Process -FilePath "cmd.exe" -ArgumentList \'/c "{uninstall_string}"\' -Wait -PassThru; $r.ExitCode'
    else:
        # Find via registry and attempt removal
        reg_result = mcp.find_installed_app_registry(app_name)
        matches = reg_result.get("matches", [])
        if not matches:
            return json.dumps({"success": False, "error": f"No registry entry found for '{app_name}' to clean up"})
        match = matches[0]
        quiet_str = match.get("QuietUninstallString") or match.get("UninstallString", "")
        if not quiet_str:
            return json.dumps({"success": False, "error": "No uninstall string found in registry"})
        ps = f'$r = Start-Process -FilePath "cmd.exe" -ArgumentList \'/c "{quiet_str}"\' -Wait -PassThru; $r.ExitCode'

    result = mcp.run_powershell(ps, timeout_seconds=180)
    exit_code = result.get("exit_code", -1)

    # Verify removal
    still_installed = mcp.find_installed_app_registry(app_name)
    cleaned = len(still_installed.get("matches", [])) == 0

    return json.dumps({
        "success": cleaned,
        "cleanup_exit_code": exit_code,
        "exit_code_meaning": explain_exit_code(exit_code),
        "still_installed": not cleaned,
        "remaining_entries": still_installed.get("matches", []),
    }, indent=2)


def _run_powershell(script: str, timeout_seconds: int = 120) -> str:
    result = mcp.run_powershell(script, timeout_seconds)
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# CrewAI BaseTool subclasses
# ---------------------------------------------------------------------------

class GetInstallerMetadataTool(BaseTool):
    name: str = "get_installer_metadata"
    description: str = "Extract metadata (name, version, vendor, type) from an EXE, MSI, or MSIX installer file."
    args_schema: Type[BaseModel] = InstallerPathInput
    def _run(self, installer_path: str) -> str:
        return _get_installer_metadata(installer_path)

class SearchSilentSwitchesTool(BaseTool):
    name: str = "search_silent_switches"
    description: str = "Determine the correct silent installation switches for a given installer file."
    args_schema: Type[BaseModel] = SearchSwitchesInput
    def _run(self, installer_path: str, app_name: str = "") -> str:
        return _search_silent_switches(installer_path, app_name)

class AnalyzeDependenciesTool(BaseTool):
    name: str = "analyze_dependencies"
    description: str = "Analyze an installer for required runtime dependencies (e.g., .NET, VCRedist)."
    args_schema: Type[BaseModel] = DependenciesInput
    def _run(self, installer_path: str, app_name: str, app_version: str = "") -> str:
        return _analyze_dependencies(installer_path, app_name, app_version)

class ReadPsadtTemplateTool(BaseTool):
    name: str = "read_psadt_template"
    description: str = "Read and validate a PSADT template directory, returning the toolkit version and available functions."
    args_schema: Type[BaseModel] = TemplatePathInput
    def _run(self, template_path: str = PSADT_TEMPLATE_PATH) -> str:
        return _read_psadt_template(template_path)

class BuildFolderStructureTool(BaseTool):
    name: str = "build_folder_structure"
    description: str = "Create the PSADT package folder structure by copying the template and injecting the installer."
    args_schema: Type[BaseModel] = BuildFolderInput
    def _run(self, spec_json: str, template_path: str = PSADT_TEMPLATE_PATH) -> str:
        return _build_folder_structure(spec_json, template_path)

class GetPackageHistoryTool(BaseTool):
    name: str = "get_package_history"
    description: str = "Retrieve historical package records for an application to ensure consistency and detect version upgrades."
    args_schema: Type[BaseModel] = HistoryInput
    def _run(self, app_name: str) -> str:
        return _get_package_history(app_name)

class GenerateScriptTool(BaseTool):
    name: str = "generate_deploy_script"
    description: str = "Generate and write a complete Deploy-Application.ps1 for the PSADT package."
    args_schema: Type[BaseModel] = GenerateScriptInput
    def _run(self, spec_json: str) -> str:
        return _generate_script(spec_json)

class ExecuteInstallTestTool(BaseTool):
    name: str = "execute_install_test"
    description: str = "Execute the PSADT Deploy-Application.ps1 in Install mode on the host or in Windows Sandbox."
    args_schema: Type[BaseModel] = ExecuteTestInput
    def _run(self, package_dir: str, deployment_type: str = "Install", test_mode: str = TEST_MODE) -> str:
        return _execute_install_test(package_dir, deployment_type, test_mode)

class ParseLogsTool(BaseTool):
    name: str = "parse_psadt_logs"
    description: str = "Parse PSADT/MSI logs from C:\\Windows\\Logs\\Software to determine install success or failure."
    args_schema: Type[BaseModel] = ParseLogsInput
    def _run(self, app_name: str = "", log_path: str = "") -> str:
        return _parse_logs(app_name, log_path)

class VerifyRegistryTool(BaseTool):
    name: str = "verify_registry_installation"
    description: str = "Search the Windows Uninstall registry keys to verify an application is installed."
    args_schema: Type[BaseModel] = VerifyRegistryInput
    def _run(self, app_name_fragment: str) -> str:
        return _verify_registry(app_name_fragment)

class VerifyFileTool(BaseTool):
    name: str = "verify_file_exists"
    description: str = "Verify that a specific file exists on the system (e.g., the app's main executable)."
    args_schema: Type[BaseModel] = VerifyFileInput
    def _run(self, file_path: str) -> str:
        return _verify_file(file_path)

class VerifyWmiTool(BaseTool):
    name: str = "verify_wmi_installation"
    description: str = "Verify application installation via WMI Win32_Product class."
    args_schema: Type[BaseModel] = VerifyWmiInput
    def _run(self, app_name_fragment: str) -> str:
        return _verify_wmi(app_name_fragment)

class CleanupTestInstallTool(BaseTool):
    name: str = "cleanup_test_installation"
    description: str = "Uninstall the test application after a successful QA test to restore the system to its original state."
    args_schema: Type[BaseModel] = CleanupInput
    def _run(self, app_name: str, product_code: str = "", uninstall_string: str = "") -> str:
        return _cleanup_test_install(app_name, product_code, uninstall_string)

class RunPowerShellTool(BaseTool):
    name: str = "run_powershell"
    description: str = "Execute an arbitrary PowerShell script and return stdout, stderr, and exit code."
    args_schema: Type[BaseModel] = PowerShellInput
    def _run(self, script: str, timeout_seconds: int = 120) -> str:
        return _run_powershell(script, timeout_seconds)


# Singleton instances imported by agents.py
get_installer_metadata_tool = GetInstallerMetadataTool()
search_silent_switches_tool = SearchSilentSwitchesTool()
analyze_dependencies_tool = AnalyzeDependenciesTool()
read_psadt_template_tool = ReadPsadtTemplateTool()
build_folder_structure_tool = BuildFolderStructureTool()
get_package_history_tool = GetPackageHistoryTool()
generate_script_tool = GenerateScriptTool()
execute_install_test_tool = ExecuteInstallTestTool()
parse_logs_tool = ParseLogsTool()
verify_registry_tool = VerifyRegistryTool()
verify_file_tool = VerifyFileTool()
verify_wmi_tool = VerifyWmiTool()
cleanup_test_install_tool = CleanupTestInstallTool()
run_powershell_tool = RunPowerShellTool()
