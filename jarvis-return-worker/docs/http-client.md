# HTTP Client Guide

The shared HTTP client sends JSON with a bounded timeout and does not follow redirects. It supports GET, POST, PUT, PATCH, and DELETE. Requests carry `X-Request-ID`; correlation IDs use `X-Correlation-ID`; durable delivery keys use `Idempotency-Key`.

The client returns bounded receipt metadata. It never logs URLs, credentials, request bodies, or response bodies. Structured logs include provider, method, request/correlation IDs, HTTP status, retry count, elapsed milliseconds, and outcome.

Redirects are returned without being followed, preventing credentials from crossing origins. Timeout is per request, capped at 30 seconds.

## DNS and connection safety

For each connection attempt, the client resolves the configured hostname once, validates every returned IPv4/IPv6 address, rejects the full answer set if any address is non-public, and connects directly to one of the validated socket addresses. TLS certificate verification and SNI continue to use the configured hostname. This closes the DNS rebinding window between a standalone check and the later socket lookup. Proxy environment variables are not used.

Loopback HTTP is allowed only when the provider was explicitly constructed with the local-test option; it is intended for synthetic tests. Private, loopback, link-local, shared, multicast, unspecified, and reserved destinations are rejected in normal operation.
