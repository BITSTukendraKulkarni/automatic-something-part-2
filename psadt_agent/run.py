"""
Entry point — launches the Gradio UI.
Usage:
    python run.py            # UI on http://localhost:7860
    python run.py --port 8080
    python run.py --cli --installer "C:\\path\\app.exe" --name "MyApp" --version "1.0"
"""

import argparse
import sys
import os

# Ensure the package directory is on the path
sys.path.insert(0, os.path.dirname(__file__))


def cli_mode(installer: str, name: str, version: str, template: str, test_mode: str):
    """Run the workflow headlessly (no UI). Useful for CI/CD pipelines."""
    from crew import PSADTCrew
    from config import PSADT_TEMPLATE_PATH, TEST_MODE

    def progress(phase, status, data):
        print(f"[{phase}] {status}: {str(data)[:120]}")

    crew = PSADTCrew(
        installer_path=installer,
        app_name=name,
        app_version=version,
        template_path=template or PSADT_TEMPLATE_PATH,
        test_mode=test_mode or TEST_MODE,
        progress_callback=progress,
    )
    result = crew.run()

    import json
    print("\n" + "=" * 60)
    print("FINAL RESULT")
    print("=" * 60)
    print(json.dumps(result, indent=2, default=str))

    return 0 if result.get("success") else 1


def ui_mode(port: int):
    """Launch the Gradio UI."""
    from ui import build_ui
    ui = build_ui()
    from ui import UI_THEME
    ui.launch(
        server_name="0.0.0.0",
        server_port=port,
        share=False,
        show_error=True,
        theme=UI_THEME,
    )


def main():
    parser = argparse.ArgumentParser(description="PSADT Agentic AI")
    parser.add_argument("--port", type=int, default=7860, help="Gradio UI port (default: 7860)")
    parser.add_argument("--cli", action="store_true", help="Run in headless CLI mode (no UI)")
    parser.add_argument("--installer", type=str, help="[CLI] Installer path")
    parser.add_argument("--name",      type=str, help="[CLI] Application name")
    parser.add_argument("--version",   type=str, default="", help="[CLI] Application version")
    parser.add_argument("--template",  type=str, default="", help="[CLI] PSADT template path")
    parser.add_argument("--test-mode", type=str, default="host", choices=["host", "sandbox"],
                        help="[CLI] Test mode: host or sandbox")
    args = parser.parse_args()

    if args.cli:
        if not args.installer or not args.name:
            parser.error("--cli requires --installer and --name")
        sys.exit(cli_mode(args.installer, args.name, args.version, args.template, args.test_mode))
    else:
        ui_mode(args.port)


if __name__ == "__main__":
    main()
