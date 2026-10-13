# October 13: isolated acceptance
Base: MCP PR #39, SHA 131ef30e72ca3e36ed5c4a08c72e5da885f30c27.
New branch: feat/oct13-stability-diagnostics-20261013. Keep draft; do not merge.

AUTO prepares/classifies/reads. Every external effect requires existing fingerprint-bound human approval.
Unknown outcome, insufficient confidence and safety uncertainty require HUMAN. The new routing helper
does not grant transport permissions. Existing real Email/Telegram/CRM provider tests remain in the suite.
SMS uses the channel contract sink only; no real SMS provider is connected.

Four-channel PostgreSQL matrix covers duplicate enqueue, changed payload conflict, dual worker claim,
confirmed rejection retry, lost response quarantine, application recovery, durable receipt recovery,
expired approval and stale-worker fencing. Runs ONLY against localhost disposable CI PostgreSQL.
It must not be run against the shared Railway Sandbox DB: existing test cleanup is destructive.

Exactly-once applies to internal transactional effects. An external ambiguous send is retained for
operator reconciliation, never automatically resent; mailbox delivery cannot be proved by SMTP acceptance.

100 unique synthetic fixtures: ten symptom templates across ten makes. Baseline keyword classifier only.
The resulting 100/100 exploratory score is not an LLM benchmark or production readiness evidence.
Candidate API/model comparison is NOT VERIFIED; no unverified model is connected to production.

Rollback: keep production/main unchanged. Sandbox worker was live on
1eff8d069d5769bae811fadadebb529f6a5f4029 (deployment 80beb798-12d4-44bc-a6cd-808a6546d4c6).
A pre-existing pending patch f6325b94-3706-4f7f-9586-8cfc4dedb6c0 stages Email PR #39;
do not commit it as part of the isolated benchmark runner. An isolated benchmark service can be stopped
without changing this worker or its database. No new queue migration is introduced by this branch.
