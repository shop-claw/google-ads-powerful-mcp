import pytest

from shopclaw import operations as ops
from tests.conftest import CID

HEADLINES = ["Buy Coffee Online", "Free Shipping Over 50", "Small Batch Roasted"]
DESCRIPTIONS = ["Fresh roasted beans shipped weekly.", "Order today and taste the difference."]


def _campaign_kwargs(**over):
    base = dict(
        campaign_name="Search | Brand",
        daily_budget=20,
        ad_group_name="Brand terms",
        final_url="https://example.com",
        headlines=HEADLINES,
        descriptions=DESCRIPTIONS,
        keywords=["acme coffee", "[acme]"],
        geo_target_ids=[2840],
    )
    base.update(over)
    return base


def kinds(operations):
    return [op._pb.WhichOneof("operation") for op in operations]


# ---- keyword parsing ------------------------------------------------------


def test_parse_keyword_syntax():
    assert ops.parse_keyword("[exact one]") == ("exact one", "EXACT")
    assert ops.parse_keyword('"phrase one"') == ("phrase one", "PHRASE")
    assert ops.parse_keyword("broad one", "BROAD") == ("broad one", "BROAD")


def test_parse_keywords_dedupes():
    out = ops.parse_keywords(["Shoes", "shoes", "[shoes]"], "PHRASE")
    assert out == [("Shoes", "PHRASE"), ("shoes", "EXACT")]


def test_parse_keyword_rejects_empty_and_bad_match():
    with pytest.raises(ops.ValidationError):
        ops.parse_keyword("  ")
    with pytest.raises(ops.ValidationError):
        ops.parse_keyword("x", "FUZZY")


# ---- RSA validation -------------------------------------------------------


def test_rsa_validation():
    ops.validate_rsa_text(HEADLINES, DESCRIPTIONS)
    with pytest.raises(ops.ValidationError):
        ops.validate_rsa_text(HEADLINES[:2], DESCRIPTIONS)
    with pytest.raises(ops.ValidationError):
        ops.validate_rsa_text(HEADLINES, DESCRIPTIONS[:1])
    with pytest.raises(ops.ValidationError):
        ops.validate_rsa_text(["x" * 31, "b", "c"], DESCRIPTIONS)
    with pytest.raises(ops.ValidationError):
        ops.validate_rsa_text(["a", "a", "b"], DESCRIPTIONS)
    with pytest.raises(ops.ValidationError):
        ops.validate_rsa_text(HEADLINES, DESCRIPTIONS, path2="deals")


# ---- create_search_campaign ----------------------------------------------


def test_create_search_campaign_structure(client):
    built = ops.build_create_search_campaign(
        client, CID, **_campaign_kwargs(negative_keywords=["free", "jobs"], language_ids=[1000])
    )
    k = kinds(built)
    assert k[0] == "campaign_budget_operation"
    assert k[1] == "campaign_operation"
    assert k.count("campaign_criterion_operation") == 1 + 1 + 2  # geo + language + 2 negatives
    assert k.count("ad_group_operation") == 1
    assert k.count("ad_group_criterion_operation") == 2
    assert k[-1] == "ad_group_ad_operation"


def test_campaign_is_paused_and_uses_temp_ids(client):
    built = ops.build_create_search_campaign(client, CID, **_campaign_kwargs())
    campaign = built[1].campaign_operation.create
    assert campaign.status == client.enums.CampaignStatusEnum.PAUSED
    assert campaign.resource_name == f"customers/{CID}/campaigns/-2"
    assert campaign.campaign_budget == f"customers/{CID}/campaignBudgets/-1"
    assert campaign.network_settings.target_content_network is False


def test_budget_micros_and_unique_name(client):
    a = ops.build_create_search_campaign(client, CID, **_campaign_kwargs(daily_budget=12.34))
    b = ops.build_create_search_campaign(client, CID, **_campaign_kwargs(daily_budget=12.34))
    ba, bb = a[0].campaign_budget_operation.create, b[0].campaign_budget_operation.create
    assert ba.amount_micros == 12_340_000
    assert ba.name != bb.name


@pytest.mark.parametrize(
    "bidding,field,extra",
    [
        ("MANUAL_CPC", "manual_cpc", {"cpc_bid": 1.5}),
        ("MAXIMIZE_CLICKS", "target_spend", {}),
        ("MAXIMIZE_CONVERSIONS", "maximize_conversions", {}),
    ],
)
def test_bidding_strategy_is_set(client, bidding, field, extra):
    built = ops.build_create_search_campaign(
        client, CID, **_campaign_kwargs(bidding=bidding, **extra)
    )
    campaign = built[1].campaign_operation.create
    assert campaign._pb.WhichOneof("campaign_bidding_strategy") == field


def test_manual_cpc_sets_ad_group_bid(client):
    built = ops.build_create_search_campaign(
        client, CID, **_campaign_kwargs(bidding="MANUAL_CPC", cpc_bid=1.25)
    )
    ad_group = [o for o in built if o._pb.WhichOneof("operation") == "ad_group_operation"][0]
    assert ad_group.ad_group_operation.create.cpc_bid_micros == 1_250_000


def test_geo_targets_are_mandatory(client):
    with pytest.raises(ops.ValidationError, match="geo_target_ids"):
        ops.build_create_search_campaign(client, CID, **_campaign_kwargs(geo_target_ids=[]))


def test_manual_cpc_requires_bid(client):
    with pytest.raises(ops.ValidationError, match="cpc_bid"):
        ops.build_create_search_campaign(client, CID, **_campaign_kwargs(bidding="MANUAL_CPC"))


def test_geo_resource_name(client):
    built = ops.build_create_search_campaign(client, CID, **_campaign_kwargs(geo_target_ids=[2840]))
    crit = [o for o in built if o._pb.WhichOneof("operation") == "campaign_criterion_operation"][0]
    assert crit.campaign_criterion_operation.create.location.geo_target_constant == (
        "geoTargetConstants/2840"
    )


def test_rsa_in_campaign(client):
    built = ops.build_create_search_campaign(client, CID, **_campaign_kwargs(path1="coffee"))
    ad = built[-1].ad_group_ad_operation.create.ad
    assert list(ad.final_urls) == ["https://example.com"]
    assert [h.text for h in ad.responsive_search_ad.headlines] == HEADLINES
    assert ad.responsive_search_ad.path1 == "coffee"


# ---- small builders -------------------------------------------------------


def test_add_keywords(client):
    built = ops.build_add_keywords(client, CID, ad_group_id=55, keywords=["a b", "[c d]"])
    assert len(built) == 2
    crit = built[1].ad_group_criterion_operation.create
    assert crit.ad_group == f"customers/{CID}/adGroups/55"
    assert crit.keyword.match_type == client.enums.KeywordMatchTypeEnum.EXACT


def test_add_negative_keywords(client):
    built = ops.build_add_negative_keywords(client, CID, campaign_id=77, keywords=["free"])
    crit = built[0].campaign_criterion_operation.create
    assert crit.negative is True
    assert crit.campaign == f"customers/{CID}/campaigns/77"


def test_update_budget_mask(client):
    built = ops.build_update_budget(client, CID, budget_id=9, daily_amount=30)
    op = built[0].campaign_budget_operation
    assert op.update.amount_micros == 30_000_000
    assert "amount_micros" in list(op.update_mask.paths)


def test_update_campaign_status(client):
    built = ops.build_update_campaign_status(client, CID, campaign_id=5, status="enabled")
    op = built[0].campaign_operation
    assert op.update.status == client.enums.CampaignStatusEnum.ENABLED
    assert "status" in list(op.update_mask.paths)


def test_removing_is_not_supported(client):
    with pytest.raises(ops.ValidationError):
        ops.build_update_campaign_status(client, CID, campaign_id=5, status="REMOVED")
