import json
import time

import pytest

from shopclaw.config import load_settings
from shopclaw import safety


def test_settings_defaults():
    s = load_settings({})
    assert s.read_only is False
    assert s.max_daily_budget == 100
    assert s.validate_on_preview is True
    assert s.allowed_customers == frozenset()


def test_settings_parsing():
    s = load_settings(
        {
            "SHOPCLAW_READ_ONLY": "true",
            "SHOPCLAW_MAX_DAILY_BUDGET": "25.5",
            "SHOPCLAW_ALLOWED_CUSTOMERS": "123-456-7890, 111-222-3333",
            "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "999-888-7777",
            "SHOPCLAW_PENDING_TTL_SECONDS": "60",
        }
    )
    assert s.read_only is True
    assert s.max_daily_budget == 25.5
    assert s.allowed_customers == frozenset({"1234567890", "1112223333"})
    assert s.login_customer_id == "9998887777"
    assert s.pending_ttl_seconds == 60


def test_normalize_customer_id():
    assert safety.normalize_customer_id("123-456-7890") == "1234567890"
    with pytest.raises(safety.SafetyError):
        safety.normalize_customer_id("12345")


def test_read_only_blocks_writes():
    s = load_settings({"SHOPCLAW_READ_ONLY": "true"})
    with pytest.raises(safety.SafetyError, match="read-only"):
        safety.check_writes_allowed(s, "1234567890")


def test_allowlist_blocks_other_accounts():
    s = load_settings({"SHOPCLAW_ALLOWED_CUSTOMERS": "1234567890"})
    safety.check_writes_allowed(s, "1234567890")
    with pytest.raises(safety.SafetyError, match="ALLOWED_CUSTOMERS"):
        safety.check_writes_allowed(s, "1112223333")


def test_budget_cap():
    s = load_settings({"SHOPCLAW_MAX_DAILY_BUDGET": "50"})
    safety.check_budget(s, 50)
    with pytest.raises(safety.SafetyError, match="cap"):
        safety.check_budget(s, 50.01)
    with pytest.raises(safety.SafetyError):
        safety.check_budget(s, 0)


def test_pending_store_roundtrip_and_expiry():
    store = safety.PendingStore(ttl_seconds=1)
    change = store.add("1234567890", "x", {"a": 1}, [object()])
    assert store.get(change.id) is change
    assert store.list() == [change]
    assert store.pop(change.id) is change
    assert store.get(change.id) is None

    old = store.add("1234567890", "x", {}, [])
    old.created_at = time.time() - 5
    assert store.get(old.id) is None


def test_audit_log_writes_json_lines(tmp_path):
    path = tmp_path / "nested" / "audit.jsonl"
    log = safety.AuditLog(path)
    log.write("staged", change_id="abc", customer_id="1234567890")
    log.write("confirmed", change_id="abc")
    lines = path.read_text().strip().splitlines()
    assert [json.loads(line)["event"] for line in lines] == ["staged", "confirmed"]
