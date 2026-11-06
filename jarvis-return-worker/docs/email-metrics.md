# Email metrics

Authenticated `/metrics` exposes the following counters and histogram. They are
process metrics and reset on process restart.

| Metric | Meaning |
| --- | --- |
| `email_send_total` | SMTP send attempts made by the provider |
| `email_success_total` | Relay accepted the message (not mailbox delivery) |
| `email_failed_total` | Definitive invalid request, provider rejection, or terminal failure |
| `email_unknown_total` | Result became ambiguous after possible SMTP acceptance |
| `email_duplicate_prevented_total` | An existing outbox event/intent prevented duplicate materialization |
| `email_reconciliation_total` | Operations placed in reconciliation-required state |
| `email_latency_seconds` | SMTP submission attempt duration histogram |

No recipient, message body, subject, SMTP credentials, or connection string is
included in metric labels.
