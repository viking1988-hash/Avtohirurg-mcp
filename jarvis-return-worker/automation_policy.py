"""Preparation-only AUTO / APPROVAL / HUMAN routing; never grants send authority."""
import math
from dataclasses import dataclass

CHANNELS = frozenset(("email", "telegram", "sms", "crm"))
READ_ONLY = frozenset(("classify", "draft", "read"))
WRITES = frozenset(("send", "create", "update", "cancel"))


@dataclass(frozen=True)
class AutomationDecision:
    mode: str
    reason: str


def route(channel, operation, *, confidence=None, safety_flag=False,
          unknown_outcome=False, synthetic=False):
    if channel not in CHANNELS or operation not in READ_ONLY | WRITES:
        return AutomationDecision("HUMAN", "unsupported_action")
    if synthetic is not True:
        return AutomationDecision("HUMAN", "synthetic_scope_required")
    if safety_flag is not False or unknown_outcome is not False:
        return AutomationDecision("HUMAN", "safety_or_delivery_uncertainty")
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        return AutomationDecision("HUMAN", "missing_or_invalid_confidence")
    if confidence < .9:
        return AutomationDecision("HUMAN", "insufficient_confidence")
    if operation in READ_ONLY:
        return AutomationDecision("AUTO", "preparation_only")
    return AutomationDecision("APPROVAL", "fingerprint_bound_human_approval_required")
