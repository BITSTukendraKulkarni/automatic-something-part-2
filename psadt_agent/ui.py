"""
Gradio UI for the PSADT Agentic AI system.

Panels:
  1. Package Builder     — input form + workflow trigger
  2. Live Log            — real-time workflow output
  3. HITL Approvals      — approve / reject phase gates
  4. Package History     — browse previous packages for any app
  5. Test Report         — structured QA results from the last run
  6. System Status       — MCP health, environment info
"""

import json
import time
import threading
from pathlib import Path

import gradio as gr

from crew import PSADTCrewRunner
from utils import (
    hitl_get_pending,
    hitl_set_decision,
    get_package_history,
    list_all_packaged_apps,
    explain_exit_code,
    get_logger,
)
from config import (
    PSADT_TEMPLATE_PATH,
    TEST_MODE,
    HITL_ENABLED,
    PACKAGES_DIR,
    PSADT_VERSION,
    GROQ_MODEL,
)
import mcp_server as mcp

log = get_logger("psadt-ui")

# Single global runner (one workflow at a time)
runner = PSADTCrewRunner()


# ---------------------------------------------------------------------------
# Helper renderers
# ---------------------------------------------------------------------------

def _render_qa_report(report: dict) -> str:
    if not report:
        return "No test report available yet."

    overall = report.get("overall_result", "UNKNOWN")
    icon = "✅ PASS" if overall == "PASS" else "❌ FAIL"

    lines = [
        f"## {icon}",
        "",
        f"**Install Exit Code:** `{report.get('install_exit_code', 'N/A')}` — {report.get('exit_code_meaning', '')}",
        "",
        "### Log Analysis",
    ]
    log_a = report.get("log_analysis", {})
    lines.append(f"- Final Status: `{log_a.get('final_status', 'N/A')}`")
    for err in log_a.get("error_lines", [])[:5]:
        lines.append(f"  - ⚠️ `{err}`")

    lines += ["", "### Validation"]
    val = report.get("validation", {})
    for check, v in val.items():
        status = "✅" if v.get("pass") else "❌"
        lines.append(f"- {status} **{check.title()}**: {v.get('details', '')}")

    lines += ["", "### Post-Test Cleanup"]
    cleanup = report.get("cleanup", {})
    if cleanup.get("completed"):
        lines.append("✅ Cleanup completed — system restored to original state.")
    else:
        lines.append("❌ Cleanup incomplete or not attempted.")
        if cleanup.get("still_installed"):
            lines.append("  > ⚠️ Application may still be installed on this system.")

    if report.get("failure_diagnosis"):
        lines += ["", "### Failure Diagnosis", f"> {report['failure_diagnosis']}"]

    if report.get("recommendations"):
        lines += ["", "### Recommendations"]
        for rec in report["recommendations"]:
            lines.append(f"- {rec}")

    return "\n".join(lines)


def _render_history(app_name: str) -> str:
    if not app_name.strip():
        all_apps = list_all_packaged_apps()
        if not all_apps:
            return "No packages in history yet."
        return "**Packaged apps:**\n" + "\n".join(f"- {a}" for a in all_apps)

    records = get_package_history(app_name.strip())
    if not records:
        return f"No history found for '{app_name}'."

    lines = [f"## Package History — {app_name}", f"*{len(records)} record(s), newest first*", ""]
    for i, r in enumerate(records[:10], 1):
        result_icon = "✅" if r.get("qa_result") == "PASS" else "❌"
        lines += [
            f"### {i}. v{r.get('app_version', '?')} — {r.get('timestamp', 'N/A')[:19]} {result_icon}",
            f"- Installer type: `{r.get('installer_type', 'N/A')}`",
            f"- Silent switches: `{r.get('silent_switches', 'N/A')}`",
            f"- PSADT version: `{r.get('psadt_version', 'N/A')}`",
            f"- Package dir: `{r.get('package_dir', 'N/A')}`",
            f"- Script: `{r.get('script_path', 'N/A')}`",
            f"- QA result: `{r.get('qa_result', 'N/A')}`",
            "",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# UI action handlers
# ---------------------------------------------------------------------------

def start_workflow(
    installer_path: str,
    app_name: str,
    app_version: str,
    template_path: str,
    test_mode: str,
) -> str:
    """Start the CrewAI workflow. Returns a status message."""
    if not installer_path.strip():
        return "❌ Please provide the installer path."
    if not app_name.strip():
        return "❌ Please provide the application name."
    if not Path(installer_path).exists():
        return f"❌ Installer not found: {installer_path}"
    if not Path(template_path).exists():
        return f"❌ PSADT template not found: {template_path}"

    msg = runner.start(
        installer_path=installer_path.strip(),
        app_name=app_name.strip(),
        app_version=app_version.strip() or "1.0",
        template_path=template_path.strip(),
        test_mode=test_mode,
    )
    return f"🚀 {msg}"


def stop_workflow() -> str:
    return runner.stop()


def refresh_log() -> str:
    return runner.get_log() or "(no output yet)"


def get_pending_approvals_ui() -> tuple[str, list]:
    """Return formatted pending approvals and token list for dropdowns."""
    pending = hitl_get_pending()
    if not pending:
        return "No approvals pending.", []
    lines = []
    tokens = []
    for p in pending:
        lines.append(f"**[{p['phase']}]** `{p['token']}`\n> {p['context'][:300]}")
        tokens.append(p["token"])
    return "\n\n---\n\n".join(lines), tokens


def approve_phase(token: str) -> str:
    if not token:
        return "Select a token first."
    hitl_set_decision(token, True)
    return f"✅ Approved: `{token}`"


def reject_phase(token: str) -> str:
    if not token:
        return "Select a token first."
    hitl_set_decision(token, False)
    return f"❌ Rejected: `{token}`"


def get_test_report_ui() -> str:
    if not runner.result:
        return "No test report available. Run the workflow first."
    qa = runner.result.get("qa", {})
    return _render_qa_report(qa)


def get_full_result_json() -> str:
    if not runner.result:
        return "No result yet."
    return json.dumps(runner.result, indent=2, default=str)


def load_history_ui(app_name: str) -> str:
    return _render_history(app_name)


def get_system_status() -> str:
    sys_info = mcp.get_system_info()
    sandbox = mcp.check_sandbox_available()
    lines = [
        "## System Status",
        "",
        f"- **OS:** {sys_info.get('OS', 'N/A')} {sys_info.get('OSVersion', '')}",
        f"- **Architecture:** {sys_info.get('Architecture', 'N/A')}",
        f"- **PowerShell:** {sys_info.get('PSVersion', 'N/A')}",
        f"- **Free Disk (C:):** {sys_info.get('FreeDiskGB', 'N/A')} GB",
        f"- **Windows Sandbox:** {'✅ Available (' + sandbox.get('State','?') + ')' if sandbox.get('Available') else '❌ Not available'}",
        "",
        "## Configuration",
        "",
        f"- **HITL Enabled:** {'✅ Yes' if HITL_ENABLED else '⚠️ Bypassed'}",
        f"- **Test Mode:** `{TEST_MODE}`",
        f"- **PSADT Version:** `{PSADT_VERSION}`",
        f"- **Groq Model:** `{GROQ_MODEL}`",
        f"- **Template Path:** `{PSADT_TEMPLATE_PATH}`",
        f"- **Packages Dir:** `{PACKAGES_DIR}`",
    ]
    return "\n".join(lines)


def inspect_installer(installer_path: str) -> str:
    if not installer_path.strip():
        return "Enter an installer path first."
    result = mcp.get_installer_metadata(installer_path.strip())
    return json.dumps(result, indent=2)


def search_registry_ui(app_fragment: str) -> str:
    if not app_fragment.strip():
        return "Enter an app name fragment."
    result = mcp.find_installed_app_registry(app_fragment.strip())
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Gradio layout
# ---------------------------------------------------------------------------

def build_ui() -> gr.Blocks:
    theme = gr.themes.Soft(
        primary_hue="indigo",
        secondary_hue="slate",
        neutral_hue="gray",
        font=[gr.themes.GoogleFont("Inter"), "sans-serif"],
    )

    with gr.Blocks(title="PSADT Agentic AI", theme=theme) as app:

        gr.Markdown(
            """
# 🤖 PSADT Agentic AI
### Automated PowerShell App Deployment Toolkit Package Builder
Powered by **CrewAI** · **Groq LLM** · **FastMCP** · **PSADT**
---
"""
        )

        with gr.Tabs():

            # ----------------------------------------------------------------
            # Tab 1 — Package Builder
            # ----------------------------------------------------------------
            with gr.Tab("📦 Package Builder"):
                gr.Markdown("### Configure and launch the packaging workflow")

                with gr.Row():
                    with gr.Column(scale=2):
                        installer_input = gr.Textbox(
                            label="Installer Path",
                            placeholder=r"C:\Downloads\MyApp_Setup_v2.1.exe",
                            info="Full path to the EXE, MSI, or MSIX installer",
                        )
                        app_name_input = gr.Textbox(
                            label="Application Name",
                            placeholder="Google Chrome",
                        )
                        app_version_input = gr.Textbox(
                            label="Application Version",
                            placeholder="125.0.0.0",
                            value="",
                        )
                        template_input = gr.Textbox(
                            label="PSADT Template Path",
                            value=PSADT_TEMPLATE_PATH,
                            info="Path to your PSADT toolkit template directory",
                        )
                        test_mode_input = gr.Radio(
                            choices=["host", "sandbox"],
                            value=TEST_MODE,
                            label="Test Mode",
                            info="'host' tests on this machine. 'sandbox' uses Windows Sandbox (requires feature enabled).",
                        )

                    with gr.Column(scale=1):
                        gr.Markdown(
                            """
### Workflow Phases
1. 🔍 **Research** — switches & dependencies
2. 🏗️ **Architecture** — folder structure
3. ✍️ **Scripting** — Deploy-Application.ps1
4. 🧪 **QA Testing** — execute & validate

### HITL Gates
Each phase transition **pauses for your approval** unless HITL bypass is enabled.
Go to the **Approvals** tab to approve each gate.
"""
                        )
                        inspect_btn = gr.Button("🔍 Inspect Installer", variant="secondary", size="sm")
                        installer_meta_out = gr.Code(
                            label="Installer Metadata", language="json", lines=10, visible=True
                        )

                with gr.Row():
                    start_btn = gr.Button("🚀 Start Workflow", variant="primary", size="lg")
                    stop_btn  = gr.Button("⏹ Stop", variant="stop", size="lg")

                workflow_status = gr.Markdown("_Ready. Configure inputs and click Start._")

                # Quick installer inspect
                inspect_btn.click(
                    fn=inspect_installer,
                    inputs=[installer_input],
                    outputs=[installer_meta_out],
                )
                start_btn.click(
                    fn=start_workflow,
                    inputs=[installer_input, app_name_input, app_version_input, template_input, test_mode_input],
                    outputs=[workflow_status],
                )
                stop_btn.click(fn=stop_workflow, outputs=[workflow_status])

            # ----------------------------------------------------------------
            # Tab 2 — Live Log
            # ----------------------------------------------------------------
            with gr.Tab("📋 Live Log"):
                gr.Markdown("### Real-time workflow output")
                log_output = gr.Textbox(
                    label="Workflow Log",
                    lines=30,
                    max_lines=200,
                    autoscroll=True,
                    interactive=False,
                    placeholder="Workflow output will appear here...",
                )
                with gr.Row():
                    refresh_btn = gr.Button("🔄 Refresh", variant="secondary")
                    auto_refresh = gr.Checkbox(label="Auto-refresh every 3s", value=False)

                refresh_btn.click(fn=refresh_log, outputs=[log_output])

                # Gradio 5: gr.Timer takes active= not value=, and toggle via .change on active attr
                try:
                    timer = gr.Timer(value=3, active=False)
                    timer.tick(fn=refresh_log, outputs=[log_output])
                    auto_refresh.change(
                        fn=lambda v: gr.Timer(value=3, active=v),
                        inputs=[auto_refresh],
                        outputs=[timer],
                    )
                except Exception:
                    pass

            # ----------------------------------------------------------------
            # Tab 3 — HITL Approvals
            # ----------------------------------------------------------------
            with gr.Tab("✅ Approvals (HITL)"):
                gr.Markdown(
                    f"""
### Human-in-the-Loop Phase Gates
HITL is currently: **{'ENABLED ✅' if HITL_ENABLED else 'BYPASSED ⚠️'}**

When the workflow reaches a phase gate, it pauses here. Review the context and approve or reject.
Rejecting a gate will abort the workflow at that phase.
"""
                )
                pending_display = gr.Markdown("_Click Refresh to check for pending approvals._")
                token_dropdown = gr.Dropdown(
                    label="Select Approval Token",
                    choices=[],
                    interactive=True,
                )
                with gr.Row():
                    refresh_approvals_btn = gr.Button("🔄 Refresh Approvals", variant="secondary")
                    approve_btn = gr.Button("✅ Approve", variant="primary")
                    reject_btn  = gr.Button("❌ Reject",  variant="stop")
                approval_result = gr.Markdown("")

                def refresh_approvals_fn():
                    text, tokens = get_pending_approvals_ui()
                    return text, gr.Dropdown(choices=tokens, value=tokens[0] if tokens else None)

                refresh_approvals_btn.click(
                    fn=refresh_approvals_fn,
                    outputs=[pending_display, token_dropdown],
                )
                approve_btn.click(fn=approve_phase, inputs=[token_dropdown], outputs=[approval_result])
                reject_btn.click(fn=reject_phase,   inputs=[token_dropdown], outputs=[approval_result])

            # ----------------------------------------------------------------
            # Tab 4 — Test Report
            # ----------------------------------------------------------------
            with gr.Tab("🧪 Test Report"):
                gr.Markdown("### QA Testing Results")
                report_display = gr.Markdown("_Run the workflow first to see the test report._")
                with gr.Row():
                    refresh_report_btn = gr.Button("🔄 Refresh Report")
                    show_raw_btn       = gr.Button("{ } Show Raw JSON")
                raw_json_out = gr.Code(label="Full Result JSON", language="json", visible=False, lines=40)

                refresh_report_btn.click(fn=get_test_report_ui, outputs=[report_display])
                show_raw_btn.click(
                    fn=lambda: (get_full_result_json(), gr.Code(visible=True)),
                    outputs=[raw_json_out, raw_json_out],
                )

            # ----------------------------------------------------------------
            # Tab 5 — Package History
            # ----------------------------------------------------------------
            with gr.Tab("📚 Package History"):
                gr.Markdown("### Browse previously built packages")
                with gr.Row():
                    history_app_input = gr.Textbox(
                        label="Application Name (leave blank to list all)",
                        placeholder="Google Chrome",
                    )
                    history_btn = gr.Button("🔍 Look Up History", variant="secondary")
                history_output = gr.Markdown("_Enter an app name and click Look Up History._")
                history_btn.click(fn=load_history_ui, inputs=[history_app_input], outputs=[history_output])

            # ----------------------------------------------------------------
            # Tab 6 — Registry Inspector
            # ----------------------------------------------------------------
            with gr.Tab("🔎 Registry Inspector"):
                gr.Markdown("### Search installed applications in the Windows Registry")
                with gr.Row():
                    reg_query = gr.Textbox(label="App Name Fragment", placeholder="Chrome")
                    reg_btn   = gr.Button("Search Registry", variant="secondary")
                reg_output = gr.Code(label="Registry Matches", language="json", lines=20)
                reg_btn.click(fn=search_registry_ui, inputs=[reg_query], outputs=[reg_output])

            # ----------------------------------------------------------------
            # Tab 7 — System Status
            # ----------------------------------------------------------------
            with gr.Tab("⚙️ System Status"):
                gr.Markdown("### Environment & Configuration")
                status_display = gr.Markdown("_Click Refresh to load system status._")
                refresh_status_btn = gr.Button("🔄 Refresh System Status", variant="secondary")
                refresh_status_btn.click(fn=get_system_status, outputs=[status_display])

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7860
    ui = build_ui()
    ui.launch(
        server_name="0.0.0.0",
        server_port=port,
        share=False,
        show_error=True,
    )
