"""Server level tests with the Google Ads API mocked out."""

import pytest

from shopclaw import server
from shopclaw.config import load_settings

CID = "123-456-7890"

CAMPAIGN_ARGS = dict(
    customer_id=CID,
    campaign_name="Search | Brand",
    daily_budget=20,
    final_url="https://example.com",
    headlines=["Buy Coffee Online", "Free Shipping Over 50", "Small Batch Roasted"],
    descriptions=["Fresh roasted beans shipped weekly.", "Order today and taste the difference."],
    keywords=["acme coffee", "[acme]"],
    geo_target_ids=[2840],
)


@pytest.fixture
def env(client, tmp_path, monkeypatch):
    calls = []

    def fake_mutate(customer_id, operations, validate_only):
        calls.append({"customer_id": customer_id, "n": len(operations), "validate_only": validate_only})
        resp = client.get_type("MutateGoogleAdsResponse")
        for field, rn in (
            ("campaign_budget_result", f"customers/{customer_id}/campaignBudgets/111"),
            ("campaign_result", f"customers/{customer_id}/campaigns/222"),
            ("ad_group_result", f"customers/{customer_id}/adGroups/333"),
        ):
            item = client.get_type("MutateOperationResponse")
            getattr(item, field).resource_name = rn
            resp.mutate_operation_responses.append(item)
        return resp

    def configure(**env_vars):
        base = {"SHOPCLAW_AUDIT_LOG": str(tmp_path / "audit.jsonl")}
        base.update(env_vars)
        return server.configure(load_settings(base))

    monkeypatch.setattr(server, "get_client", lambda settings: client)
    monkeypatch.setattr(server, "_mutate", fake_mutate)
    configure()
    return type("Env", (), {"calls": calls, "configure": staticmethod(configure), "tmp": tmp_path})


def test_stage_then_confirm(env):
    staged = server.create_search_campaign(**CAMPAIGN_ARGS)
    assert staged["status"] == "pending_confirmation"
    assert staged["validated_by_google"] is True
    assert staged["summary"]["status_after_creation"] == "PAUSED"
    # Only the validate_only call has happened so far.
    assert [c["validate_only"] for c in env.calls] == [True]

    pending = server.list_pending_changes()
    assert [p["change_id"] for p in pending["pending"]] == [staged["change_id"]]

    applied = server.confirm_change(staged["change_id"])
    assert applied["status"] == "applied"
    assert applied["created"]["campaign_id"] == "222"
    assert applied["created"]["budget_id"] == "111"
    assert applied["created"]["ad_group_id"] == "333"
    assert [c["validate_only"] for c in env.calls] == [True, False]
    assert env.calls[0]["customer_id"] == "1234567890"

    # Cannot be confirmed twice.
    again = server.confirm_change(staged["change_id"])
    assert again["status"] == "not_found"
    assert server.list_pending_changes()["pending"] == []


def test_validate_on_preview_can_be_disabled(env):
    env.configure(SHOPCLAW_VALIDATE_ON_PREVIEW="false")
    staged = server.create_search_campaign(**CAMPAIGN_ARGS)
    assert staged["validated_by_google"] is False
    assert env.calls == []


def test_cancel(env):
    staged = server.add_negative_keywords(CID, 222, ["free", "jobs"])
    assert staged["status"] == "pending_confirmation"
    assert server.cancel_change(staged["change_id"])["status"] == "cancelled"
    assert server.confirm_change(staged["change_id"])["status"] == "not_found"
    assert not any(c["validate_only"] is False for c in env.calls)


def test_budget_cap_blocks_before_anything_is_sent(env):
    env.configure(SHOPCLAW_MAX_DAILY_BUDGET="10")
    result = server.create_search_campaign(**CAMPAIGN_ARGS)
    assert result["status"] == "blocked"
    assert "cap" in result["error"]
    assert env.calls == []


def test_update_budget_is_capped(env):
    env.configure(SHOPCLAW_MAX_DAILY_BUDGET="50")
    assert server.update_budget(CID, 9, 500)["status"] == "blocked"
    assert server.update_budget(CID, 9, 30)["status"] == "pending_confirmation"


def test_read_only_mode_blocks_every_write_tool(env):
    env.configure(SHOPCLAW_READ_ONLY="true")
    assert server.create_search_campaign(**CAMPAIGN_ARGS)["status"] == "blocked"
    assert server.add_keywords(CID, 1, ["x"])["status"] == "blocked"
    assert server.add_negative_keywords(CID, 1, ["x"])["status"] == "blocked"
    assert server.update_campaign_status(CID, 1, "ENABLED")["status"] == "blocked"
    assert env.calls == []


def test_allowlist(env):
    env.configure(SHOPCLAW_ALLOWED_CUSTOMERS="999-999-9999")
    assert server.add_keywords(CID, 1, ["x"])["status"] == "blocked"


def test_validation_errors_are_returned_not_raised(env):
    bad = dict(CAMPAIGN_ARGS, geo_target_ids=[])
    result = server.create_search_campaign(**bad)
    assert result["status"] == "blocked"
    assert "geo_target_ids" in result["error"]


def test_bad_customer_id(env):
    assert server.add_keywords("123", 1, ["x"])["status"] == "blocked"


def test_removed_status_not_supported(env):
    assert server.update_campaign_status(CID, 1, "REMOVED")["status"] == "blocked"


def test_gaql_read_only_allows_select(env):
    for q in ("UPDATE campaign SET x=1", "SELECT campaign.id FROM campaign; DROP", "mutate"):
        assert server.gaql_read(CID, q)["status"] == "blocked"


def test_audit_log_records_lifecycle(env):
    staged = server.add_keywords(CID, 1, ["a b"])
    server.confirm_change(staged["change_id"])
    lines = (env.tmp / "audit.jsonl").read_text().strip().splitlines()
    assert '"event": "staged"' in lines[0]
    assert '"event": "confirmed"' in lines[1]


def test_api_failure_during_preview_is_reported(env, monkeypatch):
    class GoogleAdsException(Exception):
        class failure:  # noqa: N801
            errors = []

    def boom(*a, **k):
        raise GoogleAdsException("bad request")

    monkeypatch.setattr(server, "_mutate", boom)
    result = server.add_keywords(CID, 1, ["a b"])
    assert result["status"] == "api_error"
    assert server.list_pending_changes()["pending"] == []


def test_tools_are_registered():
    import asyncio

    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert {
        "create_search_campaign", "add_keywords", "add_negative_keywords",
        "create_responsive_search_ad", "update_budget", "update_campaign_status",
        "confirm_change", "cancel_change", "list_pending_changes", "gaql_read",
    } <= names
    # No destructive tool is exposed.
    assert not any("remove" in n or "delete" in n for n in names)
