# Retry Strategy

Retries use bounded exponential backoff with jitter and a maximum retry count. `Retry-After` is honored for rate limits and capped at ten seconds.

Only GET is retried inside the HTTP client after network errors, HTTP 429, or server failures. An explicit HTTP 429 on a write is treated as a rate-limit rejection and may be rescheduled by the durable worker using Retry-After; the same call is never repeated inside the HTTP client. A write is never automatically repeated after timeout, connection failure, HTTP 408/409, or 5xx because the external effect may have occurred; the result becomes UNKNOWN and requires reconciliation. Other 4xx responses fail without retry.

GET retries are counted and logged. Durable idempotency keys protect deliveries. There is no worker-level redelivery after UNKNOWN.

## Outcome map

| HTTP/network result | GET | Write method |
| --- | --- | --- |
| 2xx | Success receipt | Success receipt |
| 429 | Retry within client, bounded by max retries | Durable retry using Retry-After or bounded default delay |
| 400, 401, 404, other 4xx | Permanent failure | Permanent failure |
| 408, 409, 5xx, timeout, connection reset | Safe GET retry within client, then retryable failure | UNKNOWN and reconciliation required |
