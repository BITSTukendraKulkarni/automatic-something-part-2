"""
Utility helpers: history management, HITL gate, logging, exit-code lookup.
"""

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from config import HISTORY_DB_PATH, HITL_ENABLED, LOGS_DIR

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s — %(message)s")
    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    # File handler
    fh = logging.FileHandler(LOGS_DIR / "system.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger

log = get_logger("psadt-utils")

# ---------------------------------------------------------------------------
# Exit code lookup table
# ---------------------------------------------------------------------------
MSI_EXIT_CODES: dict[int, str] = {
    0:    "SUCCESS — Installation completed successfully",
    1601: "ERROR — Windows Installer service could not be accessed",
    1602: "USER_CANCEL — User cancelled installation",
    1603: "FATAL_ERROR — Fatal error during installation (check log, permissions, disk space)",
    1604: "SUSPEND — Installation suspended, incomplete",
    1605: "UNINSTALL_NOT_FOUND — Product code not registered for uninstall",
    1618: "ALREADY_RUNNING — Another installation is already in progress",
    1619: "PACKAGE_NOT_FOUND — Installation package could not be opened",
    1620: "PACKAGE_INVALID — Installation package is invalid",
    1622: "LOG_OPEN_FAILED — Error opening installation log file",
    1625: "POLICY_PROHIBITED — Installation forbidden by system policy",
    1633: "PLATFORM_UNSUPPORTED — Platform not supported",
    1638: "NEWER_VERSION — Another version already installed",
    1641: "REBOOT_INITIATED — Installer initiated a restart",
    3010: "REBOOT_REQUIRED — Restart required to complete installation (soft reboot)",
}

def explain_exit_code(code: int) -> str:
    return MSI_EXIT_CODES.get(code, f"UNKNOWN exit code {code} — consult vendor documentation")

# ---------------------------------------------------------------------------
# Package history management
# ---------------------------------------------------------------------------
def _load_history() -> dict:
    p = Path(HISTORY_DB_PATH)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_history(data: dict) -> None:
    Path(HISTORY_DB_PATH).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def save_package_record(app_name: str, record: dict) -> None:
    """Append a package build record to the history database."""
    history = _load_history()
    if app_name not in history:
        history[app_name] = []
    record["timestamp"] = datetime.utcnow().isoformat()
    history[app_name].append(record)
    _save_history(history)
    log.info(f"Package record saved for '{app_name}'")


def get_package_history(app_name: str) -> list[dict]:
    """Return all historical records for an app (newest first)."""
    history = _load_history()
    records = history.get(app_name, [])
    return sorted(records, key=lambda r: r.get("timestamp", ""), reverse=True)


def list_all_packaged_apps() -> list[str]:
    """List all app names that have at least one package record."""
    return sorted(_load_history().keys())


# ---------------------------------------------------------------------------
# Human-in-the-Loop gate
# ---------------------------------------------------------------------------
# This dict is used by the Gradio UI to communicate approval state.
_hitl_state: dict[str, Any] = {}

def hitl_request_approval(phase: str, context: str) -> dict:
    """
    Register an approval request.  Returns immediately with a pending token.
    The UI polls this dict and sets the 'approved' flag.
    """
    if not HITL_ENABLED:
        return {"approved": True, "bypass": True, "phase": phase}
    token = f"{phase}_{datetime.utcnow().strftime('%H%M%S')}"
    _hitl_state[token] = {
        "phase": phase,
        "context": context,
        "approved": None,  # None = pending, True = approved, False = rejected
        "requested_at": datetime.utcnow().isoformat(),
    }
    log.info(f"[HITL] Approval requested — phase={phase}, token={token}")
    return {"token": token, "phase": phase, "status": "pending"}


def hitl_set_decision(token: str, approved: bool) -> None:
    """Called by the UI when the user clicks Approve or Reject."""
    if token in _hitl_state:
        _hitl_state[token]["approved"] = approved
        _hitl_state[token]["decided_at"] = datetime.utcnow().isoformat()
        log.info(f"[HITL] Decision recorded — token={token}, approved={approved}")


def hitl_get_pending() -> list[dict]:
    """Return all pending (undecided) HITL requests."""
    return [
        {"token": k, **v}
        for k, v in _hitl_state.items()
        if v["approved"] is None
    ]


def hitl_wait_for_approval(token: str, poll_interval: float = 1.0, timeout: float = 300.0) -> bool:
    """
    Block (in a thread) until the user approves or rejects the token,
    or until timeout elapses (auto-reject on timeout).
    """
    import time
    if not HITL_ENABLED:
        return True
    elapsed = 0.0
    while elapsed < timeout:
        state = _hitl_state.get(token, {})
        if state.get("approved") is True:
            return True
        if state.get("approved") is False:
            return False
        time.sleep(poll_interval)
        elapsed += poll_interval
    log.warning(f"[HITL] Timeout waiting for approval — token={token}")
    return False


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------
def sanitize_app_name(name: str) -> str:
    """
    Return a filesystem-safe version of an app name.
    - Replaces any character that is not alphanumeric, hyphen, or dot with underscore
    - Collapses consecutive underscores
    - Strips leading/trailing underscores and dots
    - Caps at 64 characters
    - Rejects Windows reserved names (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
    """
    _WINDOWS_RESERVED = {
        "CON","PRN","AUX","NUL",
        "COM1","COM2","COM3","COM4","COM5","COM6","COM7","COM8","COM9",
        "LPT1","LPT2","LPT3","LPT4","LPT5","LPT6","LPT7","LPT8","LPT9",
    }
    safe = re.sub(r'[^\w\-.]', '_', name)
    safe = re.sub(r'_+', '_', safe)
    safe = safe.strip("_.")[:64]
    if not safe:
        safe = "app"
    if safe.upper() in _WINDOWS_RESERVED:
        safe = f"_{safe}"
    return safe


def validate_package_path(pkg_dir: Path, output_base: Path) -> None:
    """
    Raise ValueError if pkg_dir is not safe to use as a package destination.
    Checks:
      1. Path stays inside output_base (prevents traversal)
      2. Directory does not already exist (prevents collision/overwrite)
      3. Parent directory is reachable and writable
      4. Sufficient disk space (at least 500 MB free)
      5. Path length within Windows MAX_PATH (260 chars)
    """
    import shutil as _shutil

    # 1. Traversal check — resolve both and confirm containment
    try:
        resolved_pkg  = pkg_dir.resolve()
        resolved_base = output_base.resolve()
        resolved_pkg.relative_to(resolved_base)
    except ValueError:
        raise ValueError(
            f"Package path '{pkg_dir}' escapes the allowed packages directory '{output_base}'. "
            "Possible path traversal attempt."
        )

    # 2. Collision check
    if pkg_dir.exists():
        raise ValueError(
            f"Package directory already exists: '{pkg_dir}'. "
            "Delete it manually or use a different app name/version."
        )

    # 3. Parent writable
    parent = pkg_dir.parent
    if not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            raise ValueError(f"Cannot create parent directory '{parent}' — permission denied.")
    if not os.access(str(parent), os.W_OK):
        raise ValueError(f"Parent directory '{parent}' is not writable.")

    # 4. Disk space — require at least 500 MB free
    _MIN_FREE_MB = 500
    try:
        usage = _shutil.disk_usage(str(parent))
        free_mb = usage.free // (1024 * 1024)
        if free_mb < _MIN_FREE_MB:
            raise ValueError(
                f"Insufficient disk space on '{parent.drive}': "
                f"{free_mb} MB free, need at least {_MIN_FREE_MB} MB."
            )
    except (FileNotFoundError, PermissionError):
        pass  # Non-fatal if we can't check

    # 5. Path length
    if len(str(pkg_dir)) > 200:
        raise ValueError(
            f"Package path is too long ({len(str(pkg_dir))} chars): '{pkg_dir}'. "
            "Shorten the app name or version to stay within Windows path limits."
        )


def timestamp_slug() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")
