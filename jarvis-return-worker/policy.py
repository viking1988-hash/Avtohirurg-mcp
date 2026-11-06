"""Central policy for approval, retries, reconciliation and repeat sends."""
from dataclasses import dataclass
from .approval import valid_approval
from .transport import Classification, DeliveryError


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    reason: str


class PolicyEngine:
    """Only this module classifies whether a failed send can be attempted again."""

    def approval(self, task):
        return PolicyDecision("send" if valid_approval(task) else "fail", "approval_valid" if valid_approval(task) else "approval_invalid")

    def delivery_failure(self, error, task):
        if isinstance(error, DeliveryError):
            if error.classification == Classification.RETRYABLE:
                return PolicyDecision("retry", "provider_confirmed_rejection")
            if error.classification == Classification.PERMANENT:
                return PolicyDecision("fail", "provider_permanent_rejection")
            return PolicyDecision("reconcile", "provider_outcome_unknown")
        if task.get("delivery_receipt"):
            return PolicyDecision("complete", "receipt_already_durable")
        if task.get("delivery_started_at"):
            return PolicyDecision("reconcile", "send_outcome_unknown")
        return PolicyDecision("fail", "failure_before_send")

    def may_resend(self, task, provider_classification=None):
        return (provider_classification == Classification.RETRYABLE
                and not task.get("delivery_receipt")
                )

    def crm_operation(self, operation):
        allowed=(operation.get('operation') in ('send','update','cancel')
                 and operation.get('state')=='PROCESSING'
                 and bool(operation.get('booking_id'))
                 and operation.get('idempotency_key')=='jarvis-crm:'+str(operation.get('event_id')))
        return PolicyDecision('dispatch' if allowed else 'reconcile',
                              'crm_operation_valid' if allowed else 'crm_operation_integrity')

    def crm_intake_operation(self,operation):
        allowed=(operation.get('state')=='PROCESSING'
                 and bool(operation.get('source_id'))
                 and bool(operation.get('approved_by'))
                 and bool(operation.get('approved_at'))
                 and operation.get('payload',{}).get('source_id')==operation.get('source_id'))
        return PolicyDecision('dispatch' if allowed else 'reject',
                              'crm_intake_approved' if allowed else 'crm_intake_approval_integrity')

    def crm_intake_delivery_failure(self,error,operation):
        if isinstance(error,DeliveryError):
            if error.classification==Classification.RETRYABLE:
                return PolicyDecision('retry','crm_intake_confirmed_rejection')
            if error.classification==Classification.PERMANENT:
                return PolicyDecision('fail','crm_intake_permanent_rejection')
        return PolicyDecision('reconcile','crm_intake_outcome_unknown')

    def crm_delivery_failure(self,error,operation):
        if isinstance(error,DeliveryError):
            if error.classification==Classification.RETRYABLE:
                return PolicyDecision('retry','crm_confirmed_rejection')
            if error.classification==Classification.PERMANENT:
                return PolicyDecision('fail','crm_permanent_rejection')
        return PolicyDecision('reconcile','crm_outcome_unknown')

    def crm_reconciliation(self,operation):
        allowed=operation.get('state')=='RECONCILIATION_REQUIRED' and bool(operation.get('booking_id'))
        return PolicyDecision('reconcile' if allowed else 'reject',
                              'crm_reconciliation_allowed' if allowed else 'crm_not_awaiting_reconciliation')

    def email_operation(self,operation):
        allowed=(operation.get('state')=='PROCESSING'
                 and operation.get('event_id')
                 and operation.get('idempotency_key')=='jarvis-email:'+str(operation.get('event_id'))
                 and operation.get('recipient','').lower().endswith(('.test','.invalid')))
        return PolicyDecision('dispatch' if allowed else 'reject',
                              'email_operation_valid' if allowed else 'email_operation_integrity')

    def email_delivery_failure(self,error,operation):
        if isinstance(error,DeliveryError):
            if error.classification==Classification.RETRYABLE:
                return PolicyDecision('retry','smtp_confirmed_not_accepted')
            if error.classification==Classification.PERMANENT:
                return PolicyDecision('fail','smtp_permanent_rejection')
        return PolicyDecision('reconcile','smtp_outcome_unknown')

    def email_reconciliation(self,operation):
        allowed=(operation.get('state')=='RECONCILIATION_REQUIRED'
                 and bool(operation.get('idempotency_key')))
        return PolicyDecision('reconcile' if allowed else 'reject',
                              'email_reconciliation_allowed' if allowed else 'email_not_awaiting_reconciliation')

    def email_cancellation(self,operation):
        allowed=operation.get('state')=='PENDING'
        return PolicyDecision('cancel' if allowed else 'reject',
                              'email_unsent_cancellation_allowed' if allowed else 'email_already_attempted')

