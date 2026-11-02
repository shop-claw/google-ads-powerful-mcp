"""Environment driven settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


def load_env_files() -> None:
    """Load ~/.shopclaw/.env (written by shopclaw-setup), then .env in the working directory.

    Values already present in the process environment always win, so an `env`
    block in a Claude config file overrides the files.
    """
    from dotenv import load_dotenv

    load_dotenv(Path.home() / ".shopclaw" / ".env")
    load_dotenv()


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


@dataclass(frozen=True)
class Settings:
    read_only: bool
    max_daily_budget: float
    allowed_customers: frozenset
    validate_on_preview: bool
    pending_ttl_seconds: int
    audit_log_path: Path
    api_version: Optional[str]
    yaml_path: Optional[str]
    login_customer_id: Optional[str]


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    env = os.environ if env is None else env

    allowed_raw = env.get("SHOPCLAW_ALLOWED_CUSTOMERS", "")
    allowed = frozenset(_digits(part) for part in allowed_raw.split(",") if _digits(part))

    login = _digits(env.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")) or None

    try:
        max_budget = float(env.get("SHOPCLAW_MAX_DAILY_BUDGET", "100") or "100")
    except ValueError:
        max_budget = 100.0

    try:
        ttl = int(env.get("SHOPCLAW_PENDING_TTL_SECONDS", "900") or "900")
    except ValueError:
        ttl = 900

    audit = Path(env.get("SHOPCLAW_AUDIT_LOG", "~/.shopclaw/audit.jsonl")).expanduser()

    return Settings(
        read_only=_bool(env, "SHOPCLAW_READ_ONLY", False),
        max_daily_budget=max_budget,
        allowed_customers=allowed,
        validate_on_preview=_bool(env, "SHOPCLAW_VALIDATE_ON_PREVIEW", True),
        pending_ttl_seconds=ttl,
        audit_log_path=audit,
        api_version=(env.get("SHOPCLAW_API_VERSION") or "").strip() or None,
        yaml_path=(env.get("SHOPCLAW_GOOGLE_ADS_YAML") or "").strip() or None,
        login_customer_id=login,
    )
