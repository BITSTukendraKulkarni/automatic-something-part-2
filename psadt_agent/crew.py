"""
CrewAI Orchestrator — PSADT Agentic AI
Manages the four-phase workflow with Human-in-the-Loop (HITL) gates between phases.

Phases:
  1. Research      → Researcher agent
  2. Architecture  → Architect agent
  3. Scripting     → Scripter agent
  4. QA Testing    → QA Tester agent

HITL gates pause execution between phases until the user approves or rejects.
Set HITL_BYPASS=true in .env to skip gates.
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from crewai import Crew, Process

from agents import make_researcher, make_architect, make_scripter, make_qa_tester
from tasks import make_research_task, make_architecture_task, make_scripting_task, make_qa_task
from utils import (
    hitl_request_approval,
    hitl_wait_for_approval,
    save_package_record,
    get_logger,
    sanitize_app_name,
    timestamp_slug,
)
from config import HITL_ENABLED, TEST_MODE, PSADT_TEMPLATE_PATH
from psadt_template import PackageSpec
from terminal_prompt import ask_terminal_permission

log = get_logger("psadt-crew")


# ---------------------------------------------------------------------------
# Progress callback type
# ---------------------------------------------------------------------------
# Signature: callback(phase: str, status: str, data: dict)
ProgressCallback = Callable[[str, str, dict], None]


# ---------------------------------------------------------------------------
# Main orchestrator class
# ---------------------------------------------------------------------------

class PSADTCrew:
    """
    Orchestrates the four-agent, four-phase PSADT package automation workflow.
    Supports HITL gating between every phase.
    """

    def __init__(
        self,
        installer_path: str,
        app_name: str,
        app_version: str,
        template_path: str = PSADT_TEMPLATE_PATH,
        test_mode: str = TEST_MODE,
        progress_callback: Optional[ProgressCallback] = None,
    ):
        self.installer_path = installer_path
        self.app_name = app_name
        self.app_version = app_version
        self.template_path = template_path
        self.test_mode = test_mode
        self.progress_callback = progress_callback or (lambda phase, status, data: None)

        # Phase outputs (populated as workflow runs)
        self.research_output: Optional[str] = None
        self.architecture_output: Optional[str] = None
        self.scripting_output: Optional[str] = None
        self.qa_output: Optional[str] = None

        # Control flags
        self._stop_requested = False
        self._current_phase = "idle"
        self._phase_results: dict = {}

        # Build agents once
        self.researcher  = make_researcher()
        self.architect   = make_architect()
        self.scripter    = make_scripter()
        self.qa_tester   = make_qa_tester()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        Execute the full workflow synchronously.
        Returns the final combined result dict.
        """
        log.info(f"=== PSADT Agentic AI — Starting workflow for '{self.app_name}' ===")
        log.info(f"HITL enabled: {HITL_ENABLED} | Test mode: {self.test_mode}")

        try:
            # ---- Phase 1: Research ----------------------------------------
            p1_desc = (f"Installer: {self.installer_path} | App: {self.app_name} {self.app_version}\n"
                       "1. Call get_installer_metadata. 2. Call search_silent_switches. 3. Call analyze_dependencies.\n"
                       "Output compact JSON: app_name, app_version, app_vendor, installer_type, silent_switches, architecture, dependencies, notes.")
            if not self._gate("Research", "Begin installer analysis and switch discovery?", task_description=p1_desc):
                return self._aborted("Research")

            self._set_phase("Research")
            research_result = self._run_phase_1()
            self._phase_results["research"] = research_result

            # ---- Phase 2: Architecture ------------------------------------
            context_str = json.dumps(research_result)[:300]
            p2_desc = (f"Research: {context_str}\nTemplate: {self.template_path} | Installer: {self.installer_path}\n"
                       "1. Call read_psadt_template. 2. Call get_package_history. 3. Call build_folder_structure.\n"
                       "Output compact JSON: psadt_version, package_dir, spec_json.")
            if not self._gate("Architecture", f"Research complete. Proceed to build folder structure?", task_description=p2_desc):
                return self._aborted("Architecture")

            self._set_phase("Architecture")
            time.sleep(20)
            arch_result = self._run_phase_2(research_result)
            self._phase_results["architecture"] = arch_result

            # ---- Phase 3: Scripting ---------------------------------------
            p3_desc = (f"Package dir: {arch_result.get('package_dir','?')}\n"
                       "1. Extract spec_json. 2. Call generate_deploy_script.\n"
                       "Output compact JSON: script_path, package_dir, installer_type, silent_switches_used, script_preview.")
            if not self._gate("Scripting", f"Architecture complete. Package dir: {arch_result.get('package_dir','?')}", task_description=p3_desc):
                return self._aborted("Scripting")

            self._set_phase("Scripting")
            time.sleep(20)
            script_result = self._run_phase_3(arch_result)
            self._phase_results["scripting"] = script_result

            # ---- Phase 4: QA Testing --------------------------------------
            p4_desc = (f"Script: {script_result.get('script_path','?')} | Mode: {self.test_mode}\n"
                       "1. execute_install_test. 2. parse_psadt_logs. 3. verify_registry. 4. verify_wmi. 5. cleanup.\n"
                       "Output compact JSON: overall_result, install_exit_code, log_analysis, validation, cleanup.")
            script_preview = script_result.get("script_preview", "")[:200]
            if not self._gate("QA Testing", f"Script at {script_result.get('script_path','?')}\n{script_preview}", task_description=p4_desc):
                return self._aborted("QA Testing")

            self._set_phase("QA Testing")
            time.sleep(20)
            qa_result = self._run_phase_4(script_result, research_result)
            self._phase_results["qa"] = qa_result

            # ---- Finalization ---------------------------------------------
            final = self._finalize(research_result, arch_result, script_result, qa_result)
            return final

        except Exception as e:
            log.exception(f"Workflow crashed: {e}")
            return {"success": False, "error": str(e), "phase": self._current_phase}

    def stop(self):
        """Request workflow stop (checked at next HITL gate)."""
        self._stop_requested = True

    # ------------------------------------------------------------------
    # Phase runners — each builds a single-agent Crew and kicks it off
    # ------------------------------------------------------------------

    def _run_phase_1(self) -> dict:
        task = make_research_task(
            agent=self.researcher,
            installer_path=self.installer_path,
            app_name=self.app_name,
            app_version=self.app_version,
        )
        crew = Crew(agents=[self.researcher], tasks=[task], process=Process.sequential, verbose=True)
        raw_output = crew.kickoff()
        result = self._parse_output(raw_output, "research")
        self.research_output = json.dumps(result)
        self._emit("Research", "completed", result)
        log.info(f"[Phase 1] Research complete: {result.get('app_name')} {result.get('app_version')}")
        return result

    def _run_phase_2(self, research_result: dict) -> dict:
        # Pass only the fields Architecture needs — avoids bloating the prompt
        arch_ctx = {k: research_result[k] for k in (
            "app_name", "app_version", "app_vendor",
            "installer_type", "silent_switches", "architecture",
        ) if k in research_result}
        task = make_architecture_task(
            agent=self.architect,
            research_output=json.dumps(arch_ctx),
            template_path=self.template_path,
            installer_path=self.installer_path,
        )
        crew = Crew(agents=[self.architect], tasks=[task], process=Process.sequential, verbose=True)
        raw_output = crew.kickoff()
        result = self._parse_output(raw_output, "architecture")
        self.architecture_output = json.dumps(result)
        self._emit("Architecture", "completed", result)
        log.info(f"[Phase 2] Architecture complete: {result.get('package_dir')}")
        return result

    def _run_phase_3(self, arch_result: dict) -> dict:
        script_ctx = {k: arch_result[k] for k in (
            "psadt_version", "package_dir", "spec_json",
        ) if k in arch_result}
        task = make_scripting_task(
            agent=self.scripter,
            architecture_output=json.dumps(script_ctx),
        )
        crew = Crew(agents=[self.scripter], tasks=[task], process=Process.sequential, verbose=True)
        raw_output = crew.kickoff()
        result = self._parse_output(raw_output, "scripting")
        self.scripting_output = json.dumps(result)
        self._emit("Scripting", "completed", result)
        log.info(f"[Phase 3] Scripting complete: {result.get('script_path')}")
        return result

    def _run_phase_4(self, script_result: dict, research_result: dict) -> dict:
        task = make_qa_task(
            agent=self.qa_tester,
            scripting_output=json.dumps({k: script_result[k] for k in (
                "package_dir", "script_path", "installer_type",
            ) if k in script_result}),
            research_output=json.dumps({k: research_result[k] for k in (
                "app_name", "product_code",
            ) if k in research_result}),
            test_mode=self.test_mode,
        )
        crew = Crew(agents=[self.qa_tester], tasks=[task], process=Process.sequential, verbose=True)
        raw_output = crew.kickoff()
        result = self._parse_output(raw_output, "qa")
        self.qa_output = json.dumps(result)
        self._emit("QA Testing", "completed", result)
        log.info(f"[Phase 4] QA Testing complete: {result.get('overall_result')}")
        return result

    # ------------------------------------------------------------------
    # HITL gate
    # ------------------------------------------------------------------

    def _gate(self, phase: str, context: str, task_description: str = "") -> bool:
        """
        Show terminal permission prompt, then (if HITL enabled) also register
        a Gradio approval request and block until decided.
        Returns True if approved, False if rejected.
        """
        if self._stop_requested:
            log.info(f"[HITL] Stop requested before phase: {phase}")
            return False

        # Always show terminal prompt first
        terminal_approved = ask_terminal_permission(
            phase=phase,
            context=context,
            task_description=task_description,
        )
        if not terminal_approved:
            log.info(f"[HITL] Rejected at terminal prompt — phase: {phase}")
            return False

        # If HITL bypass, terminal approval is sufficient
        if not HITL_ENABLED:
            log.info(f"[HITL] Bypass mode — terminal-approved phase: {phase}")
            self._emit(phase, "hitl_bypassed", {"phase": phase, "context": context})
            return True

        # Also wait for Gradio UI approval
        token_info = hitl_request_approval(phase, context)
        token = token_info["token"]
        self._emit(phase, "hitl_pending", {"phase": phase, "context": context, "token": token})
        log.info(f"[HITL] Waiting for Gradio approval — phase={phase}, token={token}")

        approved = hitl_wait_for_approval(token, timeout=600.0)
        self._emit(phase, "hitl_decided", {"phase": phase, "token": token, "approved": approved})
        return approved

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------

    def _finalize(
        self,
        research: dict,
        arch: dict,
        script: dict,
        qa: dict,
    ) -> dict:
        # Normalise — LLM may return "pass", "PASS", "Installation PASS", etc.
        # Also treat exit code 0 or 3010 (soft reboot) as PASS.
        raw_result = str(qa.get("overall_result", "")).upper()
        exit_code = qa.get("install_exit_code") or qa.get("exit_code")
        try:
            exit_ok = int(exit_code) in (0, 3010)
        except (TypeError, ValueError):
            exit_ok = False
        overall = "PASS" if ("PASS" in raw_result or exit_ok) else "FAIL"

        # Normalise cleanup — LLM may return flat keys or nested dict
        cleanup_data = qa.get("cleanup") or {}
        cleanup_done = (
            cleanup_data.get("completed")
            or cleanup_data.get("success")
            or cleanup_data.get("cleanup_exit_code") == 0
        ) if isinstance(cleanup_data, dict) else False

        record = {
            "app_name": research.get("app_name", self.app_name),
            "app_version": research.get("app_version", self.app_version),
            "installer_type": research.get("installer_type"),
            "silent_switches": research.get("silent_switches"),
            "package_dir": arch.get("package_dir"),
            "script_path": script.get("script_path"),
            "psadt_version": arch.get("psadt_version"),
            "qa_result": overall,
            "qa_exit_code": qa.get("install_exit_code") or qa.get("exit_code"),
            "validation": qa.get("validation", {}),
            "cleanup_completed": bool(cleanup_done),
        }

        save_package_record(research.get("app_name", self.app_name), record)
        self._emit("Finalization", "completed", {"overall": overall, "record": record})
        log.info(f"=== Workflow complete — {overall} ===")

        return {
            "success": overall == "PASS",
            "overall_result": overall,
            "research": research,
            "architecture": arch,
            "scripting": script,
            "qa": qa,
            "package_record": record,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_phase(self, phase: str):
        self._current_phase = phase
        self._emit(phase, "started", {"phase": phase})
        log.info(f">>> Entering phase: {phase}")

    def _emit(self, phase: str, status: str, data: dict):
        try:
            self.progress_callback(phase, status, data)
        except Exception:
            pass  # never crash the workflow due to UI callback errors

    def _aborted(self, phase: str) -> dict:
        msg = f"Workflow aborted at phase: {phase} (HITL rejected or stop requested)"
        log.warning(msg)
        self._emit(phase, "aborted", {"phase": phase, "reason": msg})
        return {"success": False, "aborted": True, "phase": phase, "message": msg}

    @staticmethod
    def _parse_output(raw_output, phase_name: str) -> dict:
        """
        Try to parse JSON from crew output. If the LLM wrapped it in markdown,
        strip the code fence first.
        """
        if hasattr(raw_output, "raw"):
            text = raw_output.raw
        elif isinstance(raw_output, str):
            text = raw_output
        else:
            text = str(raw_output)

        # Strip markdown code fences
        import re
        text = re.sub(r"^```[a-z]*\n?", "", text.strip(), flags=re.MULTILINE)
        text = re.sub(r"\n?```$", "", text.strip(), flags=re.MULTILINE)
        text = text.strip()

        # Find first JSON object/array
        match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Fallback: return raw text wrapped
        log.warning(f"[{phase_name}] Could not parse JSON from output, returning raw")
        return {"raw_output": text[:3000], "parse_error": True}


# ---------------------------------------------------------------------------
# Background runner (for Gradio non-blocking execution)
# ---------------------------------------------------------------------------

class PSADTCrewRunner:
    """
    Wraps PSADTCrew to run in a background thread so the Gradio UI stays responsive.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._crew: Optional[PSADTCrew] = None
        self.result: Optional[dict] = None
        self.is_running = False
        self.log_lines: list[str] = []

    def start(
        self,
        installer_path: str,
        app_name: str,
        app_version: str,
        template_path: str,
        test_mode: str,
    ) -> str:
        if self.is_running:
            return "A workflow is already running."

        self.result = None
        self.log_lines = []
        self.is_running = True

        def _progress(phase, status, data):
            msg = f"[{datetime.now().strftime('%H:%M:%S')}] [{phase}] {status}: {json.dumps(data)[:200]}"
            self.log_lines.append(msg)

        self._crew = PSADTCrew(
            installer_path=installer_path,
            app_name=app_name,
            app_version=app_version,
            template_path=template_path,
            test_mode=test_mode,
            progress_callback=_progress,
        )

        def _run():
            try:
                self.result = self._crew.run()
            except Exception as e:
                self.result = {"success": False, "error": str(e)}
            finally:
                self.is_running = False

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        return "Workflow started."

    def stop(self):
        if self._crew:
            self._crew.stop()
        return "Stop requested."

    def get_log(self) -> str:
        return "\n".join(self.log_lines[-100:])  # last 100 lines

    def get_pending_approvals(self) -> list[dict]:
        from utils import hitl_get_pending
        return hitl_get_pending()

    def approve(self, token: str) -> str:
        from utils import hitl_set_decision
        hitl_set_decision(token, True)
        return f"Approved: {token}"

    def reject(self, token: str) -> str:
        from utils import hitl_set_decision
        hitl_set_decision(token, False)
        return f"Rejected: {token}"
