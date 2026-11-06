# Email Provider Guide (Sandbox)

`EmailProvider` implements the provider contract (`send`, `cancel`, `checkStatus`,
`reconcile`, `getReceipt`) and is registered by the existing generic Dispatcher.
It contains SMTP protocol handling only; approval, retry and reconciliation
choices belong to the Orchestrator and Policy Engine.

## Isolated configuration

Email is disabled unless `JARVIS_EMAIL_SANDBOX_SMTP_HOST` is configured. The host
must be localhost or a private Railway hostname naming a `mailpit`, `smtp-sink`,
or `sandbox` service. Sender and recipients must use `.test` or `.invalid`
domains. `JARVIS_EMAIL_SANDBOX_SMTP_PORT`, `JARVIS_EMAIL_SANDBOX_FROM`, and
`JARVIS_EMAIL_TIMEOUT_SECONDS` configure the test sink. Do not add production
SMTP credentials or a public SMTP endpoint. The Sandbox deployment intentionally
has no SMTP host configured, so it cannot send email.

A stable `Message-ID` and idempotency key are derived from the outbox event. SMTP
itself does not define idempotent submission. Therefore a timeout or disconnect
during DATA is `UNKNOWN` and is never automatically retried. SMTP 250 means only
that the relay accepted the message, not that a mailbox received it. SMTP also
has no standard cancellation, status lookup, or receipt lookup endpoint; those
methods report unsupported/unknown and reconciliation requires operator evidence.
