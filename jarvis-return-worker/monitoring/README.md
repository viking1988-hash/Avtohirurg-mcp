# Sandbox alert validation and rollout

Status: GitHub rules and contract tests only. **Not activated in Railway.**

## Preconditions

1. Select an isolated Prometheus-compatible rule evaluator in Sandbox. The existing worker does not load YAML alert rules itself.
2. Confirm the evaluator can scrape the Sandbox worker metrics endpoint using read-only credentials. Never put tokens in rule files or logs.
3. Confirm the actual scrape job label. The `JarvisWorkerUnavailable` rule currently assumes `job="jarvis-sandbox-worker"`; update the selector to the verified label before activation.
4. Configure a Sandbox-only notification receiver or dry-run sink; no customer-facing messages.
5. Preserve all existing Railway staged changes in other services. Do not apply unrelated staged patches as part of this rollout.

## Acceptance checks

- Validate YAML and Prometheus rule syntax with `promtool check rules jarvis-return-worker/monitoring/alerts.yml`.
- Verify `outbox_pending_total`, `reconciliation_required_queue`, and `outbox_failed_total` are exported by the target worker.
- Simulate metric changes in an isolated test environment and verify each alert's pending/firing/resolved transitions.
- Confirm a missing scrape target is reported, and a stale metric is not interpreted as zero backlog.
- Confirm alerting does **not** trigger any retry, replay, approval, delivery, or state mutation.
- Record evaluator version, rules checksum, test evidence, and rollback method.

## Rollback

Disable the Sandbox evaluator's rule group or revert the Sandbox-only rule configuration. Do not modify Production, GitHub main, or live customer data.

The GitHub CI success verifies repository checks, not alert firing or Railway deployment.
