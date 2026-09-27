# zWallet invoice-intent integration

## Scope

zTTato has an optional server-side client for the zWallet Adapter's
`POST /api/invoice-intents` endpoint. The client creates an invoice intent only.
It does not read or change balances, record usage, confirm payment, or gate TikTok
publishing. The returned `requires_payment_processor` status is not proof of
payment or credit.

The integration stays disabled unless both `ZWALLET_ADAPTER_URL` and
`Z_PLATFORM_SERVICE_TOKEN` are configured. Supply the token through the runtime
secret store; do not put a real value in `.env.example`, source control, or logs.
Production adapter URLs must use HTTPS unless they point to loopback.

## zTTato API

`POST /api/billing/invoice-intents` requires an active zTTato browser session and
the matching `X-CSRF-Token` header. Its JSON body is:

```json
{
  "idempotency_key": "a-unique-key-for-this-intent",
  "currency": "USD",
  "amount_minor": 500
}
```

`idempotency_key` must be 16–128 ASCII letters, digits, dots, underscores,
colons, or hyphens. A retry of the same logical request must reuse the same key.
`amount_minor` is a positive integer; `currency` is a three-letter uppercase
code.

The client derives `tenant_id` from the server-side browser-session digest. It
does not accept a tenant ID from the browser and does not return that ID. This
is session-level scoping, not a durable Workspace/Tenant model: a new browser
session receives a different billing identity.

The server sends a Bearer token and the idempotency key to the configured adapter.
It makes at most three attempts with bounded timeouts and backoff. It rejects
redirects and validates that the adapter response matches the tenant, key,
currency, amount, and `requires_payment_processor` status. Error responses do not
include the adapter body or service token.

Successful responses contain only:

```json
{
  "intent_id": "intent-123",
  "currency": "USD",
  "amount_minor": 500,
  "status": "requires_payment_processor",
  "created_at": "2026-09-27T12:00:00Z"
}
```

## Readiness boundary

Mocked tests verify request scoping, CSRF, idempotency forwarding, retry bounds,
and response validation. They do not prove a live zWallet Adapter, billing
ledger, payment processor, or settlement flow. Do not use `/api/credits` to
grant a user balance. Do not connect invoice-intent creation to publishing until
the billing contract adds an authoritative balance check, idempotent usage
recording, and a defined payment/credit settlement flow.
