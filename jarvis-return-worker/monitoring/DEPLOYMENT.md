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

Railway initial deployment incident (2026-10-16): the first build used the repository default Dockerfile and crashed because it started the MCP server without MCP_TOKEN. The isolated monitoring service has since been configured with Dockerfile.monitoring-sandbox. A fresh build from that Dockerfile is required; redeploying the original image is insufficient. Do not add MCP_TOKEN to the monitoring service.
