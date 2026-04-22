"""
Terminal permission prompts for PSADT Agent workflow phases.
Shown before each phase so the user can approve, reject, or skip.
Token estimates are computed from real task description + tool schemas.
"""

import json
from typing import Optional

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import box as rich_box
    _RICH = True
except ImportError:
    _RICH = False

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
    def _count_tokens(text: str) -> int:
        return len(_enc.encode(text))
except Exception:
    def _count_tokens(text: str) -> int:
        return len(text) // 4  # rough fallback

_console = Console() if _RICH else None

# Tokens for the CrewAI system prompt injected into every call
_SYSTEM_PROMPT_TOKENS = 350

# Max output tokens per call (from GROQ_MAX_TOKENS in .env)
_MAX_OUTPUT_TOKENS = 1500

PHASE_AGENTS: dict[str, str] = {
    "Research":     "Software Package Researcher",
    "Architecture": "PSADT Package Architect",
    "Scripting":    "PSADT Script Engineer",
    "QA Testing":   "PSADT QA & Validation Engineer",
}

PHASE_MAX_ITER: dict[str, int] = {
    "Research": 4, "Architecture": 4, "Scripting": 5, "QA Testing": 5,
}

PHASE_TOOL_NAMES: dict[str, list[str]] = {
    "Research":     ["get_installer_metadata", "search_silent_switches", "analyze_dependencies"],
    "Architecture": ["read_psadt_template", "get_package_history", "build_folder_structure"],
    "Scripting":    ["generate_deploy_script", "read_psadt_template"],
    "QA Testing":   ["execute_install_test", "parse_psadt_logs", "verify_registry_installation",
                     "verify_wmi_installation", "cleanup_test_installation"],
}


def _get_tool_objects(phase: str) -> list:
    """Import and return the actual tool instances for a phase."""
    try:
        from tools import (
            get_installer_metadata_tool, search_silent_switches_tool, analyze_dependencies_tool,
            read_psadt_template_tool, build_folder_structure_tool, get_package_history_tool,
            generate_script_tool, execute_install_test_tool, parse_logs_tool,
            verify_registry_tool, verify_wmi_tool, cleanup_test_install_tool, run_powershell_tool,
        )
        mapping = {
            "Research":     [get_installer_metadata_tool, search_silent_switches_tool, analyze_dependencies_tool],
            "Architecture": [read_psadt_template_tool, get_package_history_tool, build_folder_structure_tool],
            "Scripting":    [generate_script_tool, read_psadt_template_tool],
            "QA Testing":   [execute_install_test_tool, parse_logs_tool, verify_registry_tool,
                             verify_wmi_tool, cleanup_test_install_tool, run_powershell_tool],
        }
        return mapping.get(phase, [])
    except Exception:
        return []


def estimate_phase_tokens(phase: str, task_description: str = "") -> dict:
    """
    Compute real token counts for the phase:
      - tool_schemas: tokens in the JSON tool schemas sent to Groq
      - task_desc:    tokens in the task description string
      - system:       estimated CrewAI system prompt tokens
      - output_budget: max_output * max_iter (worst case output tokens)
      - total_sent:   input tokens sent per call (schemas + desc + system)
      - total_worst:  total_sent + output_budget (full worst-case)
    """
    max_iter = PHASE_MAX_ITER.get(phase, 4)

    # Tool schema tokens
    schema_tokens = 0
    try:
        from crewai.utilities.agent_utils import convert_tools_to_openai_schema
        tool_objs = _get_tool_objects(phase)
        if tool_objs:
            schemas, _, _ = convert_tools_to_openai_schema(tool_objs)
            schema_tokens = _count_tokens(json.dumps(schemas))
    except Exception:
        schema_tokens = len(PHASE_TOOL_NAMES.get(phase, [])) * 120  # rough fallback

    # Task description tokens
    desc_tokens = _count_tokens(task_description) if task_description else 0

    # Per-call input = schemas + description + system prompt
    per_call_input = schema_tokens + desc_tokens + _SYSTEM_PROMPT_TOKENS

    # Output budget = max output tokens * max iterations
    output_budget = _MAX_OUTPUT_TOKENS * max_iter

    return {
        "tool_schemas":   schema_tokens,
        "task_desc":      desc_tokens,
        "system":         _SYSTEM_PROMPT_TOKENS,
        "per_call_input": per_call_input,
        "output_budget":  output_budget,
        "total_worst":    per_call_input + output_budget,
        "max_iter":       max_iter,
    }


# Cache so we only compute once per run
_token_cache: dict[str, dict] = {}

def get_cached_estimate(phase: str, task_description: str = "") -> dict:
    if phase not in _token_cache:
        _token_cache[phase] = estimate_phase_tokens(phase, task_description)
    return _token_cache[phase]


def ask_terminal_permission(
    phase: str,
    context: str = "",
    task_description: str = "",
    est_tokens: Optional[int] = None,
) -> bool:
    """
    Show a terminal permission prompt before a workflow phase.
    Returns True (approved/skipped) or False (rejected).
    """
    agent_name = PHASE_AGENTS.get(phase, phase)
    tool_names = PHASE_TOOL_NAMES.get(phase, [])
    token_info = get_cached_estimate(phase, task_description)

    if _RICH:
        _show_rich_prompt(phase, agent_name, tool_names, token_info, context)
    else:
        _show_plain_prompt(phase, agent_name, tool_names, token_info, context)

    while True:
        try:
            answer = input("  > Your choice [Y/N/S]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Rejected (keyboard interrupt).")
            return False

        if answer in ("y", "yes"):
            if _RICH:
                _console.print("  [bold green]✔ Approved[/bold green] — starting phase...\n")
            else:
                print("  ✔ Approved — starting phase...\n")
            return True
        elif answer in ("n", "no"):
            if _RICH:
                _console.print("  [bold red]✘ Rejected[/bold red] — workflow will abort.\n")
            else:
                print("  ✘ Rejected — workflow will abort.\n")
            return False
        elif answer in ("s", "skip"):
            if _RICH:
                _console.print("  [bold yellow]⏭ Skipped[/bold yellow] — auto-approving this phase.\n")
            else:
                print("  ⏭ Skipped — auto-approving this phase.\n")
            return True
        else:
            print("  Please enter Y (approve), N (reject), or S (skip/auto-approve).")


def _show_rich_prompt(phase: str, agent: str, tools: list, tok: dict, context: str) -> None:
    _console.rule(f"[bold cyan]PSADT Agent — Permission Required[/bold cyan]")

    info = Table(box=rich_box.SIMPLE, show_header=False, padding=(0, 1))
    info.add_column("Key",   style="bold cyan",  no_wrap=True)
    info.add_column("Value", style="white")
    info.add_row("Phase",            phase)
    info.add_row("Agent",            agent)
    info.add_row("Max iterations",   str(tok["max_iter"]))
    info.add_row("─" * 18,           "─" * 26)
    info.add_row("Tool schemas",     f"[dim]{tok['tool_schemas']:,} tokens[/dim]")
    info.add_row("Task description", f"[dim]{tok['task_desc']:,} tokens[/dim]")
    info.add_row("System prompt",    f"[dim]~{tok['system']:,} tokens[/dim]")
    info.add_row("Input per call",   f"[yellow]{tok['per_call_input']:,} tokens[/yellow]")
    info.add_row("Output budget",    f"[dim]{tok['output_budget']:,} tokens ({_MAX_OUTPUT_TOKENS} × {tok['max_iter']})[/dim]")
    info.add_row("Worst-case total", f"[bold red]{tok['total_worst']:,} tokens[/bold red]  (TPM limit: 12,000)")
    _console.print(info)

    tools_table = Table(box=rich_box.SIMPLE, show_header=False, padding=(0, 1))
    tools_table.add_column("", style="dim cyan", no_wrap=True)
    tools_table.add_column("Tool", style="white")
    for t in tools:
        tools_table.add_row("•", t)
    _console.print(Panel(tools_table, title="[bold]Tools to be called[/bold]", border_style="cyan", padding=(0, 1)))

    if context:
        short_ctx = context[:300] + ("..." if len(context) > 300 else "")
        _console.print(Panel(short_ctx, title="[bold]Context[/bold]", border_style="dim", padding=(0, 1)))

    _console.print("  [bold]Y[/bold] = Approve   [bold]N[/bold] = Reject   [bold]S[/bold] = Skip (auto-approve)\n")


def _show_plain_prompt(phase: str, agent: str, tools: list, tok: dict, context: str) -> None:
    sep = "=" * 52
    print(f"\n{sep}")
    print(f"  PSADT Agent — Permission Required")
    print(f"  Phase            : {phase}")
    print(f"  Agent            : {agent}")
    print(f"  Max iterations   : {tok['max_iter']}")
    print(f"  ---")
    print(f"  Tool schemas     : {tok['tool_schemas']:,} tokens")
    print(f"  Task description : {tok['task_desc']:,} tokens")
    print(f"  System prompt    : ~{tok['system']:,} tokens")
    print(f"  Input per call   : {tok['per_call_input']:,} tokens")
    print(f"  Output budget    : {tok['output_budget']:,} tokens ({_MAX_OUTPUT_TOKENS} x {tok['max_iter']})")
    print(f"  Worst-case total : {tok['total_worst']:,} tokens  (TPM limit: 12,000)")
    print(f"  Tools            : {', '.join(tools)}")
    if context:
        print(f"  Context          : {context[:200]}")
    print(f"{sep}")
    print("  Y = Approve   N = Reject   S = Skip (auto-approve)\n")
