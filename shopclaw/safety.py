"""Safety layer: guards, pending change store and audit log."""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from shopclaw.config import Settings


class SafetyError(Exception):
    """Raised when a request is blocked by a safety rule."""


def normalize_customer_id(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 10:
        raise SafetyError(
            f"Customer ID must have 10 digits (dashes are fine), got {value!r}."
        )
    return digits


def check_writes_allowed(settings: Settings, customer_id: str) -> None:
    if settings.read_only:
        raise SafetyError(
            "shopclaw is in read-only mode (SHOPCLAW_READ_ONLY=true). Writes are disabled."
        )
    if settings.allowed_customers and customer_id not in settings.allowed_customers:
        raise SafetyError(
            f"Customer {customer_id} is not in SHOPCLAW_ALLOWED_CUSTOMERS."
        )


def check_budget(settings: Settings, daily_amount: float) -> None:
    if daily_amount <= 0:
        raise SafetyError("Daily budget must be greater than zero.")
    if daily_amount > settings.max_daily_budget:
        raise SafetyError(
            f"Daily budget {daily_amount:g} is above the cap of "
            f"{settings.max_daily_budget:g} (SHOPCLAW_MAX_DAILY_BUDGET)."
        )


@dataclass
class PendingChange:
    id: str
    customer_id: str
    kind: str
    summary: Dict[str, Any]
    operations: List[Any]
    created_at: float
    validated: bool = False


class PendingStore:
    """In memory store of staged changes with a time to live."""

    def __init__(self, ttl_seconds: int = 900) -> None:
        self.ttl = ttl_seconds
        self._items: Dict[str, PendingChange] = {}

    def _purge(self) -> None:
        now = time.time()
        for key in [k for k, v in self._items.items() if now - v.created_at > self.ttl]:
            del self._items[key]

    def add(
        self,
        customer_id: str,
        kind: str,
        summary: Dict[str, Any],
        operations: List[Any],
        validated: bool = False,
    ) -> PendingChange:
        self._purge()
        change = PendingChange(
            id=secrets.token_hex(4),
            customer_id=customer_id,
            kind=kind,
            summary=summary,
            operations=operations,
            created_at=time.time(),
            validated=validated,
        )
        self._items[change.id] = change
        return change

    def get(self, change_id: str) -> Optional[PendingChange]:
        self._purge()
        return self._items.get(change_id)

    def pop(self, change_id: str) -> Optional[PendingChange]:
        self._purge()
        return self._items.pop(change_id, None)

    def list(self) -> List[PendingChange]:
        self._purge()
        return list(self._items.values())

    def seconds_left(self, change: PendingChange) -> int:
        return max(0, int(self.ttl - (time.time() - change.created_at)))


class AuditLog:
    """Append only JSON lines log."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, event: str, **data: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            **data,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError:
            # Auditing must never break the tool call itself.
            pass
