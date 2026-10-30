# Webhook Metrics

| Metric | Meaning |
| --- | --- |
| `webhook_requests_total` | HTTP request attempts, including retries |
| `webhook_success_total` | Successful 2xx responses |
| `webhook_failed_total` | Permanent or retryable failed outcomes |
| `webhook_unknown_total` | Ambiguous outcomes awaiting reconciliation |
| `webhook_replayed_total` | Outbox events replayed into operations |
| `webhook_duplicate_prevented_total` | Duplicate materialization/response prevented |
| `webhook_latency_seconds` | HTTP request duration in seconds |

Metrics contain no URLs, payloads, credentials, or customer identifiers. HTTP request outcomes, replay, and duplicate prevention are emitted by their lifecycle components.
