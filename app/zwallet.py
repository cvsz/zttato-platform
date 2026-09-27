"""Server-side client for the bounded ZWallet invoice-intent API."""

import asyncio
import json
import secrets
from typing import Any

import httpx


class ZWalletError(Exception):
    """Base class for safe-to-map ZWallet integration failures."""


class ZWalletNotConfigured(ZWalletError):
    pass


class ZWalletTimedOut(ZWalletError):
    pass


class ZWalletUnavailable(ZWalletError):
    pass


class ZWalletInvalidResponse(ZWalletError):
    pass


class ZWalletBillingClient:
    MAX_ATTEMPTS = 3
    MAX_RESPONSE_BYTES = 64 * 1024

    def __init__(self, adapter_url: str, service_token: str):
        self.adapter_url = adapter_url.rstrip("/")
        self.service_token = service_token
        self.transport: httpx.AsyncBaseTransport | None = None

    @property
    def configured(self) -> bool:
        return bool(self.adapter_url and self.service_token and not self.service_token.startswith("REPLACE_"))

    async def create_invoice_intent(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        currency: str,
        amount_minor: int,
    ) -> dict[str, Any]:
        if not self.configured:
            raise ZWalletNotConfigured

        payload = {
            "tenant_id": tenant_id,
            "idempotency_key": idempotency_key,
            "currency": currency,
            "amount_minor": amount_minor,
        }
        headers = {
            "Authorization": f"Bearer {self.service_token}",
            "Idempotency-Key": idempotency_key,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        timeout = httpx.Timeout(2.0, connect=1.0)
        endpoint = f"{self.adapter_url}/api/invoice-intents"

        try:
            async with asyncio.timeout(7.0):
                async with httpx.AsyncClient(
                    timeout=timeout,
                    transport=self.transport,
                    follow_redirects=False,
                ) as client:
                    for attempt in range(self.MAX_ATTEMPTS):
                        try:
                            async with client.stream("POST", endpoint, json=payload, headers=headers) as response:
                                retryable_status = (
                                    response.status_code in (408, 425, 429) or response.status_code >= 500
                                )
                                if retryable_status:
                                    retry_response = True
                                    if attempt + 1 == self.MAX_ATTEMPTS:
                                        raise ZWalletUnavailable
                                elif response.status_code not in (200, 201):
                                    raise ZWalletInvalidResponse
                                else:
                                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                                    content_encoding = response.headers.get("content-encoding", "identity").lower()
                                    if content_type != "application/json" or content_encoding not in ("", "identity"):
                                        raise ZWalletInvalidResponse
                                    retry_response = False
                                    response_body = await self._read_bounded_response(response)
                        except httpx.TimeoutException as exc:
                            if attempt + 1 == self.MAX_ATTEMPTS:
                                raise ZWalletTimedOut from exc
                            await self._backoff(attempt)
                            continue
                        except httpx.TransportError as exc:
                            if attempt + 1 == self.MAX_ATTEMPTS:
                                raise ZWalletUnavailable from exc
                            await self._backoff(attempt)
                            continue

                        if retry_response:
                            await self._backoff(attempt)
                            continue
                        try:
                            data = json.loads(response_body)
                        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                            raise ZWalletInvalidResponse from exc
                        return self._validate_response(data, payload)
        except TimeoutError as exc:
            raise ZWalletTimedOut from exc

        raise ZWalletUnavailable

    @staticmethod
    async def _backoff(attempt: int) -> None:
        delay = min(0.05 * (2**attempt) + secrets.randbelow(50_001) / 1_000_000, 0.25)
        await asyncio.sleep(delay)

    @classmethod
    async def _read_bounded_response(cls, response: httpx.Response) -> bytes:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > cls.MAX_RESPONSE_BYTES:
                raise ZWalletInvalidResponse
            body.extend(chunk)
        return bytes(body)

    @staticmethod
    def _validate_response(data: Any, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ZWalletInvalidResponse
        intent_id = data.get("intent_id")
        returned_tenant = data.get("tenant_id")
        returned_key = data.get("idempotency_key")
        returned_currency = data.get("currency")
        returned_amount = data.get("amount_minor")
        status = data.get("status")
        created_at = data.get("created_at")
        if (
            not isinstance(intent_id, str)
            or not intent_id
            or len(intent_id) > 128
            or returned_tenant != request["tenant_id"]
            or returned_key != request["idempotency_key"]
            or returned_currency != request["currency"]
            or type(returned_amount) is not int
            or returned_amount != request["amount_minor"]
            or status != "requires_payment_processor"
            or not isinstance(created_at, str)
            or not created_at
            or len(created_at) > 64
        ):
            raise ZWalletInvalidResponse
        return {
            "intent_id": intent_id,
            "currency": returned_currency,
            "amount_minor": returned_amount,
            "status": status,
            "created_at": created_at,
        }
