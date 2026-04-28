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
from verbose_logger import VerboseLogger

# ---------------------------------------------------------------------------
# Patch litellm.completion with two behaviours layered together:
#   1. Auto-retry on RateLimitError (429), sleeping the exact wait Groq reports.
#   2. Verbose logging — capture the exact messages list sent to the LLM and
#      the full raw response returned, writing both to the active task log.
# ---------------------------------------------------------------------------
_original_completion = litellm.completion

@functools.wraps(_original_completion)
def _completion_with_verbose_logging(*args, **kwargs):
    max_attempts = 8

    # --- Capture prompt -------------------------------------------------------
    messages = kwargs.get("messages") or (args[1] if len(args) > 1 else None)
    model    = kwargs.get("model") or (args[0] if args else "?")
    vlog     = VerboseLogger.get_current()
    call_label = f"model={model}"
    if vlog and messages:
        vlog.llm_prompt(messages, call_label=call_label)

    # --- Call with retry on 429 -----------------------------------------------
    for attempt in range(max_attempts):
        try:
            response = _original_completion(*args, **kwargs)

            # --- Capture response ----------------------------------------------
            if vlog:
                vlog.llm_response(response, call_label=call_label)

            return response

        except litellm.RateLimitError as e:
            if attempt == max_attempts - 1:
                if vlog:
                    vlog.error(f"RateLimitError — max retries exhausted: {e}")
                raise
            msg   = str(e)
            match = re.search(r"try again in ([0-9.]+)s", msg)
            wait  = float(match.group(1)) + 2.0 if match else 30.0
            notice = f"[rate-limit] 429 hit — sleeping {wait:.1f}s (attempt {attempt + 1}/{max_attempts})"
            print(notice)
            if vlog:
                vlog.warning(notice)
            time.sleep(wait)

litellm.completion = _completion_with_verbose_logging
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
            "Produce a detailed test report with pass/fail status and actionable failure diagnosis.\n\n"

            "=== STRICT OUTPUT ACCURACY RULES ===\n"
            "1. NEVER hallucinate or infer exit codes. Copy install_exit_code VERBATIM from the "
            "execute_install_test tool output. If the tool returns exit_code: 1, report 1 — not 1603 "
            "or any other value. If a field is missing from tool output, set it to null.\n"

            "2. PASS/FAIL LOGIC: Mark PASS only if exit_code is 0 or 3010 AND "
            "verify_wmi_installation returns installed: true (any source). "
            "All other exit codes = FAIL, including exit_code 1.\n"

            "3. ELEVATION ERRORS (exit_code 1): If stderr contains 'requires administrative permissions' "
            "or 'not an Administrator' or 'PowerShell is not elevated', set failure_diagnosis to: "
            "'Process launched without elevation. Re-run execute_install_test with an elevated/admin context. "
            "In host mode, ensure the calling process is already running as Administrator or use -Verb RunAs.' "
            "Do NOT classify this as exit_code 1603.\n"

            "4. LOG PARSE FAILURES: If parse_psadt_logs returns a Permission Denied error, note it as: "
            "'Log read failed — elevation required. Same root cause as install failure.' "
            "Do not report overall_status SUCCESS if the log file could not be read.\n"

            "5. FINAL JSON must use exactly this schema (no extra fields):\n"
            "{ \"overall_result\": \"PASS\"|\"FAIL\", \"install_exit_code\": <integer verbatim>, "
            "\"exit_code_meaning\": \"<string verbatim>\", \"log_analysis\": \"<what happened>\", "
            "\"validation\": { \"source\": \"<Win32_Product|Get-Package|registry|all_checked|none>\", "
            "\"installed\": <true|false> }, "
            "\"cleanup\": \"<result string or null>\", "
            "\"failure_diagnosis\": \"<root cause + exact remediation step>\" }"
        ),
        backstory=(
            "You are a meticulous QA engineer who has debugged thousands of failed deployments. "
            "You never rely on a single validation signal — you check the log, the registry, "
            "AND WMI to confirm an app is truly installed. You always clean up after a successful test. "
            "Your reports are precise: you copy every exit code VERBATIM from tool output — you never "
            "guess, infer, or substitute a different code. If the tool says exit_code 1, you report 1. "
            "You know that exit_code 1 from a PSADT host-mode run almost always means the PowerShell "
            "process was not elevated, not a fatal MSI error. You pinpoint the root cause and give "
            "the exact remediation step, not a generic description."
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
