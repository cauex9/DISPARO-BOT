from __future__ import annotations

import os
import uuid
from typing import Any

import requests

from subscriptions import PLAN_AMOUNT

POSEIDON_SUBSCRIPTION_URL = "https://app.poseidonpay.site/api/v1/gateway/pix/subscription"


class PoseidonConfigurationError(RuntimeError):
    pass


class PoseidonPaymentError(RuntimeError):
    pass


def _required_configuration() -> tuple[str, str, str]:
    public_key = os.getenv("POSEIDO_CLIENT_ID", "").strip()
    secret_key = os.getenv("POSEIDO_CLIENT_SECRET", "").strip()
    callback_url = os.getenv("WEBHOOK_CALLBACK_URL", "").strip()
    missing = [
        name for name, value in (
            ("POSEIDO_CLIENT_ID", public_key),
            ("POSEIDO_CLIENT_SECRET", secret_key),
            ("WEBHOOK_CALLBACK_URL", callback_url),
        ) if not value
    ]
    if missing:
        raise PoseidonConfigurationError(f"Configuração PoseidonPay ausente: {', '.join(missing)}")
    return public_key, secret_key, callback_url


def create_monthly_subscription(user: dict[str, Any], identifier: str | None = None) -> tuple[str, dict[str, Any]]:
    public_key, secret_key, callback_url = _required_configuration()
    subscription_identifier = identifier or str(uuid.uuid4())
    payload = {
        "identifier": subscription_identifier,
        "amount": PLAN_AMOUNT,
        "client": {
            "name": user["name"],
            "email": user["email"],
        },
        "product": {
            "name": "Plano Mensal",
            "price": PLAN_AMOUNT,
        },
        "metadata": {
            "plan": "monthly",
            "userId": str(user["id"]),
        },
        "subscription": {
            "periodicityType": "MONTHS",
            "periodicity": 1,
            "firstChargeIn": 0,
        },
        "callbackUrl": callback_url,
    }
    try:
        response = requests.post(
            POSEIDON_SUBSCRIPTION_URL,
            headers={
                "x-public-key": public_key,
                "x-secret-key": secret_key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        raise PoseidonPaymentError("Não foi possível conectar à PoseidonPay.") from exc

    try:
        response_data = response.json()
    except ValueError as exc:
        raise PoseidonPaymentError("A PoseidonPay retornou uma resposta inválida.") from exc

    if response.status_code >= 400:
        detail = response_data.get("errorDescription") if isinstance(response_data, dict) else None
        raise PoseidonPaymentError(detail or "A PoseidonPay recusou a criação da assinatura.")
    if not isinstance(response_data, dict):
        raise PoseidonPaymentError("A PoseidonPay retornou dados inválidos.")
    return subscription_identifier, response_data
