"""Google Ads client construction."""

from __future__ import annotations

import os
from typing import Any, Optional

from shopclaw.config import Settings

# The developer token is optional: since September 2026 Google ignores it and
# ties access levels to the Cloud project that owns the OAuth client.
_REQUIRED = (
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REFRESH_TOKEN",
)

_client: Optional[Any] = None


class ConfigError(RuntimeError):
    """Raised when credentials are missing."""


def build_client(settings: Settings) -> Any:
    from google.ads.googleads.client import GoogleAdsClient

    if settings.yaml_path:
        return GoogleAdsClient.load_from_storage(settings.yaml_path, version=settings.api_version)

    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        raise ConfigError(
            "Missing Google Ads credentials: "
            + ", ".join(missing)
            + ". Set them in the environment or in .env, or point SHOPCLAW_GOOGLE_ADS_YAML to a google-ads.yaml file."
        )

    config = {
        "client_id": os.environ["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_ADS_REFRESH_TOKEN"],
        "use_proto_plus": True,
    }
    developer_token = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip()
    if developer_token:
        config["developer_token"] = developer_token
    if settings.login_customer_id:
        config["login_customer_id"] = settings.login_customer_id
    return GoogleAdsClient.load_from_dict(config, version=settings.api_version)


def get_client(settings: Settings) -> Any:
    """Return a cached client."""
    global _client
    if _client is None:
        _client = build_client(settings)
    return _client


def reset_client() -> None:
    global _client
    _client = None
