"""Interactive setup wizard: `shopclaw-setup`.

What it does:
  1. Asks for your Google Ads developer token and OAuth client ID and secret.
  2. Opens a browser so you can sign in and approve access (gets the refresh token).
  3. Saves everything to ~/.shopclaw/.env (readable only by you).
  4. Checks the credentials against the Google Ads API and lists reachable accounts.
  5. Registers shopclaw in Claude Desktop and, if the CLI is installed, in Claude Code.

No secrets are written into the Claude config files. The server reads
~/.shopclaw/.env on start.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

SCOPES = ["https://www.googleapis.com/auth/adwords"]
ENV_PATH = Path.home() / ".shopclaw" / ".env"
SERVER_NAME = "shopclaw"


# ---------------------------------------------------------------------------
# Small, testable helpers
# ---------------------------------------------------------------------------


def claude_desktop_config_path() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env_file(path: Path, updates: Dict[str, str]) -> None:
    """Merge `updates` into the env file, keep other keys, restrict permissions."""
    values = read_env_file(path)
    values.update({k: v for k, v in updates.items() if v is not None})
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Written by shopclaw-setup. Keep this file private."]
    lines += [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)


def merge_desktop_config(path: Path, command: str, args: List[str]) -> Optional[Path]:
    """Add or replace the shopclaw entry. Returns the backup path if one was made."""
    backup: Optional[Path] = None
    config: Dict = {}
    if path.exists():
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            config = json.loads(raw)
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    servers = config.setdefault("mcpServers", {})
    servers[SERVER_NAME] = {"command": command, "args": args}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return backup


def digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


# ---------------------------------------------------------------------------
# Network steps
# ---------------------------------------------------------------------------


def get_refresh_token(client_id: str, client_secret: str) -> str:
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=SCOPES,
    )
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        raise RuntimeError(
            "Google did not return a refresh token. Remove the app from "
            "https://myaccount.google.com/permissions and run the setup again."
        )
    return creds.refresh_token


def check_access(values: Dict[str, str]) -> List[str]:
    """Return the customer IDs these credentials can reach. Raises on failure."""
    from google.ads.googleads.client import GoogleAdsClient

    config = {
        "client_id": values["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": values["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": values["GOOGLE_ADS_REFRESH_TOKEN"],
        "use_proto_plus": True,
    }
    token = values.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip()
    if token:
        config["developer_token"] = token
    login = digits(values.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", ""))
    if login:
        config["login_customer_id"] = login
    client = GoogleAdsClient.load_from_dict(config)
    response = client.get_service("CustomerService").list_accessible_customers()
    return [name.rsplit("/", 1)[-1] for name in response.resource_names]


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

HELP = """
Before you start you need an OAuth client of type "Desktop app":

     console.cloud.google.com > APIs & Services > Library: enable "Google Ads API".
     Google Auth Platform > Clients > Create client > Desktop app.
     Copy the client ID and client secret.

  Access level: Google Cloud console > Google Ads API > Overview. "Test" works on
  test accounts only. Apply for "Explorer" (usually approved in minutes) to use
  production accounts.

  A developer token is optional. Since September 2026 Google ignores it. Leave it
  empty unless you want to keep sending one.
"""


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [{'saved' if secret else default}]" if default else ""
    text = f"{prompt}{suffix}: "
    value = getpass.getpass(text) if secret else input(text)
    return value.strip() or default


def yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = input(f"{prompt} [{hint}]: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes", "д", "да"}


def register_claude_code(python: str) -> None:
    claude = shutil.which("claude")
    if not claude:
        print("  Claude Code CLI not found, skipping.")
        return
    cmd = [claude, "mcp", "add", "--scope", "user", SERVER_NAME, "--", python, "-m", "shopclaw"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print("  Registered in Claude Code (user scope).")
    else:
        message = (result.stderr or result.stdout).strip()
        print(f"  Claude Code registration failed: {message}")
        print(f"  Run it yourself: claude mcp add --scope user {SERVER_NAME} -- {python} -m shopclaw")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="shopclaw-setup", description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="only verify the saved credentials")
    parser.add_argument("--reauth", action="store_true", help="get a new refresh token")
    parser.add_argument("--no-desktop", action="store_true", help="do not touch Claude Desktop config")
    parser.add_argument("--no-claude-code", action="store_true", help="do not register in Claude Code")
    args = parser.parse_args(argv)

    saved = read_env_file(ENV_PATH)

    if args.check:
        return run_check(saved)

    print("shopclaw setup")
    print(HELP)

    values: Dict[str, str] = {}
    values["GOOGLE_ADS_CLIENT_ID"] = ask("OAuth client ID", saved.get("GOOGLE_ADS_CLIENT_ID", ""))
    values["GOOGLE_ADS_CLIENT_SECRET"] = ask(
        "OAuth client secret", saved.get("GOOGLE_ADS_CLIENT_SECRET", ""), secret=True
    )
    values["GOOGLE_ADS_LOGIN_CUSTOMER_ID"] = digits(
        ask("Manager (MCC) account ID, digits only, empty if none", saved.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", ""))
    )

    values["GOOGLE_ADS_DEVELOPER_TOKEN"] = saved.get("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    missing = [k for k in ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET") if not values[k]]
    if missing:
        print("\nMissing: " + ", ".join(missing) + ". Nothing was saved.")
        return 1

    refresh = saved.get("GOOGLE_ADS_REFRESH_TOKEN", "")
    if args.reauth or not refresh or saved.get("GOOGLE_ADS_CLIENT_ID") != values["GOOGLE_ADS_CLIENT_ID"]:
        print("\nOpening your browser. Sign in with the Google account that has access to Google Ads and click Allow.")
        try:
            refresh = get_refresh_token(values["GOOGLE_ADS_CLIENT_ID"], values["GOOGLE_ADS_CLIENT_SECRET"])
        except Exception as exc:
            print(f"\nCould not get a refresh token: {exc}")
            return 1
    values["GOOGLE_ADS_REFRESH_TOKEN"] = refresh

    # Safe defaults. Existing user choices are kept.
    defaults = {
        "SHOPCLAW_MAX_DAILY_BUDGET": "50",
        "SHOPCLAW_VALIDATE_ON_PREVIEW": "true",
    }
    for key, default in defaults.items():
        values.setdefault(key, saved.get(key, default))

    write_env_file(ENV_PATH, values)
    print(f"\nSaved credentials to {ENV_PATH}")

    merged = {**saved, **values}
    if run_check(merged) != 0:
        print("The credentials are saved, so you can fix the issue and run: shopclaw-setup --check")

    python = sys.executable
    print("\nRegistering shopclaw:")
    if not args.no_desktop:
        path = claude_desktop_config_path()
        try:
            backup = merge_desktop_config(path, python, ["-m", "shopclaw"])
            print(f"  Claude Desktop: updated {path}" + (f" (backup: {backup.name})" if backup else ""))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  Claude Desktop config not changed: {exc}")
    if not args.no_claude_code and yes("Register in Claude Code too?", default=False):
        register_claude_code(python)

    print(
        "\nDone. Restart Claude Desktop, then ask: 'what tools does shopclaw have?'\n"
        "Safety defaults: daily budget cap 50, new campaigns PAUSED, every change needs confirm.\n"
        f"Edit {ENV_PATH} to change limits (SHOPCLAW_MAX_DAILY_BUDGET, SHOPCLAW_ALLOWED_CUSTOMERS, SHOPCLAW_READ_ONLY)."
    )
    return 0


def run_check(values: Dict[str, str]) -> int:
    needed = (
        "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_REFRESH_TOKEN",
    )
    missing = [k for k in needed if not values.get(k)]
    if missing:
        print("Missing saved values: " + ", ".join(missing) + ". Run shopclaw-setup.")
        return 1
    print("\nChecking access to the Google Ads API...")
    try:
        customers = check_access(values)
    except Exception as exc:
        text = str(exc)
        print(f"  Failed: {text[:400]}")
        low = text.lower()
        if "cloud_project_not_approved" in low or ("test" in low and "account" in low):
            print("  Hint: the Cloud project has Test access only. Apply for Explorer in Cloud console > Google Ads API > Overview.")
        if "invalid_grant" in low:
            print("  Hint: run shopclaw-setup --reauth to get a fresh refresh token.")
        return 1
    if customers:
        print(f"  OK. {len(customers)} account(s) reachable: " + ", ".join(customers[:10]))
    else:
        print("  OK, but no accounts are directly reachable. Set the manager (MCC) ID if you use one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
