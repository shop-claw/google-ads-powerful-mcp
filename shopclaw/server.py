"""shopclaw MCP server.

A write layer for Google Ads. Run it next to Google's official read-only
Google Ads MCP server: that one answers questions, this one makes changes.

Every write tool only STAGES a change and returns a preview. Nothing reaches
Google Ads until confirm_change is called with the change id.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from google.protobuf.json_format import MessageToDict
from mcp.server.fastmcp import FastMCP

from shopclaw import operations as ops
from shopclaw.client import ConfigError, get_client
from shopclaw.config import Settings, load_env_files, load_settings
from shopclaw.safety import (
    AuditLog,
    PendingStore,
    SafetyError,
    check_budget,
    check_writes_allowed,
    normalize_customer_id,
)

mcp = FastMCP("shopclaw")


@dataclass
class Context:
    settings: Settings
    store: PendingStore
    audit: AuditLog


_ctx_instance: Optional[Context] = None


def configure(settings: Settings) -> Context:
    """Build the shared context. Tests call this with their own settings."""
    global _ctx_instance
    _ctx_instance = Context(
        settings=settings,
        store=PendingStore(settings.pending_ttl_seconds),
        audit=AuditLog(settings.audit_log_path),
    )
    return _ctx_instance


def _ctx() -> Context:
    if _ctx_instance is None:
        load_env_files()
        configure(load_settings())
    return _ctx_instance  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_list(exc: Exception) -> List[str]:
    """Flatten a GoogleAdsException into readable lines."""
    failure = getattr(exc, "failure", None)
    lines: List[str] = []
    if failure is not None:
        for err in failure.errors:
            code = ""
            try:
                code = err.error_code.WhichOneof("error_code") or ""
                if code:
                    code = f"{code}.{getattr(err.error_code, code)}"
            except Exception:
                pass
            lines.append(f"[{code}] {err.message}" if code else err.message)
    return lines or [str(exc)]


def _safe(fn: Callable[..., Dict[str, Any]]) -> Callable[..., Dict[str, Any]]:
    """Turn expected failures into a readable error result instead of a stack trace."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except (SafetyError, ops.ValidationError, ConfigError) as exc:
            return {"status": "blocked", "error": str(exc)}
        except Exception as exc:  # GoogleAdsException and anything unexpected
            if exc.__class__.__name__ == "GoogleAdsException":
                return {"status": "api_error", "errors": _error_list(exc)}
            return {"status": "error", "error": f"{exc.__class__.__name__}: {exc}"}

    return wrapper


def _mutate(customer_id: str, operations: List[Any], validate_only: bool) -> Any:
    client = get_client(_ctx().settings)
    service = client.get_service("GoogleAdsService")
    return service.mutate(
        customer_id=customer_id,
        mutate_operations=operations,
        validate_only=validate_only,
    )


def _stage(
    kind: str, customer_id: str, summary: Dict[str, Any], operations: List[Any]
) -> Dict[str, Any]:
    ctx = _ctx()
    validated = False
    if ctx.settings.validate_on_preview:
        _mutate(customer_id, operations, validate_only=True)  # raises on API errors
        validated = True
    change = ctx.store.add(customer_id, kind, summary, operations, validated=validated)
    ctx.audit.write("staged", change_id=change.id, kind=kind, customer_id=customer_id, summary=summary)
    return {
        "status": "pending_confirmation",
        "change_id": change.id,
        "kind": kind,
        "customer_id": customer_id,
        "summary": summary,
        "operations": len(operations),
        "validated_by_google": validated,
        "expires_in_seconds": ctx.store.seconds_left(change),
        "next_step": (
            "Show this preview to the user. Only after they approve, call "
            f"confirm_change(change_id='{change.id}')."
        ),
    }


def _group_results(response: Any) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {}
    for item in response.mutate_operation_responses:
        which = item._pb.WhichOneof("response")
        if not which:
            continue
        resource_name = getattr(item, which).resource_name
        grouped.setdefault(which.replace("_result", ""), []).append(resource_name)
    return grouped


def _ids(grouped: Dict[str, List[str]], key: str) -> List[str]:
    return [name.rsplit("/", 1)[-1] for name in grouped.get(key, [])]


# ---------------------------------------------------------------------------
# Write tools: stage
# ---------------------------------------------------------------------------


@mcp.tool()
@_safe
def create_search_campaign(
    customer_id: str,
    campaign_name: str,
    daily_budget: float,
    final_url: str,
    headlines: List[str],
    descriptions: List[str],
    keywords: List[str],
    geo_target_ids: List[int],
    ad_group_name: Optional[str] = None,
    negative_keywords: Optional[List[str]] = None,
    language_ids: Optional[List[int]] = None,
    bidding: str = "MAXIMIZE_CLICKS",
    cpc_bid: Optional[float] = None,
    default_match_type: str = "PHRASE",
    include_search_partners: bool = False,
    path1: Optional[str] = None,
    path2: Optional[str] = None,
) -> Dict[str, Any]:
    """Stage a complete Search campaign: budget, campaign, geo and language targeting,
    one ad group, keywords and one responsive search ad, as a single atomic request.

    The campaign is always created PAUSED. Use update_campaign_status after review to enable it.
    Nothing is sent until confirm_change is called.

    Args:
        customer_id: Google Ads customer ID, dashes allowed.
        daily_budget: Daily budget in the account currency. Capped by SHOPCLAW_MAX_DAILY_BUDGET.
        headlines: 3 to 15 headlines, 30 characters max each.
        descriptions: 2 to 4 descriptions, 90 characters max each.
        keywords: Keyword syntax: [exact], "phrase", or plain text which uses default_match_type.
        geo_target_ids: Geo target constant IDs, for example 2840 for United States. Required.
        language_ids: Language constant IDs, for example 1000 for English. Optional.
        bidding: MAXIMIZE_CLICKS (default), MAXIMIZE_CONVERSIONS or MANUAL_CPC (needs cpc_bid).
        negative_keywords: Campaign level negatives, same syntax as keywords.
    """
    ctx = _ctx()
    cid = normalize_customer_id(customer_id)
    check_writes_allowed(ctx.settings, cid)
    check_budget(ctx.settings, daily_budget)
    client = get_client(ctx.settings)
    built = ops.build_create_search_campaign(
        client,
        cid,
        campaign_name=campaign_name,
        daily_budget=daily_budget,
        ad_group_name=ad_group_name or campaign_name,
        final_url=final_url,
        headlines=headlines,
        descriptions=descriptions,
        keywords=keywords,
        geo_target_ids=geo_target_ids,
        negative_keywords=negative_keywords,
        language_ids=language_ids,
        bidding=bidding,
        cpc_bid=cpc_bid,
        default_match_type=default_match_type,
        include_search_partners=include_search_partners,
        path1=path1,
        path2=path2,
    )
    summary = {
        "campaign": campaign_name,
        "status_after_creation": "PAUSED",
        "daily_budget": daily_budget,
        "bidding": bidding.upper(),
        "cpc_bid": cpc_bid,
        "geo_target_ids": geo_target_ids,
        "language_ids": language_ids or "all languages",
        "search_partners": include_search_partners,
        "ad_group": ad_group_name or campaign_name,
        "keywords": [f"{t} ({m})" for t, m in ops.parse_keywords(keywords, default_match_type)],
        "negative_keywords": [f"{t} ({m})" for t, m in ops.parse_keywords(negative_keywords or [], "PHRASE")],
        "final_url": final_url,
        "headlines": headlines,
        "descriptions": descriptions,
    }
    return _stage("create_search_campaign", cid, summary, built)


@mcp.tool()
@_safe
def add_keywords(
    customer_id: str,
    ad_group_id: int,
    keywords: List[str],
    default_match_type: str = "PHRASE",
) -> Dict[str, Any]:
    """Stage new keywords for an existing ad group.

    Keyword syntax: [exact], "phrase", or plain text which uses default_match_type.
    Find ad group IDs with gaql_read or the official read-only server.
    """
    ctx = _ctx()
    cid = normalize_customer_id(customer_id)
    check_writes_allowed(ctx.settings, cid)
    built = ops.build_add_keywords(
        get_client(ctx.settings), cid, ad_group_id=ad_group_id, keywords=keywords,
        default_match_type=default_match_type,
    )
    summary = {
        "ad_group_id": ad_group_id,
        "keywords": [f"{t} ({m})" for t, m in ops.parse_keywords(keywords, default_match_type)],
    }
    return _stage("add_keywords", cid, summary, built)


@mcp.tool()
@_safe
def add_negative_keywords(
    customer_id: str,
    campaign_id: int,
    keywords: List[str],
    default_match_type: str = "PHRASE",
) -> Dict[str, Any]:
    """Stage campaign level negative keywords.

    Typical use: take the wasted search terms from a search term report and block them.
    """
    ctx = _ctx()
    cid = normalize_customer_id(customer_id)
    check_writes_allowed(ctx.settings, cid)
    built = ops.build_add_negative_keywords(
        get_client(ctx.settings), cid, campaign_id=campaign_id, keywords=keywords,
        default_match_type=default_match_type,
    )
    summary = {
        "campaign_id": campaign_id,
        "negative_keywords": [f"{t} ({m})" for t, m in ops.parse_keywords(keywords, default_match_type)],
    }
    return _stage("add_negative_keywords", cid, summary, built)


@mcp.tool()
@_safe
def create_responsive_search_ad(
    customer_id: str,
    ad_group_id: int,
    final_url: str,
    headlines: List[str],
    descriptions: List[str],
    path1: Optional[str] = None,
    path2: Optional[str] = None,
) -> Dict[str, Any]:
    """Stage a responsive search ad in an existing ad group.

    3 to 15 headlines (30 characters max), 2 to 4 descriptions (90 characters max).
    """
    ctx = _ctx()
    cid = normalize_customer_id(customer_id)
    check_writes_allowed(ctx.settings, cid)
    built = ops.build_create_responsive_search_ad(
        get_client(ctx.settings), cid, ad_group_id=ad_group_id, final_url=final_url,
        headlines=headlines, descriptions=descriptions, path1=path1, path2=path2,
    )
    summary = {
        "ad_group_id": ad_group_id,
        "final_url": final_url,
        "headlines": headlines,
        "descriptions": descriptions,
    }
    return _stage("create_responsive_search_ad", cid, summary, built)


@mcp.tool()
@_safe
def update_budget(customer_id: str, budget_id: int, daily_amount: float) -> Dict[str, Any]:
    """Stage a new daily amount for an existing campaign budget.

    Capped by SHOPCLAW_MAX_DAILY_BUDGET. If the budget is shared, every campaign that uses it changes.
    Find the budget ID with: SELECT campaign.name, campaign_budget.id, campaign_budget.amount_micros FROM campaign
    """
    ctx = _ctx()
    cid = normalize_customer_id(customer_id)
    check_writes_allowed(ctx.settings, cid)
    check_budget(ctx.settings, daily_amount)
    built = ops.build_update_budget(
        get_client(ctx.settings), cid, budget_id=budget_id, daily_amount=daily_amount
    )
    return _stage(
        "update_budget", cid, {"budget_id": budget_id, "new_daily_amount": daily_amount}, built
    )


@mcp.tool()
@_safe
def update_campaign_status(customer_id: str, campaign_id: int, status: str) -> Dict[str, Any]:
    """Stage a campaign status change to ENABLED or PAUSED.

    Enabling a campaign starts spending money, so confirm only after the user has reviewed it.
    Removing campaigns is deliberately not supported.
    """
    ctx = _ctx()
    cid = normalize_customer_id(customer_id)
    check_writes_allowed(ctx.settings, cid)
    built = ops.build_update_campaign_status(
        get_client(ctx.settings), cid, campaign_id=campaign_id, status=status
    )
    return _stage(
        "update_campaign_status",
        cid,
        {"campaign_id": campaign_id, "new_status": status.upper()},
        built,
    )


# ---------------------------------------------------------------------------
# Confirm / cancel / list
# ---------------------------------------------------------------------------


@mcp.tool()
@_safe
def confirm_change(change_id: str) -> Dict[str, Any]:
    """Execute a staged change. Call this only after the user approved the preview."""
    ctx = _ctx()
    change = ctx.store.get(change_id)
    if change is None:
        return {
            "status": "not_found",
            "error": "Unknown or expired change_id. Stage the change again.",
        }
    check_writes_allowed(ctx.settings, change.customer_id)
    try:
        response = _mutate(change.customer_id, change.operations, validate_only=False)
    except Exception as exc:
        ctx.audit.write(
            "failed", change_id=change.id, kind=change.kind, customer_id=change.customer_id,
            error=str(exc)[:500],
        )
        raise
    ctx.store.pop(change_id)
    grouped = _group_results(response)
    ctx.audit.write(
        "confirmed", change_id=change.id, kind=change.kind, customer_id=change.customer_id,
        resources=grouped,
    )
    result: Dict[str, Any] = {
        "status": "applied",
        "change_id": change.id,
        "kind": change.kind,
        "resources": grouped,
    }
    if change.kind == "create_search_campaign":
        result["created"] = {
            "campaign_id": (_ids(grouped, "campaign") or [None])[0],
            "budget_id": (_ids(grouped, "campaign_budget") or [None])[0],
            "ad_group_id": (_ids(grouped, "ad_group") or [None])[0],
            "state": "PAUSED. Review it, then call update_campaign_status to enable.",
        }
    return result


@mcp.tool()
@_safe
def cancel_change(change_id: str) -> Dict[str, Any]:
    """Discard a staged change without sending anything to Google Ads."""
    ctx = _ctx()
    change = ctx.store.pop(change_id)
    if change is None:
        return {"status": "not_found", "error": "Unknown or expired change_id."}
    ctx.audit.write("cancelled", change_id=change.id, kind=change.kind, customer_id=change.customer_id)
    return {"status": "cancelled", "change_id": change.id}


@mcp.tool()
@_safe
def list_pending_changes() -> Dict[str, Any]:
    """List staged changes that are waiting for confirmation."""
    ctx = _ctx()
    return {
        "status": "ok",
        "pending": [
            {
                "change_id": c.id,
                "kind": c.kind,
                "customer_id": c.customer_id,
                "summary": c.summary,
                "expires_in_seconds": ctx.store.seconds_left(c),
            }
            for c in ctx.store.list()
        ],
    }


# ---------------------------------------------------------------------------
# Minimal read helper
# ---------------------------------------------------------------------------


@mcp.tool()
@_safe
def gaql_read(customer_id: str, query: str, max_rows: int = 200) -> Dict[str, Any]:
    """Run a read only GAQL SELECT query. Use it to look up campaign, ad group and budget IDs
    before staging a change. For analysis and reporting, use Google's official read-only server.
    """
    ctx = _ctx()
    cid = normalize_customer_id(customer_id)
    q = query.strip().rstrip(";").strip()
    if not re.match(r"(?is)^select\b", q) or ";" in q:
        raise SafetyError("Only a single GAQL SELECT query is allowed.")
    max_rows = max(1, min(int(max_rows), 1000))

    client = get_client(ctx.settings)
    service = client.get_service("GoogleAdsService")
    rows: List[Dict[str, Any]] = []
    for batch in service.search_stream(customer_id=cid, query=q):
        for row in batch.results:
            rows.append(MessageToDict(row._pb, preserving_proto_field_name=True))
            if len(rows) >= max_rows:
                return {"status": "ok", "rows": rows, "truncated": True}
    return {"status": "ok", "rows": rows, "truncated": False}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
