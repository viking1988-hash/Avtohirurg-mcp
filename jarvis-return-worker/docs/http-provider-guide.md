# Generic HTTP Provider (Sandbox)

`HTTPProvider` is a provider-neutral JSON transport for an explicitly configured
test API. It does not decide booking/message rules, approval, retry, or recovery.
The normal path remains Orchestrator → Policy Engine → Dispatcher → Worker →
Provider, with queue state and audit writes controlled by the Orchestrator.

## Enablement and endpoint guard

The provider is disabled unless both `JARVIS_HTTP_SANDBOX_URL` and
`JARVIS_HTTP_SANDBOX_TOKEN` are set. The URL must be HTTPS under
`*.up.railway.app`, include a hostname label containing `sandbox` or `staging`,
and must not contain a `prod`/`production` label. Redirects are refused so a
configured service cannot forward the bearer credential to a different host.
Timeouts are bounded to 1–30 seconds. Do not configure Production credentials,
arbitrary destinations, or endpoints outside the isolated Railway environment.

Supported request contract:

| Method | Request | Required response |
|---|---|---|
| `send` | `POST /messages`, destination and payload, `Idempotency-Key` | provider message ID and receipt |
| `update` | `PATCH /messages/{id}`, payload and `Idempotency-Key` | provider message ID and receipt |
| `cancel` | `POST /messages/{id}/cancel`, `Idempotency-Key` | 2xx response |
| `reconcile` / `checkStatus` | `GET /messages/{id}` | status and optional receipt |
| `get_receipt` | `GET /messages/{id}` | receipt fields or `None` |

The provider retains only IDs, receipt, status and delivery timestamp from the
response. It never logs request URLs, headers, bodies, response bodies or
credentials.

## Outcome classification and duplicate protection

HTTP 429 is a confirmed rejection (`RETRYABLE`) and includes a bounded
`Retry-After` when supplied. Other 4xx responses are `PERMANENT`. Network
timeouts, redirects, 5xx responses, unreadable responses, or a successful
mutation missing its message ID/receipt are `UNKNOWN`. The Orchestrator and
Policy Engine route UNKNOWN into `RECONCILIATION_REQUIRED`; the provider never
repeats a mutation. The stable queue idempotency key is forwarded as
`Idempotency-Key`; the remote service must honor it for safe network-level
replay. No automatic resend is permitted after an ambiguous result.

Metrics exposed on authenticated `/metrics`:

- `http_provider_requests_total`
- `http_provider_success_total`
- `http_provider_failed_total`
- `http_provider_unknown_total`
- `http_provider_latency_seconds`

## Testing and recovery

Unit tests use synthetic responses and an injected opener; they do not contact
an external API. An isolated endpoint can be smoke-tested only after it is
provisioned in Sandbox with test credentials. If an operation becomes
`RECONCILIATION_REQUIRED`, use the existing versioned reconciliation API and
record operator evidence. Do not requeue it until non-delivery has been
confirmed and the normal policy/approval flow authorizes a fresh attempt.

Rollback only the Sandbox worker revision. Keep the additive provider code and
any durable delivery/audit records; do not clear unresolved operations.
