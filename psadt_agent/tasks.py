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
    prev_context: str = "",
) -> Task:
    prev_section = (
        "\nIMPORTANT — A previous package is provided as reference. "
        "Treat its silent_switches and installer_type as the baseline; "
        "only deviate if the new installer metadata clearly contradicts them.\n"
        f"{prev_context}\n"
        if prev_context else ""
    )
    return Task(
        description=(
            f"Installer: {installer_path} | App: {app_name} {app_version}\n"
            "1. Call get_installer_metadata (type, name, version, vendor, product_code).\n"
            "2. Call search_silent_switches for the correct silent flags.\n"
            "3. Call analyze_dependencies for prerequisites.\n"
            + prev_section +
            "Output compact JSON: app_name, app_version, app_vendor, installer_type, "
            "silent_switches, product_code, architecture, dependencies[], notes."
        ),
        expected_output=(
            "Compact JSON with app_name, app_version, app_vendor, installer_type, "
            "silent_switches, product_code, architecture, dependencies, notes."
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
        description=(
            f"Research: {research_output}\n"
            f"Template: {template_path} | Installer: {installer_path}\n"
            "1. Call read_psadt_template (confirm version, available functions).\n"
            "2. Call get_package_history for app_name from research.\n"
            "3. Call build_folder_structure passing spec_json as a plain JSON object (NOT a pre-encoded string).\n"
            "   Example: spec_json = {\"app_name\": \"...\", \"installer_path\": \"...\", ...}\n"
            "Output compact JSON: psadt_version, available_functions[], history_summary, "
            "package_dir, spec_json."
        ),
        expected_output=(
            "Compact JSON with psadt_version, available_functions, history_summary, "
            "package_dir, and spec_json."
        ),
        agent=agent,
    )


# ---------------------------------------------------------------------------
# Phase 3 — Scripting
# ---------------------------------------------------------------------------

def make_scripting_task(
    agent: Agent,
    architecture_output: str,  # JSON string from Phase 2
    prev_context: str = "",
) -> Task:
    prev_section = (
        "\nIMPORTANT — Match the structure of the previous script provided below. "
        "Reuse its pre-install commands, post-install commands, silent switches, "
        "and uninstall product code unless the new architecture data overrides them.\n"
        f"{prev_context}\n"
        if prev_context else ""
    )
    return Task(
        description=(
            f"Architecture: {architecture_output}\n"
            "1. Extract spec_json from the architecture output above.\n"
            "2. Call generate_deploy_script passing spec_json as a plain JSON object (NOT a pre-encoded string).\n"
            "   Example: spec_json = {\"app_name\": \"...\", \"app_version\": \"...\", ...}\n"
            + prev_section +
            "Script must: use PSADT v4 cmdlets (Start-ADTMsiProcess/Start-ADTProcess/Get-ADTApplication), "
            "implement Install-ADTDeployment/Uninstall-ADTDeployment/Repair-ADTDeployment functions, "
            "silently remove prior versions via Get-ADTApplication, no reboots, no interactive dialogs.\n"
            "Output compact JSON: script_path, package_dir, installer_type, silent_switches_used, "
            "cleanup_strategy, script_preview (first 200 chars)."
        ),
        expected_output=(
            "Compact JSON with script_path, package_dir, installer_type, "
            "silent_switches_used, cleanup_strategy, script_preview."
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
    # Pass only the essential fields to keep context small
    try:
        import json as _json
        s = _json.loads(scripting_output)
        script_ctx = _json.dumps({k: s[k] for k in ("package_dir", "script_path", "installer_type") if k in s})
        r = _json.loads(research_output)
        research_ctx = _json.dumps({k: r[k] for k in ("app_name", "product_code") if k in r})
    except Exception:
        script_ctx = scripting_output
        research_ctx = research_output

    return Task(
        description=(
            f"Script: {script_ctx} | Research: {research_ctx} | Mode: {test_mode}\n\n"

            "## Steps\n"
            f"1. Call execute_install_test (package_dir, deployment_type=Install, test_mode={test_mode}).\n"
            "   Save the EXACT integer in the tool's exit_code field as install_exit_code — do not alter it.\n"
            "2. Call parse_psadt_logs with app_name.\n"
            "   If it returns a Permission Denied error, record log_analysis as:\n"
            "   'Log read failed — elevation required. Same root cause as install failure.'\n"
            "3. Call verify_wmi_installation with app_name.\n"
            "   It checks Win32_Product, Get-Package, AND registry.\n"
            "   An EXE/NSIS app showing source: registry or source: Get-Package is NORMAL — still PASS-eligible.\n"

            "## Pass/Fail Decision\n"
            "- PASS: install_exit_code is 0 or 3010 AND verify_wmi_installation.installed == true.\n"
            "- FAIL: any other exit code, including 1. No exceptions.\n"

            "## Elevation Error (exit_code 1)\n"
            "If stderr from execute_install_test contains any of:\n"
            "  'requires administrative permissions' | 'not an Administrator' | 'PowerShell is not elevated'\n"
            "Then set failure_diagnosis to EXACTLY:\n"
            "  'Process launched without elevation. Re-run execute_install_test with an elevated/admin context. "
            "In host mode, ensure the calling process is already running as Administrator or use -Verb RunAs.'\n"
            "Do NOT call this 1603.\n"

            "## Cleanup\n"
            "5. If overall_result is PASS, call cleanup_test_installation and record its result.\n"
            "   If FAIL, set cleanup to null — do not attempt uninstall.\n"

            "## Output\n"
            "Return ONLY this compact JSON — no extra keys, no markdown wrapping:\n"
            '{"overall_result":"PASS or FAIL",'
            '"install_exit_code":<integer verbatim from tool>,'
            '"exit_code_meaning":"<string verbatim from tool>",'
            '"log_analysis":"<what happened, including any read errors>",'
            '"validation":{"source":"<Win32_Product|Get-Package|registry|all_checked|none>",'
            '"installed":<true or false>},'
            '"cleanup":"<result string or null>",'
            '"failure_diagnosis":"<root cause + exact remediation step>"}'
        ),
        expected_output=(
            'Compact JSON with exactly these keys: overall_result ("PASS" or "FAIL"), '
            "install_exit_code (integer copied verbatim from execute_install_test output), "
            "exit_code_meaning (string from tool), log_analysis, "
            "validation (object with source and installed), cleanup, failure_diagnosis."
        ),
        agent=agent,
    )
