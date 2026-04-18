"""
CrewAI Task definitions.

Each task maps to one agent and one phase of the PSADT automation workflow:
  Phase 1 — Research      (Researcher agent)
  Phase 2 — Architecture  (Architect agent)
  Phase 3 — Scripting     (Scripter agent)
  Phase 4 — QA Testing    (QA Tester agent)
"""

import json
from crewai import Task
from crewai import Agent


# ---------------------------------------------------------------------------
# Phase 1 — Research
# ---------------------------------------------------------------------------

def make_research_task(
    agent: Agent,
    installer_path: str,
    app_name: str,
    app_version: str,
) -> Task:
    return Task(
        description=f"""
You are given an installer at path: {installer_path}
Application name hint: "{app_name}"
Application version hint: "{app_version}"

Your deliverables:
1. Call get_installer_metadata to extract: installer type (EXE/MSI/MSIX), product name,
   product version, vendor/manufacturer, and any available product code (MSI GUID).
2. Call search_silent_switches to determine the definitive silent installation switches.
   - For MSI: use /quiet /norestart ALLUSERS=1 REBOOT=ReallySuppress
   - For EXE: identify the installer framework from metadata and provide the correct switch.
   - For MSIX: explain the Add-AppxPackage/Add-AppxProvisionedPackage approach.
3. Call analyze_dependencies to identify required prerequisites (e.g., .NET, VCRedist).
   For each dependency: state the name, version, download URL (if known), and whether
   it should be bundled in the PSADT Files folder.

Output a single JSON object with these keys:
{{
  "app_name": "...",
  "app_version": "...",
  "app_vendor": "...",
  "installer_type": "EXE|MSI|MSIX",
  "silent_switches": "...",
  "product_code": "{{GUID}}" or null,
  "architecture": "x64|x86|ARM64",
  "dependencies": [
    {{"name": "...", "version": "...", "url": "...", "bundle": true|false}}
  ],
  "notes": "..."
}}
""",
        expected_output=(
            "A JSON object with app_name, app_version, app_vendor, installer_type, "
            "silent_switches, product_code, architecture, dependencies list, and notes."
        ),
        agent=agent,
    )


# ---------------------------------------------------------------------------
# Phase 2 — Architecture
# ---------------------------------------------------------------------------

def make_architecture_task(
    agent: Agent,
    research_output: str,   # JSON string from Phase 1
    template_path: str,
    installer_path: str,
) -> Task:
    return Task(
        description=f"""
Research output from the Researcher agent:
{research_output}

PSADT template path: {template_path}
Installer path: {installer_path}

Your deliverables:
1. Call read_psadt_template on the template path to:
   - Confirm the PSADT version (critical — the Scripter must use version-correct cmdlets)
   - List available toolkit functions
   - Validate that AppDeployToolkitMain.ps1 exists
2. Call get_package_history with the app_name from research output to:
   - Check if this app has been packaged before
   - Report the previous version if upgrading (version delta)
   - Note any previously used switches or known issues from history
3. Parse the research JSON and build a PackageSpec JSON for the Scripter.
   The PackageSpec must include all fields needed to call build_folder_structure.
4. Call build_folder_structure with the PackageSpec JSON and template_path.
   This creates the package directory on disk.

Output a single JSON object:
{{
  "psadt_version": "...",
  "available_functions": ["Execute-MSI", "Execute-Process", ...],
  "history_summary": "First package for this app" or "Upgrading from v1.x to v2.x",
  "package_dir": "C:\\\\path\\\\to\\\\built\\\\package",
  "spec_json": {{...complete PackageSpec as JSON object...}}
}}
""",
        expected_output=(
            "A JSON object with psadt_version, available_functions, history_summary, "
            "package_dir path, and the full spec_json for the Scripter."
        ),
        agent=agent,
        context=[],  # will be set by crew
    )


# ---------------------------------------------------------------------------
# Phase 3 — Scripting
# ---------------------------------------------------------------------------

def make_scripting_task(
    agent: Agent,
    architecture_output: str,  # JSON string from Phase 2
) -> Task:
    return Task(
        description=f"""
Architecture output from the Architect agent:
{architecture_output}

Your deliverables:
1. Read the spec_json from the architecture output.
2. Call read_psadt_template one more time to internalize the exact available functions
   for the detected PSADT version. DO NOT use cmdlets that don't exist in that version.
3. Call generate_deploy_script with the spec_json.
   The generated Deploy-Application.ps1 MUST:
   a. Import AppDeployToolkitMain.ps1 correctly
   b. Implement THREE phases: Pre-Installation, Installation, Post-Installation
   c. In Pre-Installation: detect and silently remove ALL existing versions of the app
      using Get-InstalledApplication (PSADT built-in) — version-agnostic cleanup
   d. In Installation: use the correct cmdlet for the installer type:
      - MSI → Execute-MSI -Action Install with ALLUSERS=1 REBOOT=ReallySuppress
      - EXE → Execute-Process with the correct silent switches
      - MSIX → Execute-Process calling PowerShell Add-AppxProvisionedPackage
   e. Set $global:AllowRebootPassThru = $false
   f. Use -DeployMode Silent / NonInteractive — NO interactive dialogs
   g. Use Show-InstallationProgress for silent status (no user prompts)
   h. Handle exit code 3010 (soft reboot required) gracefully — log it, do not reboot
4. Verify the script was written to the package_dir.

Output:
{{
  "script_path": "C:\\\\path\\\\to\\\\Deploy-Application.ps1",
  "package_dir": "...",
  "installer_type": "MSI|EXE|MSIX",
  "silent_switches_used": "...",
  "cleanup_strategy": "description of how previous versions are removed",
  "script_preview": "first 300 chars of generated script..."
}}
""",
        expected_output=(
            "A JSON object with script_path, package_dir, installer_type, "
            "silent_switches_used, cleanup_strategy, and script_preview."
        ),
        agent=agent,
    )


# ---------------------------------------------------------------------------
# Phase 4 — QA Testing
# ---------------------------------------------------------------------------

def make_qa_task(
    agent: Agent,
    scripting_output: str,   # JSON string from Phase 3
    research_output: str,    # JSON string from Phase 1 (for app name / product code)
    test_mode: str = "host",
) -> Task:
    return Task(
        description=f"""
Scripting output from the Scripter agent:
{scripting_output}

Research output (for app metadata):
{research_output}

Test mode: {test_mode}

Your deliverables:
1. Execute the install test:
   - Call execute_install_test with package_dir and deployment_type="Install" and test_mode="{test_mode}"
   - Record the exit code and map it to its meaning using your knowledge of MSI exit codes.
   - Exit code 0 = success, 3010 = success with pending reboot (acceptable), anything else = FAILURE.

2. Parse the install logs:
   - Call parse_psadt_logs with the app_name from research output.
   - Look for error lines, exit codes in the log, and the final status marker.
   - If any error lines contain "1603", "1618", or "0x8", flag them explicitly.

3. Validate installation via THREE methods:
   a. Registry: Call verify_registry_installation with the app_name fragment.
   b. WMI: Call verify_wmi_installation with the app_name fragment.
   c. File: If an install_location is known from research, call verify_file_exists
      on the main executable path.
   Report pass/fail for each validation method.

4. Post-test cleanup (MANDATORY if install succeeded):
   - Call cleanup_test_installation with app_name and product_code (if MSI).
   - Verify the app is fully removed after cleanup.
   - If cleanup fails, report it explicitly — DO NOT leave partial installations.

5. Produce a DETAILED test report. The report must:
   - State overall PASS or FAIL
   - For failures: pinpoint EXACTLY where in the chain the failure occurred
     (e.g., "Installer returned exit code 1603 during Installation phase — likely missing VCRedist dependency")
   - For successes: confirm all three validation methods passed and cleanup completed

Output JSON:
{{
  "overall_result": "PASS|FAIL",
  "install_exit_code": 0,
  "exit_code_meaning": "...",
  "log_analysis": {{
    "final_status": "SUCCESS|FAILURE",
    "error_lines": [...],
    "exit_codes_found": [...]
  }},
  "validation": {{
    "registry": {{"pass": true, "details": "..."}},
    "wmi": {{"pass": true, "details": "..."}},
    "file": {{"pass": true, "details": "..."}}
  }},
  "cleanup": {{
    "completed": true,
    "cleanup_exit_code": 0,
    "still_installed": false
  }},
  "failure_diagnosis": null,
  "recommendations": []
}}
""",
        expected_output=(
            "A detailed JSON test report with overall_result (PASS/FAIL), "
            "exit code analysis, three-method validation results, cleanup confirmation, "
            "and precise failure diagnosis if applicable."
        ),
        agent=agent,
    )
