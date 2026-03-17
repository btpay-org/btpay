# BTPay API Reference

Base URL: `/api/v1/`

All endpoints require authentication via API key passed as a Bearer token:

```
Authorization: Bearer YOUR_API_KEY
```

Create API keys in the web UI at **Settings > API Keys**.

## Invoices

### List Invoices

```
GET /api/v1/invoices
```

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status` | string | (all) | Filter by status: `draft`, `pending`, `partial`, `paid`, `confirmed`, `expired`, `cancelled` |
| `limit` | integer | 50 | Results per page (max 100) |
| `offset` | integer | 0 | Pagination offset |

**Response:**

```json
{
  "invoices": [
    {
      "ref": "A1B2C3D4-E5F6A7B8",
      "invoice_number": "INV-0001",
      "status": "pending",
      "customer_email": "alice@example.com",
      "customer_name": "Alice",
      "customer_company": "Acme Inc",
      "currency": "USD",
      "subtotal": "99.98",
      "tax_amount": "0",
      "discount_amount": "0",
      "total": "99.98",
      "amount_paid": "0",
      "btc_rate": "70862.71",
      "btc_amount": "0.00141126",
      "notes": "",
      "created_at": "2024-03-13T16:00:00+00:00",
      "lines": [
        {
          "description": "Widget",
          "quantity": "2",
          "unit_price": "49.99",
          "amount": "99.98"
        }
      ]
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### Create Invoice

```
POST /api/v1/invoices
Content-Type: application/json
```

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `lines` | array | yes | Line items (see below) |
| `customer_email` | string | no | Customer email |
| `customer_name` | string | no | Customer name |
| `customer_company` | string | no | Customer company |
| `currency` | string | no | Currency code (default: org default) |
| `notes` | string | no | Invoice notes |
| `tax_rate` | decimal | no | Tax rate as percentage (e.g. `10` for 10%) |
| `discount_amount` | decimal | no | Discount in currency units |
| `payment_methods` | array | no | Enabled methods: `["onchain_btc", "wire"]` |
| `metadata` | object | no | Arbitrary key-value metadata |

**Line Item Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `description` | string | yes | Item description |
| `quantity` | decimal | yes | Quantity |
| `unit_price` | decimal | yes | Price per unit |

**Example:**

```bash
curl -X POST http://localhost:5000/api/v1/invoices \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_email": "alice@example.com",
    "customer_name": "Alice",
    "customer_company": "Acme Inc",
    "currency": "USD",
    "tax_rate": 10,
    "lines": [
      {"description": "Consulting (1 hour)", "quantity": 1, "unit_price": "150.00"},
      {"description": "Hardware wallet setup", "quantity": 2, "unit_price": "25.00"}
    ],
    "notes": "Payment due within 30 days"
  }'
```

**Response (201):**

```json
{
  "ref": "A1B2C3D4-E5F6A7B8",
  "invoice_number": "INV-0001",
  "status": "draft",
  "total": "220.00",
  "lines": [...]
}
```

### Get Invoice

```
GET /api/v1/invoices/<ref>
```

The `<ref>` can be an invoice number (e.g. `INV-0001`), a reference number, or a numeric ID.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `include_payments` | boolean | false | Include payment records |

**Response:** Full invoice object. If `include_payments=true`, includes a `payments` array.

### Finalize Invoice

```
POST /api/v1/invoices/<ref>/finalize
```

Transitions a draft invoice to pending. This:
1. Assigns a fresh Bitcoin address from your wallet
2. Locks the current BTC exchange rate (valid for `BTC_QUOTE_DEADLINE` minutes)
3. Starts monitoring the address for payments

**Response:** Updated invoice object with `btc_amount`, `btc_rate`, and payment address.

**Errors:**
- `400` — Invoice is not a draft, or no active wallet configured

### Get Invoice Status

```
GET /api/v1/invoices/<ref>/status
```

Lightweight endpoint for polling payment status.

**Response:**

```json
{
  "invoice_number": "INV-0001",
  "status": "pending",
  "total": "220.00",
  "amount_paid": "0",
  "amount_due": "220.00",
  "currency": "USD"
}
```

### Cancel Invoice

```
DELETE /api/v1/invoices/<ref>
```

Cancels a draft or pending invoice. Releases any assigned Bitcoin address back to the pool.

**Response:**

```json
{
  "ok": true,
  "status": "cancelled"
}
```

## Payment Links

### List Payment Links

```
GET /api/v1/payment-links
```

**Response:**

```json
{
  "payment_links": [
    {
      "ref": "A1B2C3D4-E5F6A7B8",
      "slug": "donate",
      "title": "Donate",
      "description": "Support our project",
      "amount": null,
      "currency": "USD",
      "is_active": true,
      "created_at": "2024-03-13T16:00:00+00:00"
    }
  ]
}
```

### Create Payment Link

```
POST /api/v1/payment-links
Content-Type: application/json
```

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | yes | Link title |
| `slug` | string | no | URL slug (auto-generated from title if omitted) |
| `description` | string | no | Description shown to payer |
| `amount` | decimal | no | Fixed amount (null = payer chooses) |
| `currency` | string | no | Currency code |
| `payment_methods` | array | no | Enabled methods |
| `redirect_url` | string | no | URL to redirect after payment |
| `metadata` | object | no | Arbitrary metadata |

**Response (201):** Payment link object.

### Delete Payment Link

```
DELETE /api/v1/payment-links/<slug>
```

Deactivates the payment link (soft delete).

**Response:**

```json
{
  "ok": true
}
```

## Exchange Rates

### Get Current Rates

```
GET /api/v1/rates
```

Returns the latest BTC exchange rates from configured sources.

**Response:**

```json
{
  "rates": [
    {"currency": "USD", "rate": "70862.71"},
    {"currency": "EUR", "rate": "61919.68"},
    {"currency": "GBP", "rate": "53545.32"},
    {"currency": "CAD", "rate": "97583.60"}
  ]
}
```

## Webhooks

### List Webhook Endpoints

```
GET /api/v1/webhooks
```

**Response:**

```json
{
  "webhooks": [
    {
      "id": 1,
      "url": "https://yoursite.com/webhook",
      "events": ["invoice.paid", "invoice.confirmed"],
      "is_active": true,
      "description": "Production webhook"
    }
  ]
}
```

### Create Webhook Endpoint

```
POST /api/v1/webhooks
Content-Type: application/json
```

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | yes | HTTPS endpoint URL |
| `events` | array | no | Events to subscribe to (default: `["*"]`) |
| `description` | string | no | Description |

**Available Events:**

| Event | Description |
|-------|-------------|
| `invoice.created` | Invoice was created |
| `invoice.paid` | Invoice payment received (0-conf) |
| `invoice.confirmed` | Invoice payment reached required confirmations |
| `invoice.expired` | Invoice quote deadline passed |
| `invoice.cancelled` | Invoice was cancelled |
| `payment.received` | Bitcoin payment detected on address |
| `payment.confirmed` | Bitcoin payment confirmed |
| `*` | Subscribe to all events |

**Response (201):**

```json
{
  "id": 1,
  "url": "https://yoursite.com/webhook",
  "secret": "whsec_a1b2c3d4...",
  "events": ["invoice.paid", "invoice.confirmed"],
  "is_active": true
}
```

**Important:** The `secret` is only returned on creation. Save it immediately for signature verification.

### Delete Webhook Endpoint

```
DELETE /api/v1/webhooks/<id>
```

**Response:**

```json
{
  "ok": true
}
```

### Webhook Payload Format

All webhook deliveries include:

**Headers:**

| Header | Description |
|--------|-------------|
| `Content-Type` | `application/json` |
| `X-BTPay-Event` | Event name (e.g. `invoice.paid`) |
| `X-BTPay-Signature` | HMAC-SHA256 hex signature of the body |

**Body:**

```json
{
  "event": "invoice.paid",
  "data": {
    "invoice_number": "INV-0001",
    "status": "paid",
    "total": "220.00",
    "amount_paid": "220.00",
    "currency": "USD",
    "btc_amount": "0.00310521"
  },
  "timestamp": "2026-03-13T18:30:00Z"
}
```

### Verifying Webhook Signatures

```python
import hmac
import hashlib

def verify_webhook(body_bytes, signature, secret):
    expected = hmac.new(
        secret.encode('utf-8'),
        body_bytes,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

### Retry Policy

Failed deliveries are retried with exponential backoff:

| Attempt | Delay |
|---------|-------|
| 1 | 60 seconds |
| 2 | 5 minutes |
| 3 | 15 minutes |
| 4 | 1 hour |
| 5 | 2 hours |

A delivery is considered failed if it returns a non-2xx status code or times out.

## Error Responses

All errors return JSON:

```json
{
  "error": "Description of what went wrong"
}
```

**Common Status Codes:**

| Code | Meaning |
|------|---------|
| `400` | Bad request (missing/invalid parameters) |
| `401` | Unauthorized (missing or invalid API key) |
| `403` | Forbidden (insufficient permissions) |
| `404` | Not found |
| `429` | Rate limited (100 requests/minute for API) |

## Rate Limits

API endpoints are rate limited to **100 requests per minute** per API key. When rate limited, you'll receive a `429` response. Wait and retry.
