# Email Adapter Guide

The `EmailAdapter` subscribes to `EmailRequested`, `EmailQueued`, `EmailSent`,
`EmailUnknown`, `EmailFailed`, and `EmailCancelled` through the PostgreSQL
Transactional Outbox. It transforms `EmailRequested` into one durable
`jarvis_email_operations` intent. A unique event key plus the outbox processed
marker makes replay idempotent. Lifecycle notifications are acknowledged by the
adapter without creating another send intent.

The adapter performs no network calls and does not import or call Queue or a
Provider. `EmailOperationWorker` asks the Orchestrator to apply policy, obtain a
fenced claim, and dispatch the operation to the registered provider. Never call
`EmailProvider` directly from an API handler or event subscriber.

## Registering another provider

Implement the provider protocol, register its instance by name in
`make_dispatcher`, and add an outbox adapter plus operation worker only when the
provider needs durable operations. Keep provider registration generic: do not
add provider-specific branches to `TransportDispatcher`. Protect the provider
with sandbox-only configuration and add contract tests for success, permanent,
retryable, and unknown outcomes.
