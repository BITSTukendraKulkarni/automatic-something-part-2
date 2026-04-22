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
        description=(
            f"Installer: {installer_path} | App: {app_name} {app_version}\n"
            "1. Call get_installer_metadata (type, name, version, vendor, product_code).\n"
            "2. Call search_silent_switches for the correct silent flags.\n"
            "3. Call analyze_dependencies for prerequisites.\n"
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
) -> Task:
    return Task(
        description=(
            f"Architecture: {architecture_output}\n"
            "1. Extract spec_json from the architecture output above.\n"
            "2. Call generate_deploy_script passing spec_json as a plain JSON object (NOT a pre-encoded string).\n"
            "   Example: spec_json = {\"app_name\": \"...\", \"app_version\": \"...\", ...}\n"
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
            f"Script: {script_ctx} | Research: {research_ctx} | Mode: {test_mode}\n"
            f"1. Call execute_install_test (package_dir, deployment_type=Install, test_mode={test_mode}).\n"
            "2. Call parse_psadt_logs with app_name.\n"
            "3. Call verify_wmi_installation with app_name — it checks Win32_Product, Get-Package, "
            "AND registry; an EXE/NSIS install will show 'source: registry' or 'source: Get-Package' "
            "instead of 'source: Win32_Product' — this is NORMAL and still counts as PASS.\n"
            "4. Mark overall_result PASS if: exit_code is 0 or 3010 AND "
            "verify_wmi_installation shows installed=true (any source).\n"
            "5. If overall_result is PASS, call cleanup_test_installation.\n"
            "Output compact JSON: overall_result, install_exit_code, exit_code_meaning, "
            "log_analysis, validation (source + installed), cleanup, failure_diagnosis."
        ),
        expected_output=(
            "Compact JSON with overall_result (PASS/FAIL), install_exit_code, "
            "log_analysis, validation, cleanup, failure_diagnosis."
        ),
        agent=agent,
    )
