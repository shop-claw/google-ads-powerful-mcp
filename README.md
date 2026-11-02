# shopclaw

A write layer MCP server for Google Ads.

Google's official [Google Ads MCP server](https://github.com/googleads/google-ads-mcp) is read-only. shopclaw is the other half: it lets Claude (or any MCP client) create and change campaigns, but only through a **preview, then confirm** flow with hard safety limits.

```
            ┌──────────────────────────┐
            │  Claude / Cursor / etc.  │
            └─────────┬──────────┬─────┘
          reads       │          │      writes (stage, confirm)
   ┌──────────────────▼──┐   ┌───▼──────────────────┐
   │ Google Ads MCP      │   │ shopclaw             │
   │ (official, read)    │   │ (this repo, write)   │
   └──────────┬──────────┘   └───┬──────────────────┘
              └────────┬─────────┘
                 Google Ads API
```

Use the official server for analysis and reporting. Use shopclaw to act on what you found: launch a campaign, block wasted search terms, add keywords, move budgets.

## How a change flows

1. You ask for something: "Create a brand search campaign for account 123-456-7890, 20 per day, geo US."
2. A shopclaw tool **stages** it. Nothing is sent to Google yet. By default the change is run through Google's `validate_only` mode so API errors show up immediately.
3. Claude shows you the preview and a `change_id`.
4. You approve. Claude calls `confirm_change`. Only now are the changes applied, atomically.
5. Every step (staged, confirmed, cancelled, failed) is appended to a JSON lines audit log.

## Safety model

| Rule | Detail |
|---|---|
| Preview then confirm | No write tool sends anything. Only `confirm_change` does. Staged changes expire (default 15 minutes). |
| New campaigns are PAUSED | Enabling is a separate, explicit step with `update_campaign_status`. |
| No destructive tools | There is no remove or delete tool. Statuses are limited to ENABLED and PAUSED. |
| Budget cap | `SHOPCLAW_MAX_DAILY_BUDGET` is a hard cap for any budget created or changed. |
| Geo targeting is mandatory | New campaigns require `geo_target_ids`, so nothing goes out to every country by accident. |
| Account allowlist | `SHOPCLAW_ALLOWED_CUSTOMERS` limits which accounts can be written to. |
| Read-only switch | `SHOPCLAW_READ_ONLY=true` blocks every write tool. |
| Atomic requests | A campaign is created with one `GoogleAdsService.Mutate` call. If any part fails, nothing is created. |
| Audit log | `~/.shopclaw/audit.jsonl` by default. |

## Tools

| Tool | What it does |
|---|---|
| `create_search_campaign` | Stages budget, campaign (PAUSED), geo and language targeting, ad group, keywords, negatives and one responsive search ad |
| `add_keywords` | Stages keywords for an existing ad group |
| `add_negative_keywords` | Stages campaign level negative keywords |
| `create_responsive_search_ad` | Stages an RSA in an existing ad group |
| `update_budget` | Stages a new daily amount for a budget (capped) |
| `update_campaign_status` | Stages ENABLED or PAUSED |
| `confirm_change` | Executes a staged change |
| `cancel_change` | Discards a staged change |
| `list_pending_changes` | Shows what is waiting for confirmation |
| `gaql_read` | Read-only GAQL SELECT, mainly to look up IDs before staging a change |

Keyword syntax in every tool: `[exact match]`, `"phrase match"`, or plain text which uses `default_match_type`.

## Quick start

```bash
pipx install git+https://github.com/shop-claw/google-ads-powerful-mcp.git
shopclaw-setup
```

`shopclaw-setup` is an interactive wizard. It:

1. asks for your OAuth client ID and secret (a developer token is no longer needed),
2. opens your browser so you can sign in and click Allow (this creates the refresh token),
3. saves everything to `~/.shopclaw/.env`, readable only by you,
4. checks the credentials against the Google Ads API and lists the accounts it can reach,
5. registers shopclaw in Claude Desktop (your config is backed up first) and, if you want, in Claude Code.

No secrets are written into the Claude config files. Restart Claude Desktop afterwards and ask "what tools does shopclaw have?".

Check or refresh the saved credentials at any time:

```bash
shopclaw-setup --check      # verify access
shopclaw-setup --reauth     # get a new refresh token
```

Safe defaults are applied: daily budget cap of 50, new campaigns PAUSED, every change needs confirmation. Edit `~/.shopclaw/.env` to change limits.

### What you need before running the wizard

Things that only you can create in your own Google accounts:

1. **Google Ads API access level.** Since September 2026 developer tokens are no longer issued or needed. Access belongs to the Cloud project that owns your OAuth client. In Cloud console open Google Ads API, Overview. "Test" works on test accounts only. Apply for **Explorer** (usually approved within minutes, 2,880 production operations per day). **Basic** needs brand verification.
2. **An OAuth client of type Desktop app.** In Google Cloud Console enable the "Google Ads API" for a project, then APIs & Services, Credentials, Create credentials, OAuth client ID, Desktop app. Copy the client ID and client secret. If the consent screen is in "Testing" mode, add your Google account as a test user.

## Manual setup

If you prefer to do it yourself instead of the wizard.

### 1. Google Ads API access

You need:

- Explorer or higher access on the Cloud project (see above). A developer token is optional.
- A Google Cloud project with the **Google Ads API** enabled and an OAuth client of type **Desktop app**.
- A refresh token with the `adwords` scope (step 3).

### 2. Install

```bash
git clone https://github.com/shop-claw/google-ads-powerful-mcp.git
cd google-ads-powerful-mcp
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env
```

### 3. Get a refresh token

```bash
export GOOGLE_ADS_CLIENT_ID=...
export GOOGLE_ADS_CLIENT_SECRET=...
python scripts/get_refresh_token.py
```

Sign in with the Google account that has access to your manager account. Put the printed token in `.env`.

### 4. Configure

Edit `.env`. At minimum set the five `GOOGLE_ADS_*` values. Review the `SHOPCLAW_*` safety settings. Start with a low `SHOPCLAW_MAX_DAILY_BUDGET` and, ideally, an allowlist containing a single test account.

### 5. Connect to Claude

**Claude Desktop.** Merge `examples/claude_desktop_config.json` into your `claude_desktop_config.json`. It registers the official read-only server and shopclaw side by side. Check the official repo's README for its current environment variables, they can change.

**Claude Code.**

```bash
claude mcp add shopclaw -- /ABSOLUTE/PATH/TO/shopclaw/.venv/bin/python -m shopclaw
claude mcp add google-ads-official -- pipx run --spec git+https://github.com/googleads/google-ads-mcp.git google-ads-mcp
```

Credentials are read from the environment or from `.env` in the working directory the server starts in. Passing them in the MCP `env` block is the most reliable option.

**Using a google-ads.yaml instead.** Set `SHOPCLAW_GOOGLE_ADS_YAML=/absolute/path/to/google-ads.yaml` and skip the individual variables.

## Example prompts

- "In account 123-456-7890, find search terms with more than 50 spent and no conversions in the last 30 days, then stage them as negative keywords on the top three campaigns." (reads through the official server, writes through shopclaw)
- "Create a Search campaign called 'Brand | US' with a 20 per day budget, geo 2840, language 1000, keywords [acme coffee] and 'acme', final URL https://example.com, and these headlines and descriptions..."
- "Raise the budget of campaign 'Brand | US' to 30."
- "Enable campaign 222 after I confirm the preview."

## Looking up IDs

Geo and language IDs come from Google's constants. Common examples, verify before relying on them:

| Geo | ID | Language | ID |
|---|---|---|---|
| United States | 2840 | English | 1000 |
| United Kingdom | 2826 | German | 1001 |
| Germany | 2276 | French | 1002 |
| Spain | 2724 | Spanish | 1003 |
| Finland | 2246 | Finnish | 1011 |
| United Arab Emirates | 2784 | Arabic | 1019 |

To look up any other location through the server:

```
gaql_read(customer_id, "SELECT geo_target_constant.id, geo_target_constant.name, geo_target_constant.country_code FROM geo_target_constant WHERE geo_target_constant.name = 'Spain'")
```

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

The tests run fully offline. Operations are built with the real Google Ads protobuf types and the API call is mocked.

## Status and limits

- Version 0.1. The operation builders and the safety flow are covered by offline tests. Run the first real change against a test or low-risk account and look at the `validate_only` result before trusting it with client money.
- Search campaigns only for now. Performance Max, Shopping, Demand Gen, asset groups and Customer Match are not covered yet.
- Campaign level negative keywords only. Shared negative lists are not covered yet.
- Pending changes live in memory. Restarting the server drops them.
- Google can change API versions and required fields. Pin `SHOPCLAW_API_VERSION` if you need stable behavior.

## License

MIT. See `LICENSE`.
