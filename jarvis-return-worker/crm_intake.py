"""Sandbox-only transport for the CRM PR #78 diagnostic intake contract.

Not registered in the worker yet. Runtime dispatch remains behind the existing
Jarvis approval and transactional outbox flow.
"""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .transport import Classification, DeliveryError

_SOURCE_ID = re.compile(
    r"^jarvis:[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_URGENCY = {"unassessed", "routine", "soon", "urgent", "emergency"}
_FIELDS = {
    "source_id", "name", "phone", "make", "model", "vehicle_year",
    "modification", "desired_date", "notes", "symptom", "urgency",
    "followup_consent",
}
_LIMITS = {
    "name": 120, "phone": 40, "make": 80, "model": 100,
    "modification": 120, "desired_date": 30, "notes": 2000, "symptom": 2000,
}


def normalize_intake(payload):
    """Whitelist and normalize fields accepted by CRM PR #78."""
    if not isinstance(payload, dict) or set(payload) - _FIELDS:
        raise DeliveryError(Classification.PERMANENT, "crm_lead_payload_invalid")
    normalized = {}
    for key in ("source_id", "name", "phone", "make", "model",
                "modification", "desired_date", "notes", "symptom"):
        value = payload.get(key, "")
        if not isinstance(value, str) or len(value) > _LIMITS.get(key, 200):
            raise DeliveryError(Classification.PERMANENT, "crm_lead_payload_invalid")
        normalized[key] = value.strip()
    if not _SOURCE_ID.fullmatch(normalized["source_id"]):
        raise DeliveryError(Classification.PERMANENT, "crm_lead_source_id_invalid")
    if not normalized["name"] or not normalized["phone"]:
        raise DeliveryError(Classification.PERMANENT, "crm_lead_identity_required")
    year = payload.get("vehicle_year")
    if year is not None and (type(year) is not int or not 1886 <= year <= 2100):
        raise DeliveryError(Classification.PERMANENT, "crm_lead_vehicle_year_invalid")
    urgency = payload.get("urgency", "unassessed")
    consent = payload.get("followup_consent", False)
    if urgency not in _URGENCY or type(consent) is not bool:
        raise DeliveryError(Classification.PERMANENT, "crm_lead_policy_field_invalid")
    normalized.update({
        "vehicle_year": year,
        "urgency": urgency,
        "followup_consent": consent,
    })
    return normalized


class CRMIntakeProvider:
    """Sandbox-only POST client for /api/v1/leads, authenticated by X-API-Key."""

    provider = "crm-intake"

    def __init__(self, base_url, api_key, timeout_seconds=8, opener=None):
        parsed = urlsplit(base_url or "")
        host = (parsed.hostname or "").lower()
        labels = host.split(".")
        sandbox_host = any("sandbox" in label for label in labels)
        production_host = any(
            "production" in label or label == "prod" or label.startswith("prod-")
            for label in labels
        )
        if (parsed.scheme != "https" or not host.endswith(".up.railway.app")
                or not api_key or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not sandbox_host
                or production_host or not 1 <= timeout_seconds <= 30):
            raise ValueError("CRM intake requires HTTPS Railway Sandbox endpoint and bounded timeout")
        self.__base_url = base_url.rstrip("/")
        self.__api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._opener = opener or urlopen

    def create_lead(self, payload):
        body = normalize_intake(payload)
        request = Request(
            self.__base_url + "/api/v1/leads",
            data=json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            headers={
                "X-API-Key": self.__api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                status_code = response.status
                raw = response.read(32768)
        except HTTPError as exc:
            if exc.code == 429:
                raise DeliveryError(Classification.RETRYABLE, "crm_intake_rate_limited") from None
            if 400 <= exc.code < 500:
                raise DeliveryError(Classification.PERMANENT, "crm_intake_rejected") from None
            raise DeliveryError(Classification.UNKNOWN, "crm_intake_server_outcome_unknown") from None
        except (TimeoutError, URLError, OSError):
            raise DeliveryError(Classification.UNKNOWN, "crm_intake_network_outcome_unknown") from None
        except Exception:
            raise DeliveryError(Classification.UNKNOWN, "crm_intake_transport_outcome_unknown") from None
        try:
            result = json.loads(raw) if raw else {}
        except Exception:
            raise DeliveryError(Classification.UNKNOWN, "crm_intake_response_unreadable") from None
        if status_code == 429:
            raise DeliveryError(Classification.RETRYABLE, "crm_intake_rate_limited")
        if status_code == 409:
            raise DeliveryError(Classification.PERMANENT, "crm_intake_payload_conflict")
        if status_code >= 500 or status_code < 200 or status_code >= 300:
            classification = Classification.UNKNOWN if status_code >= 500 else Classification.PERMANENT
            raise DeliveryError(classification, "crm_intake_request_rejected")
        if (status_code != 202 or not isinstance(result, dict) or result.get("ok") is not True
                or not isinstance(result.get("lead_id"), (str, int)) or not result.get("status")):
            raise DeliveryError(Classification.UNKNOWN, "crm_intake_receipt_missing")
        # Keep only the CRM identifier and status; never retain customer PII.
        return {
            "receipt_id": "crm-lead:" + str(result["lead_id"]),
            "lead_id": result["lead_id"],
            "status": str(result["status"]),
        }
