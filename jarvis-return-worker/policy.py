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
                and not task.get("delivery_started_at"))
