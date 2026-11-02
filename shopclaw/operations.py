"""Pure builders for Google Ads mutate operations.

Nothing in this module talks to the network. Every function takes a
GoogleAdsClient (only used for protobuf types, enums and resource name
helpers) and returns a list of MutateOperation messages that can be sent
through GoogleAdsService.mutate, either as validate_only or for real.

Design rules:
  * New campaigns are always created PAUSED.
  * There are no remove operations.
  * Geo targeting is mandatory for new campaigns, so nothing ever goes
    out to "all countries" by accident.
"""

from __future__ import annotations

import secrets
from typing import Any, List, Optional, Sequence, Tuple

from google.api_core import protobuf_helpers

MICROS = 1_000_000
MATCH_TYPES = ("BROAD", "PHRASE", "EXACT")
BIDDING_STRATEGIES = ("MANUAL_CPC", "MAXIMIZE_CLICKS", "MAXIMIZE_CONVERSIONS")
CAMPAIGN_STATUSES = ("ENABLED", "PAUSED")

BUDGET_TMP = -1
CAMPAIGN_TMP = -2
AD_GROUP_TMP = -3


class ValidationError(ValueError):
    """Raised when user supplied input is invalid."""


def to_micros(amount: float) -> int:
    return int(round(float(amount) * MICROS))


def parse_keyword(raw: str, default_match: str = "PHRASE") -> Tuple[str, str]:
    """Parse keyword syntax.

    [text]  -> EXACT
    "text"  -> PHRASE
    text    -> default_match
    """
    text = (raw or "").strip()
    if not text:
        raise ValidationError("Empty keyword.")
    match = default_match.upper()
    if match not in MATCH_TYPES:
        raise ValidationError(f"Match type must be one of {MATCH_TYPES}, got {default_match!r}.")
    if len(text) >= 2 and text[0] == "[" and text[-1] == "]":
        text, match = text[1:-1].strip(), "EXACT"
    elif len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text, match = text[1:-1].strip(), "PHRASE"
    if not text:
        raise ValidationError(f"Empty keyword in {raw!r}.")
    if len(text) > 80:
        raise ValidationError(f"Keyword too long (max 80 characters): {text[:30]}...")
    return text, match


def parse_keywords(raw: Sequence[str], default_match: str = "PHRASE") -> List[Tuple[str, str]]:
    seen = set()
    result = []
    for item in raw:
        parsed = parse_keyword(item, default_match)
        key = (parsed[0].lower(), parsed[1])
        if key not in seen:
            seen.add(key)
            result.append(parsed)
    return result


def validate_final_url(url: str) -> str:
    url = (url or "").strip()
    if not (url.startswith("https://") or url.startswith("http://")):
        raise ValidationError("final_url must start with http:// or https://")
    return url


def validate_rsa_text(
    headlines: Sequence[str],
    descriptions: Sequence[str],
    path1: Optional[str] = None,
    path2: Optional[str] = None,
) -> None:
    if not 3 <= len(headlines) <= 15:
        raise ValidationError("A responsive search ad needs 3 to 15 headlines.")
    if not 2 <= len(descriptions) <= 4:
        raise ValidationError("A responsive search ad needs 2 to 4 descriptions.")
    for h in headlines:
        if not h.strip() or len(h) > 30:
            raise ValidationError(f"Headline must be 1 to 30 characters: {h!r}")
    for d in descriptions:
        if not d.strip() or len(d) > 90:
            raise ValidationError(f"Description must be 1 to 90 characters: {d!r}")
    if len({h.strip().lower() for h in headlines}) != len(headlines):
        raise ValidationError("Headlines must be unique.")
    for p in (path1, path2):
        if p and len(p) > 15:
            raise ValidationError(f"Display path must be at most 15 characters: {p!r}")
    if path2 and not path1:
        raise ValidationError("path2 requires path1.")


def _enum(client: Any, enum_name: str, value: str) -> Any:
    return getattr(getattr(client.enums, enum_name), value)


def _text_asset(client: Any, text: str) -> Any:
    asset = client.get_type("AdTextAsset")
    asset.text = text.strip()
    return asset


# ---------------------------------------------------------------------------
# Individual operation builders
# ---------------------------------------------------------------------------


def _keyword_ops(
    client: Any, customer_id: str, ad_group_rn: str, keywords: Sequence[Tuple[str, str]]
) -> List[Any]:
    ops = []
    for text, match in keywords:
        op = client.get_type("MutateOperation")
        crit = op.ad_group_criterion_operation.create
        crit.ad_group = ad_group_rn
        crit.status = _enum(client, "AdGroupCriterionStatusEnum", "ENABLED")
        crit.keyword.text = text
        crit.keyword.match_type = _enum(client, "KeywordMatchTypeEnum", match)
        ops.append(op)
    return ops


def _negative_keyword_ops(
    client: Any, campaign_rn: str, keywords: Sequence[Tuple[str, str]]
) -> List[Any]:
    ops = []
    for text, match in keywords:
        op = client.get_type("MutateOperation")
        crit = op.campaign_criterion_operation.create
        crit.campaign = campaign_rn
        crit.negative = True
        crit.keyword.text = text
        crit.keyword.match_type = _enum(client, "KeywordMatchTypeEnum", match)
        ops.append(op)
    return ops


def _rsa_op(
    client: Any,
    ad_group_rn: str,
    final_url: str,
    headlines: Sequence[str],
    descriptions: Sequence[str],
    path1: Optional[str],
    path2: Optional[str],
) -> Any:
    op = client.get_type("MutateOperation")
    ad_group_ad = op.ad_group_ad_operation.create
    ad_group_ad.ad_group = ad_group_rn
    ad_group_ad.status = _enum(client, "AdGroupAdStatusEnum", "ENABLED")
    ad = ad_group_ad.ad
    ad.final_urls.append(final_url)
    rsa = ad.responsive_search_ad
    rsa.headlines.extend(_text_asset(client, h) for h in headlines)
    rsa.descriptions.extend(_text_asset(client, d) for d in descriptions)
    if path1:
        rsa.path1 = path1
    if path2:
        rsa.path2 = path2
    return op


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------


def build_create_search_campaign(
    client: Any,
    customer_id: str,
    *,
    campaign_name: str,
    daily_budget: float,
    ad_group_name: str,
    final_url: str,
    headlines: Sequence[str],
    descriptions: Sequence[str],
    keywords: Sequence[str],
    geo_target_ids: Sequence[int],
    negative_keywords: Optional[Sequence[str]] = None,
    language_ids: Optional[Sequence[int]] = None,
    bidding: str = "MAXIMIZE_CLICKS",
    cpc_bid: Optional[float] = None,
    default_match_type: str = "PHRASE",
    include_search_partners: bool = False,
    path1: Optional[str] = None,
    path2: Optional[str] = None,
) -> List[Any]:
    """Budget + campaign + targeting + ad group + keywords + one RSA, in one atomic request."""
    campaign_name = (campaign_name or "").strip()
    if not campaign_name:
        raise ValidationError("campaign_name is required.")
    if not geo_target_ids:
        raise ValidationError(
            "geo_target_ids is required. Without it the campaign would target every country."
        )
    bidding = bidding.upper()
    if bidding not in BIDDING_STRATEGIES:
        raise ValidationError(f"bidding must be one of {BIDDING_STRATEGIES}.")
    if bidding == "MANUAL_CPC" and not cpc_bid:
        raise ValidationError("cpc_bid is required for MANUAL_CPC.")

    final_url = validate_final_url(final_url)
    validate_rsa_text(headlines, descriptions, path1, path2)
    kw = parse_keywords(keywords, default_match_type)
    if not kw:
        raise ValidationError("At least one keyword is required.")
    neg = parse_keywords(negative_keywords or [], "PHRASE")

    budget_svc = client.get_service("CampaignBudgetService")
    campaign_svc = client.get_service("CampaignService")
    ad_group_svc = client.get_service("AdGroupService")
    # Path helpers for constants live on GoogleAdsService (there is no separate language service).
    geo_svc = client.get_service("GoogleAdsService")
    lang_svc = geo_svc

    budget_rn = budget_svc.campaign_budget_path(customer_id, BUDGET_TMP)
    campaign_rn = campaign_svc.campaign_path(customer_id, CAMPAIGN_TMP)
    ad_group_rn = ad_group_svc.ad_group_path(customer_id, AD_GROUP_TMP)

    ops: List[Any] = []

    # 1. Budget (names must be unique inside an account)
    op = client.get_type("MutateOperation")
    budget = op.campaign_budget_operation.create
    budget.resource_name = budget_rn
    budget.name = f"{campaign_name} budget {secrets.token_hex(3)}"
    budget.amount_micros = to_micros(daily_budget)
    budget.delivery_method = _enum(client, "BudgetDeliveryMethodEnum", "STANDARD")
    budget.explicitly_shared = False
    ops.append(op)

    # 2. Campaign, always PAUSED
    op = client.get_type("MutateOperation")
    campaign = op.campaign_operation.create
    campaign.resource_name = campaign_rn
    campaign.name = campaign_name
    campaign.status = _enum(client, "CampaignStatusEnum", "PAUSED")
    campaign.advertising_channel_type = _enum(client, "AdvertisingChannelTypeEnum", "SEARCH")
    campaign.campaign_budget = budget_rn
    campaign.network_settings.target_google_search = True
    campaign.network_settings.target_search_network = bool(include_search_partners)
    campaign.network_settings.target_content_network = False
    campaign.geo_target_type_setting.positive_geo_target_type = _enum(
        client, "PositiveGeoTargetTypeEnum", "PRESENCE"
    )
    campaign.contains_eu_political_advertising = _enum(
        client,
        "EuPoliticalAdvertisingStatusEnum",
        "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
    )
    if bidding == "MANUAL_CPC":
        client.copy_from(campaign.manual_cpc, client.get_type("ManualCpc"))
    elif bidding == "MAXIMIZE_CONVERSIONS":
        client.copy_from(campaign.maximize_conversions, client.get_type("MaximizeConversions"))
    else:
        client.copy_from(campaign.target_spend, client.get_type("TargetSpend"))
    ops.append(op)

    # 3. Campaign targeting
    for geo_id in geo_target_ids:
        op = client.get_type("MutateOperation")
        crit = op.campaign_criterion_operation.create
        crit.campaign = campaign_rn
        crit.location.geo_target_constant = geo_svc.geo_target_constant_path(int(geo_id))
        ops.append(op)
    for lang_id in language_ids or []:
        op = client.get_type("MutateOperation")
        crit = op.campaign_criterion_operation.create
        crit.campaign = campaign_rn
        crit.language.language_constant = lang_svc.language_constant_path(int(lang_id))
        ops.append(op)
    ops.extend(_negative_keyword_ops(client, campaign_rn, neg))

    # 4. Ad group
    op = client.get_type("MutateOperation")
    ad_group = op.ad_group_operation.create
    ad_group.resource_name = ad_group_rn
    ad_group.name = (ad_group_name or campaign_name).strip()
    ad_group.campaign = campaign_rn
    ad_group.status = _enum(client, "AdGroupStatusEnum", "ENABLED")
    ad_group.type_ = _enum(client, "AdGroupTypeEnum", "SEARCH_STANDARD")
    if bidding == "MANUAL_CPC":
        ad_group.cpc_bid_micros = to_micros(cpc_bid)
    ops.append(op)

    # 5. Keywords and the ad
    ops.extend(_keyword_ops(client, customer_id, ad_group_rn, kw))
    ops.append(_rsa_op(client, ad_group_rn, final_url, headlines, descriptions, path1, path2))
    return ops


def build_add_keywords(
    client: Any,
    customer_id: str,
    *,
    ad_group_id: int,
    keywords: Sequence[str],
    default_match_type: str = "PHRASE",
) -> List[Any]:
    kw = parse_keywords(keywords, default_match_type)
    if not kw:
        raise ValidationError("At least one keyword is required.")
    ad_group_rn = client.get_service("AdGroupService").ad_group_path(customer_id, int(ad_group_id))
    return _keyword_ops(client, customer_id, ad_group_rn, kw)


def build_add_negative_keywords(
    client: Any,
    customer_id: str,
    *,
    campaign_id: int,
    keywords: Sequence[str],
    default_match_type: str = "PHRASE",
) -> List[Any]:
    kw = parse_keywords(keywords, default_match_type)
    if not kw:
        raise ValidationError("At least one negative keyword is required.")
    campaign_rn = client.get_service("CampaignService").campaign_path(customer_id, int(campaign_id))
    return _negative_keyword_ops(client, campaign_rn, kw)


def build_create_responsive_search_ad(
    client: Any,
    customer_id: str,
    *,
    ad_group_id: int,
    final_url: str,
    headlines: Sequence[str],
    descriptions: Sequence[str],
    path1: Optional[str] = None,
    path2: Optional[str] = None,
) -> List[Any]:
    final_url = validate_final_url(final_url)
    validate_rsa_text(headlines, descriptions, path1, path2)
    ad_group_rn = client.get_service("AdGroupService").ad_group_path(customer_id, int(ad_group_id))
    return [_rsa_op(client, ad_group_rn, final_url, headlines, descriptions, path1, path2)]


def build_update_budget(
    client: Any, customer_id: str, *, budget_id: int, daily_amount: float
) -> List[Any]:
    op = client.get_type("MutateOperation")
    budget = op.campaign_budget_operation.update
    budget.resource_name = client.get_service("CampaignBudgetService").campaign_budget_path(
        customer_id, int(budget_id)
    )
    budget.amount_micros = to_micros(daily_amount)
    client.copy_from(
        op.campaign_budget_operation.update_mask, protobuf_helpers.field_mask(None, budget._pb)
    )
    return [op]


def build_update_campaign_status(
    client: Any, customer_id: str, *, campaign_id: int, status: str
) -> List[Any]:
    status = status.upper()
    if status not in CAMPAIGN_STATUSES:
        raise ValidationError(
            f"status must be one of {CAMPAIGN_STATUSES}. Removing campaigns is not supported."
        )
    op = client.get_type("MutateOperation")
    campaign = op.campaign_operation.update
    campaign.resource_name = client.get_service("CampaignService").campaign_path(
        customer_id, int(campaign_id)
    )
    campaign.status = _enum(client, "CampaignStatusEnum", status)
    client.copy_from(
        op.campaign_operation.update_mask, protobuf_helpers.field_mask(None, campaign._pb)
    )
    return [op]
