# Transactional Outbox dispatch gate

## Contract

An approval is persisted together with its `TaskApproved` outbox event. The
Replay Worker processes that event with the database-only
`jarvis.dispatcher-intent` subscriber and records a version-bound row in
`jarvis_provider_dispatch_intents`. When `require_outbox_dispatch` is enabled,
the Return Worker cannot claim the approved task until that row exists for the
same approval timestamp and fingerprint. The Worker then applies Policy and
uses Dispatcher to select the configured provider.

The intent subscriber does not call Telegram or any other external service.
Telegram is never called by Replay Worker. After the durable send-start marker,
the existing receipt and lease fencing rules still apply: confirmed Telegram
429 may be retried; ambiguous outcomes stay in `RECONCILIATION_REQUIRED` and
are not resent automatically. Operator verification and reconciliation use the
Jarvis API under `/queue/{task_id}/delivery` and its versioned reconciliation
routes. The separate CRM Alerts operator service and its
`crm_telegram_outbox` table are not part of this Jarvis flow.

## Recovery

- Restart before approval-event replay: task remains unclaimable; Replay Worker
  resumes and creates the intent transactionally with its processed marker.
- Replay acknowledgement lost: the processed marker repairs publication; the
  subscriber's insert is idempotent.
- Restart after the intent but before claim: Worker can claim only after the
  durable intent exists.
- Restart after the delivery-start marker: existing recovery quarantines the
  task for reconciliation instead of blindly sending again.
- Receipt persisted before `DONE`: recovery completes the task without a new
  provider call.

## Sandbox test and rollback

`tests/test_outbox_dispatch_chain.py` exercises synthetic approvals, outbox
replay, the claim gate, Fake Provider, persisted receipt, repeat replay, and the
Jarvis delivery read API. A second test routes a synthetic UNKNOWN outcome to
operator reconciliation. Neither test makes a network request.

The schema addition is additive and repeatable. If a Sandbox rollout fails,
stop the Sandbox Worker first, retain the new table and audit evidence, and
restore the previous Sandbox commit with `JARVIS_TRANSPORT=fake` and no Telegram
credentials. Re-run health/readiness and the Fake-only tests before resuming.
Do not reuse this rollback procedure for Production.
