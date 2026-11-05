# Webhook Provider Guide

Webhook Provider delivers configured HTTP operations through Jarvis. An event stores a method, relative path, and JSON payload; destination origin and credentials come from worker configuration. Events cannot choose a host.

Paths start with one slash and reject query, fragment, backslash, and parent-directory segments. Production requires HTTPS. Keep credentials in Railway environment variables; never put them in event payloads.

## Lifecycle

1. Queue records a request through Orchestrator.
2. Policy Engine validates the operation.
3. Dispatcher selects the registered `webhook` provider.
4. Transactional Outbox persists `WebhookRequested`.
5. Replay Worker materializes a durable operation.
6. Webhook Worker claims it and invokes Orchestrator, which applies Policy and Dispatcher before the Provider.
7. Receipt and state are committed with lease fencing.

Providers do not call Queue or write storage. Registration uses the same Dispatcher interface as other providers.

## Configuration

Set `JARVIS_WEBHOOK_BASE_URL` to an HTTPS origin/prefix. Optional settings: `JARVIS_WEBHOOK_TIMEOUT_SECONDS`, `JARVIS_WEBHOOK_MAX_RETRIES`, and `JARVIS_WEBHOOK_HEADERS_JSON`. Tests use only a local mock server.

Supported methods: GET, POST, PUT, PATCH, DELETE. 2xx responses are receipts; HTTP 202 is accepted. Missing provider response IDs stay null; Jarvis request ID is always recorded.
