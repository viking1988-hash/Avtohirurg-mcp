# Worker Recovery Guide

The send marker is committed before a transport call. A receipt and provider identifiers are committed before DONE.

- Expired lease with no send marker: recovery may return the task to APPROVED only while approval remains valid and the attempt budget permits it.
- Send marker without a durable receipt: recovery changes the task to RECONCILIATION_REQUIRED, sets delivery_status=UNKNOWN and reconciliation_status=REQUIRED, and releases the lease. Claims exclude this state.
- Durable receipt present: recovery completes the task without calling a provider again.
- Confirmed delivery: DELIVERED_CONFIRMED is terminal for worker claims.
- Confirmed non-delivery: UNDELIVERED_CONFIRMED is terminal until the explicit fresh-approval request resets it to READY.

Restarting a worker does not erase the marker, receipt, reconciliation decision, audit trail or version. A second worker uses database row locks and lease fencing; stale owners cannot write. An ambiguous transport result is never automatically retried. A provider's definitive rejection may follow its documented retry policy; this is separate from ambiguous reconciliation.

Rollback only the Sandbox worker. Keep the additive schema and event journal; do not restore an older database snapshot or delete delivery evidence.
