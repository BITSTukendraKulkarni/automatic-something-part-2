"""
CrewAI Agent definitions for the PSADT Agentic AI system.

Agents:
  1. The Researcher    — silent switches, dependencies, installer analysis
  2. The Architect     — folder structure, template reading, history review
  3. The Scripter      — Deploy-Application.ps1 generation
  4. The QA Tester     — execution, log analysis, system verification, cleanup
"""

import os
import re
import time
import functools
import litellm
from crewai import Agent, LLM

from config import GROQ_API_KEY, GROQ_MODEL, GROQ_MAX_TOKENS

# ---------------------------------------------------------------------------
# Patch litellm.completion to auto-retry on RateLimitError.
# litellm's built-in max_retries does NOT cover 429s — it only covers
# network errors. This wrapper reads the "try again in Xs" from Groq's
# error message and sleeps exactly that long before retrying.
# ---------------------------------------------------------------------------
_original_completion = litellm.completion

@functools.wraps(_original_completion)
def _completion_with_rate_limit_retry(*args, **kwargs):
    max_attempts = 8
    for attempt in range(max_attempts):
        try:
            return _original_completion(*args, **kwargs)
        except litellm.RateLimitError as e:
            if attempt == max_attempts - 1:
                raise
            msg = str(e)
            match = re.search(r"try again in ([0-9.]+)s", msg)
            wait = float(match.group(1)) + 2.0 if match else 30.0
            print(f"[rate-limit] 429 hit — sleeping {wait:.1f}s (attempt {attempt + 1}/{max_attempts})")
            time.sleep(wait)

litellm.completion = _completion_with_rate_limit_retry
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
def _make_llm() -> LLM:
    if not GROQ_API_KEY:
        raise ValueError(
            "GROQ_API_KEY is not set. "
            "Copy .env.example to .env and add your key from https://console.groq.com"
        )
    os.environ["GROQ_API_KEY"] = GROQ_API_KEY
    return LLM(
        model=f"groq/{GROQ_MODEL}",
        api_key=GROQ_API_KEY,
        max_tokens=GROQ_MAX_TOKENS,
        temperature=0.0,
        max_retries=6,
        timeout=120,
    )


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
        max_iter=4,
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
            "You enforce PSADT v4 conventions strictly: correct folder names, PSAppDeployToolkit module import, "
            "proper Open-ADTSession initialization. You review history to detect regressions and reuse proven patterns."
        ),
        tools=[
            read_psadt_template_tool,
            build_folder_structure_tool,
            get_package_history_tool,
        ],
        llm=_make_llm(),
        verbose=True,
        allow_delegation=False,
        max_iter=4,
    )


# ---------------------------------------------------------------------------
# Agent 3 — The Scripter
# ---------------------------------------------------------------------------
def make_scripter() -> Agent:
    return Agent(
        role="PSADT Script Engineer",
        goal=(
            "Generate a complete, production-ready Invoke-AppDeployToolkit.ps1 (PSADT v4) that: "
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
        max_iter=5,
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
        max_iter=5,
    )
