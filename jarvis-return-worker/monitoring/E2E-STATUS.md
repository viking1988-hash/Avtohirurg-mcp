# Monitoring E2E acceptance record

Verified on commit 3a477e33b26b00eceeb6368b9b78067ed7018d66:
- GitHub Actions: Jarvis Sandbox Alert Rules, Avtohirurg MCP check, Jarvis Return Worker: success.
- Railway isolated Prometheus deployment 3c26998c-3606-4930-8f1f-a65c69e6507b: success.
- Prometheus rule unit tests cover firing and recovery for four alerts.

Not yet verified:
- Authenticated scrape of live Sandbox Worker /metrics across Railway projects.
- Live evaluator state transitions against a running synthetic target.
- Dry-run Alertmanager receiver delivery and deduplication.
- Actual worker metrics label alignment.

Do not interpret rule unit-test success as live end-to-end notification delivery.
No changes to Production, main, existing worker or CRM are required for these checks.
