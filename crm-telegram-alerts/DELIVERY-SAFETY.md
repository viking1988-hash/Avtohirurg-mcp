# CRM Telegram delivery safety — proposed policy

This is a conservative interim change, **not** a complete delivery-state/outbox implementation.

- A definite Telegram API rejection (HTTP 4xx, or JSON `ok:false`) releases the claim for a future retry. HTTP 429 retry timing is not yet implemented.
- A network exception, timeout, malformed response, or HTTP 5xx is **ambiguous**: Telegram might already have accepted the message. Retain the existing `(lead_id, alert_type)` claim and emit `send_outcome_uncertain_manual_review` with the lead ID and alert type. The cron exits nonzero.
- Do not automatically clear an ambiguous claim. A human must verify whether the message was received before any manual replay. Do not print bot tokens or chat IDs.
- **Trade-off:** if Telegram did not accept the message, the retained claim suppresses future automatic delivery; operators must monitor the uncertain event. This is not exactly-once delivery.
- A process crash after claiming and before sending also leaves an unresolved claim, with no explicit state or alert today. Full implementation requires an explicit pending/sent/uncertain state machine, crash recovery and monitoring (Issue #62).
- Existing historical claims cannot distinguish confirmed delivery from uncertain outcomes. Do not mass-delete or automatically migrate them.

## Release gates

1. Review real cron execution logs and determine the original `cronFailed` cause.
2. Run mocked tests and PostgreSQL-isolated E2E with zero real Telegram sends.
3. Add durable monitoring/operator handling for uncertain claims and stalled sends.
4. Review rollback and manual reconciliation. No Production deployment until these gates pass.
