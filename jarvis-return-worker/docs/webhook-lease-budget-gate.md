# PR47 webhook lease budget gate (Sandbox only)

`Config.from_env()` defaults `JARVIS_LEASE_SECONDS` to **60**. When a webhook base URL is configured, startup requires a lease strictly greater than `(retries + 1) * timeout + retries * 10 + 5` seconds. At default HTTP settings (timeout 8 seconds, retries 2), this is **49 seconds**. A 60-second lease passes; a 30-second lease is rejected at startup.

The orchestrator checks the complete HTTP budget against the **remaining** lease before provider invocation. If insufficient, the provider is not called and the operation enters reconciliation. No webhook lease renewal is currently implemented. This pre-send check is not atomic with the external HTTP side effect; the receiver must implement durable idempotency for safe retries.

## Verified synthetic CI

- `bee6c3d`: concurrent PostgreSQL operations with the same response ID preserve the duplicate receipt; 289 passed.
- `f135796`: insufficient lease prevents provider invocation; 290 passed.
- `cba7ba6`: sufficient lease permits one fake provider call and DONE; [CI](https://github.com/viking1988-hash/Avtohirurg-mcp/actions/runs/40947517714) SUCCESS, 291 passed, 2 skipped, 1 warning, 9 subtests passed; PostgreSQL restart recovery PASS.

## Release gate

Do not equate passing synthetic tests with remote exactly-once. Review HTTP retry timing, receiver-side idempotency, uncertain outcomes and manual reconciliation. Independent Telegram P1 review and explicit human approval remain mandatory. PR47 stays Draft; Production unchanged.
