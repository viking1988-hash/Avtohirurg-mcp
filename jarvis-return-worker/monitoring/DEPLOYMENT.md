# Isolated Sandbox Prometheus deployment

This configuration starts a Prometheus rule evaluator with **self-scrape only**.
It does not connect to Jarvis Worker, CRM, Production, or any outbound alert receiver.
The alert rules are loaded, but Worker/Outbox alerts are not operational until a
separate, approved read-only metrics scrape is configured.

For Railway, use the repository-root `Dockerfile.monitoring-sandbox` as the
service's custom Dockerfile. The Prometheus listener uses port 9090.

Before connecting Worker metrics:
- Confirm exact Sandbox-only private URL, metrics authentication and job label.
- Do not embed credentials in git, command lines, or Prometheus alert annotations.
- Configure a read-only scrape and verify target health in the isolated project.
- Verify alert pending/firing/resolved in Sandbox and a safe rollback.
- Never configure automatic retry/replay or customer notifications.
