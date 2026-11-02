"""Get an OAuth refresh token with the Google Ads (adwords) scope.

Usage:
    pip install -e .
    export GOOGLE_ADS_CLIENT_ID=...
    export GOOGLE_ADS_CLIENT_SECRET=...
    python scripts/get_refresh_token.py

A browser window opens, you sign in with the Google account that has access to
your Google Ads manager account, and the refresh token is printed. Put it in
.env as GOOGLE_ADS_REFRESH_TOKEN.

The OAuth client must be of type "Desktop app" in Google Cloud Console, with the
Google Ads API enabled for the project.
"""

import os
import sys

SCOPES = ["https://www.googleapis.com/auth/adwords"]


def main() -> int:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Missing dependency. Run: pip install -e .", file=sys.stderr)
        return 1

    client_id = os.environ.get("GOOGLE_ADS_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_ADS_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("Set GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET first.", file=sys.stderr)
        return 1

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
    print("\nGOOGLE_ADS_REFRESH_TOKEN=" + creds.refresh_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
