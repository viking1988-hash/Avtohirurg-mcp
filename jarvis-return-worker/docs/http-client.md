# HTTP Client Guide

The shared HTTP client sends JSON with a bounded timeout and does not follow redirects. It supports GET, POST, PUT, PATCH, and DELETE. Requests carry `X-Request-ID`; correlation IDs use `X-Correlation-ID`; durable delivery keys use `Idempotency-Key`.

The client returns bounded receipt metadata. It never logs URLs, credentials, request bodies, or response bodies. Structured logs include provider, method, request/correlation IDs, HTTP status, retry count, elapsed milliseconds, and outcome.

Redirects are returned without being followed, preventing credentials from crossing origins. Timeout is per request, capped at 30 seconds.
