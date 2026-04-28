"""
Verbose task logger for the PSADT Agentic AI system.

Creates one dedicated log file per task run, named after the app/task and timestamped.
Captures:
  - Exact LLM prompts sent (messages list)
  - Raw LLM responses received
  - Every workflow action (phase transitions, file ops, commands, HITL gates, tool calls)

Usage (in the module that knows the task name):
    from verbose_logger import VerboseLogger
    vlog = VerboseLogger.for_task("MyApp_1.0")   # creates logs/tasks/MyApp_1.0_20260424_153012.log
    vlog.action("Copied installer to package dir", src=..., dst=...)
    vlog.llm_prompt(messages)
    vlog.llm_response(response_text)
"""

import json
import logging
import os
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Resolved lazily so config circular imports are avoided
_LOGS_DIR: Path | None = None


def _get_logs_dir() -> Path:
    global _LOGS_DIR
    if _LOGS_DIR is None:
        try:
            from config import LOGS_DIR
            _LOGS_DIR = Path(LOGS_DIR) / "tasks"
        except Exception:
            _LOGS_DIR = Path(__file__).parent / "logs" / "tasks"
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return _LOGS_DIR


# ---------------------------------------------------------------------------
# Module-level registry so all modules can access the *current* logger
# by name without needing a direct reference.
# ---------------------------------------------------------------------------
_registry: dict[str, "VerboseLogger"] = {}

_SEPARATOR = "=" * 80
_MINI_SEP  = "-" * 60


def _now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str, ensure_ascii=False)
    except Exception:
        return str(obj)


class VerboseLogger:
    """One instance per task run. All writes are UTF-8, append-safe."""

    def __init__(self, task_label: str, log_path: Path):
        self.task_label = task_label
        self.log_path   = log_path
        self._llm_call_count = 0
        self._action_count   = 0

        # Open the file for the lifetime of this logger
        self._fh = open(log_path, "w", encoding="utf-8", buffering=1)
        self._write_header()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def for_task(cls, task_label: str) -> "VerboseLogger":
        """
        Create (and register) a logger for the given task label.
        The log file is named: <sanitized_label>_<timestamp>.log
        """
        safe_label = _sanitize(task_label)
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_label}_{ts}.log"
        log_path = _get_logs_dir() / filename

        inst = cls(task_label, log_path)
        _registry[task_label] = inst
        # Also store under the sanitized key for easy lookup
        _registry[safe_label] = inst
        return inst

    @classmethod
    def get(cls, key: str) -> "VerboseLogger | None":
        """Retrieve a registered logger by task label or sanitized name."""
        return _registry.get(key)

    @classmethod
    def get_current(cls) -> "VerboseLogger | None":
        """Return the most-recently created logger (last in registry)."""
        if not _registry:
            return None
        return list(_registry.values())[-1]

    # ------------------------------------------------------------------
    # Public logging API
    # ------------------------------------------------------------------

    def llm_prompt(self, messages: list[dict] | str, *, call_label: str = "") -> None:
        """
        Log the exact prompt sent to the LLM.
        `messages` may be a list of role/content dicts or a plain string.
        """
        self._llm_call_count += 1
        tag = call_label or f"LLM Call #{self._llm_call_count}"
        block = [
            _SEPARATOR,
            f"[{_now()}]  >>>  PROMPT  —  {tag}",
            _SEPARATOR,
        ]
        if isinstance(messages, list):
            for msg in messages:
                role    = msg.get("role", "?").upper()
                content = msg.get("content", "")
                if isinstance(content, list):
                    # Multi-part content (vision, tool results, etc.)
                    content_str = _safe_json(content)
                else:
                    content_str = str(content)
                block.append(f"  [{role}]")
                block.append(textwrap.indent(content_str, "    "))
                block.append("")
        else:
            block.append(str(messages))
        block.append(_SEPARATOR)
        self._write("\n".join(block))

    def llm_response(self, response: Any, *, call_label: str = "") -> None:
        """
        Log the raw LLM response (text or full response object).
        """
        tag = call_label or f"LLM Call #{self._llm_call_count}"
        block = [
            _MINI_SEP,
            f"[{_now()}]  <<<  RESPONSE  —  {tag}",
            _MINI_SEP,
        ]
        if hasattr(response, "choices"):
            # litellm / OpenAI-style ModelResponse
            for i, choice in enumerate(response.choices):
                msg = getattr(choice, "message", None)
                if msg:
                    block.append(f"  [choice {i}] role={getattr(msg, 'role', '?')}")
                    block.append(textwrap.indent(str(getattr(msg, "content", "")), "    "))
                    tool_calls = getattr(msg, "tool_calls", None)
                    if tool_calls:
                        block.append(f"  [tool_calls]")
                        block.append(textwrap.indent(_safe_json(
                            [{"id": tc.id, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                             for tc in tool_calls]
                        ), "    "))
            usage = getattr(response, "usage", None)
            if usage:
                block.append(f"  [usage] prompt_tokens={getattr(usage,'prompt_tokens','?')}  "
                             f"completion_tokens={getattr(usage,'completion_tokens','?')}")
        elif hasattr(response, "raw"):
            block.append(str(response.raw))
        else:
            block.append(str(response))
        block.append(_MINI_SEP)
        self._write("\n".join(block))

    def action(self, description: str, **details: Any) -> None:
        """
        Log a workflow action with optional key=value detail pairs.
        Examples:
            vlog.action("Copying installer", src=src_path, dst=dst_path)
            vlog.action("Running command", cmd="msiexec /i ...")
            vlog.action("Phase started", phase="Research")
        """
        self._action_count += 1
        parts = [f"[{_now()}]  ACTION #{self._action_count:04d}  {description}"]
        for k, v in details.items():
            parts.append(f"    {k} = {v}")
        self._write("\n".join(parts))

    def section(self, title: str) -> None:
        """Write a prominent section header."""
        self._write(f"\n{_SEPARATOR}\n  {title}\n{_SEPARATOR}")

    def info(self, message: str) -> None:
        """Write a plain informational line."""
        self._write(f"[{_now()}]  INFO   {message}")

    def warning(self, message: str) -> None:
        self._write(f"[{_now()}]  WARN   {message}")

    def error(self, message: str) -> None:
        self._write(f"[{_now()}]  ERROR  {message}")

    def close(self) -> None:
        """Flush and close the log file."""
        self._write_footer()
        try:
            self._fh.flush()
            self._fh.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write(self, text: str) -> None:
        try:
            self._fh.write(text + "\n")
        except Exception:
            pass  # never crash the workflow due to logging errors

    def _write_header(self) -> None:
        self._write(
            f"{_SEPARATOR}\n"
            f"  PSADT Agentic AI — Verbose Task Log\n"
            f"  Task  : {self.task_label}\n"
            f"  File  : {self.log_path}\n"
            f"  Start : {_now()}\n"
            f"  PID   : {os.getpid()}\n"
            f"{_SEPARATOR}\n"
        )

    def _write_footer(self) -> None:
        self._write(
            f"\n{_SEPARATOR}\n"
            f"  END OF LOG\n"
            f"  Task  : {self.task_label}\n"
            f"  Finish: {_now()}\n"
            f"  LLM calls logged : {self._llm_call_count}\n"
            f"  Actions logged   : {self._action_count}\n"
            f"{_SEPARATOR}\n"
        )

    def __repr__(self) -> str:
        return f"VerboseLogger(task={self.task_label!r}, log={self.log_path})"


# ---------------------------------------------------------------------------
# Logging bridge — forward Python logging records into the current task log
# ---------------------------------------------------------------------------

class _VerboseLogHandler(logging.Handler):
    """Attaches to the root logger and mirrors records into the active task log."""

    def emit(self, record: logging.LogRecord) -> None:
        vlog = VerboseLogger.get_current()
        if vlog is None:
            return
        level = record.levelname
        msg   = self.format(record)
        try:
            vlog._fh.write(f"[{_now()}]  {level:<7}  {msg}\n")
        except Exception:
            pass


def attach_logging_bridge() -> None:
    """
    Call once at startup to forward all Python logging output
    (INFO and above) into whichever task log is currently active.
    Idempotent — safe to call multiple times.
    """
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, _VerboseLogHandler):
            return  # already attached
    handler = _VerboseLogHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(name)s — %(message)s"))
    root.addHandler(handler)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize(name: str) -> str:
    import re
    safe = re.sub(r'[^\w\-.]', '_', name)
    safe = re.sub(r'_+', '_', safe).strip('_.')[:80]
    return safe or "task"
