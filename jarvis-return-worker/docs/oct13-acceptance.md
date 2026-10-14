# October 13: isolated acceptance
Base: MCP PR #39, SHA 131ef30e72ca3e36ed5c4a08c72e5da885f30c27.
New branch: feat/oct13-stability-diagnostics-20261013. Keep draft; do not merge.

AUTO prepares/classifies/reads. The generic return queue binds outbound effects to an existing fingerprint approval.
The synthetic four-channel matrix exercises its no-duplicate/recovery contract only; it does not prove each
specialized provider's distinct approval path. Unknown outcomes and safety uncertainty require HUMAN in
that policy. The helper does not grant provider permissions.

Four-channel PostgreSQL matrix covers duplicate enqueue, changed payload conflict, dual worker claim,
confirmed rejection retry, lost response quarantine, application recovery, durable receipt recovery,
expired approval and stale-worker fencing. Runs ONLY against localhost disposable CI PostgreSQL.
It must not be run against the shared Railway Sandbox DB: existing test cleanup is destructive.

Exactly-once applies to internal transactional effects. An external ambiguous send is retained for
operator reconciliation, never automatically resent; mailbox delivery cannot be proved by SMTP acceptance.

100 unique synthetic fixtures: ten symptom templates across ten makes. Baseline keyword classifier only.
The resulting 100/100 exploratory score is not an LLM benchmark or production readiness evidence.
Candidate API/model comparison is NOT VERIFIED; no unverified model is connected to production.

Provider-shaped verification added after the initial matrix:
- Telegram HTTP behavior uses a mocked `urlopen`; allowlisted test chat, minimized receipt, ambiguous timeout quarantine and wrong-chat rejection are checked without network access.
- Email `/email/requests` rejects missing human authorization and real recipient domains before creating an event. Existing PostgreSQL tests cover one durable Email intent through replay/restart and the fake SMTP sink.
- CRM adapter/provider replay and recovery tests run in CI, but the separate CRM operation path has no per-operation human approval endpoint/record in this branch. AUTO→APPROVAL→HUMAN for that path remains NOT VERIFIED and must be resolved before real CRM writes.
- SMS dispatch is explicitly unregistered and fails closed. There is no SMS adapter/provider to exercise; the generic contract sink is not SMS provider evidence.

Rollback: keep production/main unchanged. Sandbox worker was live on
1eff8d069d5769bae811fadadebb529f6a5f4029 (deployment 80beb798-12d4-44bc-a6cd-808a6546d4c6).
A pre-existing pending patch f6325b94-3706-4f7f-9586-8cfc4dedb6c0 stages Email PR #39;
do not commit it as part of the isolated benchmark runner. An isolated benchmark service can be stopped
without changing this worker or its database. No new queue migration is introduced by this branch.
