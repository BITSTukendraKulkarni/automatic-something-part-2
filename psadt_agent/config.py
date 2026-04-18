"""
Central configuration for the PSADT Agentic AI system.
All paths, model settings, and feature flags live here.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# LLM / API
# ---------------------------------------------------------------------------
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
PACKAGES_DIR = BASE_DIR / "packages"
LOGS_DIR = BASE_DIR / "logs"
TEMPLATES_DIR = BASE_DIR / "templates"
PSADT_LOG_DIR = Path(r"C:\Windows\Logs\Software")

PACKAGES_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# PSADT Defaults
# ---------------------------------------------------------------------------
PSADT_TEMPLATE_PATH: str = os.getenv("PSADT_TEMPLATE_PATH", str(TEMPLATES_DIR / "PSAppDeployToolkit"))
PSADT_VERSION: str = os.getenv("PSADT_VERSION", "3.10.2")

# ---------------------------------------------------------------------------
# Human-in-the-Loop (HITL)
# ---------------------------------------------------------------------------
# Set HITL_BYPASS=true in .env to skip approval gates (NOT recommended for production)
HITL_ENABLED: bool = os.getenv("HITL_BYPASS", "false").lower() != "true"

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------
# Options: "host" | "sandbox"
TEST_MODE: str = os.getenv("TEST_MODE", "host")

# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
HISTORY_DB_PATH: str = str(BASE_DIR / "package_history.json")

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
MCP_TRANSPORT: str = os.getenv("MCP_TRANSPORT", "stdio")  # "stdio" or "http"
MCP_HTTP_PORT: int = int(os.getenv("MCP_HTTP_PORT", "8765"))
