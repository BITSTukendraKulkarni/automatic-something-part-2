"""
CrewAI Agent definitions for the PSADT Agentic AI system.

Agents:
  1. The Researcher    — silent switches, dependencies, installer analysis
  2. The Architect     — folder structure, template reading, history review
  3. The Scripter      — Deploy-Application.ps1 generation
  4. The QA Tester     — execution, log analysis, system verification, cleanup
"""

import os
from crewai import Agent

from config import GROQ_API_KEY, GROQ_MODEL
from tools import (
    get_installer_metadata_tool,
    search_silent_switches_tool,
    analyze_dependencies_tool,
    read_psadt_template_tool,
    build_folder_structure_tool,
    get_package_history_tool,
    generate_script_tool,
    execute_install_test_tool,
    parse_logs_tool,
    verify_registry_tool,
    verify_file_tool,
    verify_wmi_tool,
    cleanup_test_install_tool,
    run_powershell_tool,
)

# ---------------------------------------------------------------------------
# Shared LLM
# ---------------------------------------------------------------------------
def _make_llm() -> str:
    os.environ.setdefault("GROQ_API_KEY", GROQ_API_KEY)
    return f"groq/{GROQ_MODEL}"


# ---------------------------------------------------------------------------
# Agent 1 — The Researcher
# ---------------------------------------------------------------------------
def make_researcher() -> Agent:
    return Agent(
        role="Software Package Researcher",
        goal=(
            "Identify the correct silent installation switches for any EXE, MSI, or MSIX installer. "
            "Discover all dependencies that must be present before installation. "
            "Provide accurate metadata: app name, version, vendor, and installer type."
        ),
        backstory=(
            "You are a veteran Windows packaging engineer who has reverse-engineered thousands of "
            "installers. You know every common silent switch scheme (NSIS /S, InnoSetup /VERYSILENT, "
            "InstallShield /s, WiX /quiet). You cross-reference installer metadata, file headers, "
            "and web documentation. You never guess — you verify."
        ),
        tools=[
            get_installer_metadata_tool,
            search_silent_switches_tool,
            analyze_dependencies_tool,
        ],
        llm=_make_llm(),
        verbose=True,
        allow_delegation=False,
        max_iter=8,
    )


# ---------------------------------------------------------------------------
# Agent 2 — The Architect
# ---------------------------------------------------------------------------
def make_architect() -> Agent:
    return Agent(
        role="PSADT Package Architect",
        goal=(
            "Create the correct PSADT folder structure from a user-provided template. "
            "Read and validate the template to determine the PSADT version and available cmdlets. "
            "Review historical packages for the same application to ensure consistency and flag version upgrades."
        ),
        backstory=(
            "You have architected hundreds of enterprise Windows deployment packages. "
            "You enforce PSADT conventions strictly: correct folder names, correct toolkit version imports, "
            "proper AppDeployToolkitMain.ps1 initialization. You review history to detect regressions "
            "and to reuse proven patterns."
        ),
        tools=[
            read_psadt_template_tool,
            build_folder_structure_tool,
            get_package_history_tool,
        ],
        llm=_make_llm(),
        verbose=True,
        allow_delegation=False,
        max_iter=6,
    )


# ---------------------------------------------------------------------------
# Agent 3 — The Scripter
# ---------------------------------------------------------------------------
def make_scripter() -> Agent:
    return Agent(
        role="PSADT Script Engineer",
        goal=(
            "Generate a complete, production-ready Deploy-Application.ps1 that: "
            "runs silently for all users, suppresses all reboots, "
            "cleans up any previous versions before installing, "
            "and implements separate Pre-Installation, Installation, and Post-Installation phases. "
            "The script must strictly follow the PSADT version detected by The Architect."
        ),
        backstory=(
            "You are a PowerShell scripting expert who lives and breathes the PSADT documentation. "
            "You write clean, idiomatic PSADT scripts. Every script you produce handles edge cases: "
            "running processes blocking the installer, WMI queries for existing versions, "
            "exit code mapping, and graceful failure logging. "
            "You never introduce reboots or interactive prompts in silent deployments."
        ),
        tools=[
            generate_script_tool,
            read_psadt_template_tool,
        ],
        llm=_make_llm(),
        verbose=True,
        allow_delegation=False,
        max_iter=10,
    )


# ---------------------------------------------------------------------------
# Agent 4 — The QA Tester
# ---------------------------------------------------------------------------
def make_qa_tester() -> Agent:
    return Agent(
        role="PSADT QA & Validation Engineer",
        goal=(
            "Execute the generated PSADT package on the host or in Windows Sandbox. "
            "Parse C:\\Windows\\Logs\\Software for success/failure and exact exit codes. "
            "Validate installation via Registry, WMI, and Get-Package. "
            "If the test passes, automatically uninstall the app and verify clean removal. "
            "Produce a detailed test report with pass/fail status and actionable failure diagnosis."
        ),
        backstory=(
            "You are a meticulous QA engineer who has debugged thousands of failed deployments. "
            "You never rely on a single validation signal — you check the log, the registry, "
            "AND WMI to confirm an app is truly installed. You always clean up after a successful test. "
            "Your reports are precise: you map every exit code to a human-readable explanation "
            "and pinpoint exactly where in the deployment chain the failure occurred."
        ),
        tools=[
            execute_install_test_tool,
            parse_logs_tool,
            verify_registry_tool,
            verify_file_tool,
            verify_wmi_tool,
            cleanup_test_install_tool,
            run_powershell_tool,
        ],
        llm=_make_llm(),
        verbose=True,
        allow_delegation=False,
        max_iter=12,
    )
