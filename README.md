# Avtohirurg-mcp

## Jarvis Return Worker (Railway Sandbox)

The separately deployed worker and its Sandbox-only Telegram transport are documented in [jarvis-return-worker/README.md](jarvis-return-worker/README.md). Telegram delivery is restricted to an explicitly allowlisted test chat. An ambiguous result is held in `RECONCILIATION_REQUIRED`; it is never automatically resent.


The provider-neutral reconciliation layer and its Sandbox-only API are documented in [the state machine](jarvis-return-worker/docs/state-machine.md), [API reference](jarvis-return-worker/docs/api-reference.md), [manual reconciliation guide](jarvis-return-worker/docs/reconciliation-guide.md), and [recovery guide](jarvis-return-worker/docs/recovery-guide.md).
