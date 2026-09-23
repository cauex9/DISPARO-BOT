from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from database import get_connection, row_dict

PROVIDER = "poseidonpay"
PLAN_MONTHLY = "monthly"
PLAN_AMOUNT = 15
ACTIVE_STATUS = "ACTIVE"
INACTIVE_STATUS = "INACTIVE"
CANCELED_STATUS = "CANCELED"
PAID_EVENT = "TRANSACTION_PAID"
CREATED_EVENT = "TRANSACTION_CREATED"


def _parse_provider_datetime(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _period_is_current(row: Any) -> bool:
    period_end = row["current_period_end"]
    if not period_end:
        return True
    parsed = _parse_provider_datetime(period_end)
    if parsed is None:
        return False
    return datetime.fromisoformat(parsed) >= datetime.now(timezone.utc)


def has_active_subscription(user_id: int) -> bool:
    with get_connection() as connection:
        subscription = connection.execute(
            """SELECT status, current_period_end FROM subscriptions
               WHERE user_id = ? AND status = ?
               ORDER BY updated_at DESC, id DESC LIMIT 1""",
            (user_id, ACTIVE_STATUS),
        ).fetchone()
    return bool(subscription and _period_is_current(subscription))


def latest_subscription(user_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """SELECT id, provider, provider_subscription_id, transaction_id,
                      identifier, plan, amount, status, current_period_start,
                      current_period_end, created_at, updated_at
               FROM subscriptions WHERE user_id = ?
               ORDER BY updated_at DESC, id DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
    return row_dict(row)


def create_pending_subscription(user_id: int, identifier: str) -> int:
    with get_connection() as connection:
        if connection.dialect == "postgres":
            row = connection.execute(
                """INSERT INTO subscriptions
                   (user_id, provider, identifier, plan, amount, status)
                   VALUES (?, ?, ?, ?, ?, ?) RETURNING id""",
                (user_id, PROVIDER, identifier, PLAN_MONTHLY, PLAN_AMOUNT, INACTIVE_STATUS),
            ).fetchone()
            return row["id"]
        cursor = connection.execute(
            """INSERT INTO subscriptions
               (user_id, provider, identifier, plan, amount, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, PROVIDER, identifier, PLAN_MONTHLY, PLAN_AMOUNT, INACTIVE_STATUS),
        )
        return cursor.lastrowid


def update_subscription_from_creation(subscription_id: int, response: dict[str, Any]) -> None:
    provider_subscription = response.get("subscription") or {}
    with get_connection() as connection:
        connection.execute(
            """UPDATE subscriptions SET provider_subscription_id = ?, transaction_id = ?,
                      status = ?, webhook_token = ?, current_period_start = ?,
                      current_period_end = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (
                provider_subscription.get("id"),
                response.get("transactionId"),
                provider_subscription.get("status") or response.get("status") or INACTIVE_STATUS,
                response.get("webhookToken"),
                _parse_provider_datetime(provider_subscription.get("startAt")),
                _parse_provider_datetime(provider_subscription.get("nextChargeAt")),
                subscription_id,
            ),
        )


def mark_subscription_creation_failed(subscription_id: int) -> None:
    with get_connection() as connection:
        connection.execute(
            """UPDATE subscriptions SET status = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            ("FAILED", subscription_id),
        )


def _webhook_event_id(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def process_poseidon_webhook(payload: dict[str, Any]) -> tuple[str, str]:
    """Process only documented payment events; activation requires a valid PAID event."""
    event = payload.get("event")
    token = payload.get("token")
    transaction = payload.get("transaction") or {}
    provider_subscription = payload.get("subscription") or {}
    if not isinstance(transaction, dict) or not isinstance(provider_subscription, dict):
        return "invalid", "invalid_payload"
    if event not in {CREATED_EVENT, PAID_EVENT}:
        return "invalid", "unsupported_event"
    if not isinstance(token, str) or not token:
        return "unauthorized", "missing_token"

    transaction_id = transaction.get("id")
    client_identifier = transaction.get("clientIdentifier")
    provider_subscription_id = provider_subscription.get("id")
    if not any((transaction_id, client_identifier, provider_subscription_id)):
        return "invalid", "missing_correlation_id"

    event_id = _webhook_event_id(payload)
    with get_connection() as connection:
        matches = connection.execute(
            """SELECT id, user_id, identifier, transaction_id, provider_subscription_id,
                      plan, amount, status, webhook_token
               FROM subscriptions
               WHERE provider = ? AND
                     (transaction_id = ? OR provider_subscription_id = ? OR identifier = ?)""",
            (PROVIDER, transaction_id, provider_subscription_id, client_identifier),
        ).fetchall()
        if len(matches) != 1:
            return "not_found", "subscription_not_found"
        subscription = matches[0]
        if transaction_id and subscription["transaction_id"] != transaction_id:
            return "invalid", "transaction_mismatch"
        if provider_subscription_id and subscription["provider_subscription_id"] != provider_subscription_id:
            return "invalid", "subscription_mismatch"
        if client_identifier and subscription["identifier"] != client_identifier:
            return "invalid", "identifier_mismatch"
        if not hmac.compare_digest(str(token), str(subscription["webhook_token"] or "")):
            return "unauthorized", "invalid_token"

        duplicate = connection.execute(
            "SELECT id FROM subscription_events WHERE provider = ? AND provider_event_id = ?",
            (PROVIDER, event_id),
        ).fetchone()
        if duplicate:
            return "duplicate", "already_processed"

        if connection.dialect == "postgres":
            event_row = connection.execute(
                """INSERT INTO subscription_events(provider, provider_event_id, subscription_id, processed_at)
                   VALUES(?, ?, ?, CURRENT_TIMESTAMP) RETURNING id""",
                (PROVIDER, event_id, subscription["id"]),
            ).fetchone()
        else:
            event_cursor = connection.execute(
                """INSERT INTO subscription_events(provider, provider_event_id, subscription_id, processed_at)
                   VALUES(?, ?, ?, CURRENT_TIMESTAMP)""",
                (PROVIDER, event_id, subscription["id"]),
            )
            event_row = {"id": event_cursor.lastrowid}

        if event == CREATED_EVENT:
            return "accepted", "created_recorded"

        if subscription["plan"] != PLAN_MONTHLY:
            return "rejected", "wrong_plan"
        if transaction.get("amount") is not None:
            try:
                if float(transaction["amount"]) != float(PLAN_AMOUNT):
                    return "rejected", "wrong_amount"
            except (TypeError, ValueError):
                return "rejected", "invalid_amount"
        if provider_subscription.get("status") not in (None, ACTIVE_STATUS):
            return "rejected", "subscription_not_active"

        connection.execute(
            """UPDATE subscriptions SET status = ?, current_period_start = ?,
                      current_period_end = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (
                ACTIVE_STATUS,
                _parse_provider_datetime(provider_subscription.get("startAt")),
                _parse_provider_datetime(provider_subscription.get("nextChargeAt")),
                subscription["id"],
            ),
        )
        return "activated", str(event_row["id"])
