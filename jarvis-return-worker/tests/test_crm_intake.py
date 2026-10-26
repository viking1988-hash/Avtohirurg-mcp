import io
import json
import uuid
from urllib.error import HTTPError, URLError

import pytest

from jarvis_return_worker.crm_intake import CRMIntakeProvider, normalize_intake
from jarvis_return_worker.transport import Classification, DeliveryError


class Response:
    def __init__(self, body, status=202):
        self.status = status
        self.raw = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.raw[:limit]


def payload(**updates):
    value = {
        "source_id": "jarvis:" + str(uuid.uuid4()),
        "name": "Synthetic customer",
        "phone": "000000000",
        "make": "Synthetic",
        "model": "Test",
        "vehicle_year": 2020,
        "modification": "Synthetic trim",
        "desired_date": "",
        "notes": "synthetic only",
        "symptom": "synthetic vibration",
        "urgency": "soon",
        "followup_consent": False,
    }
    value.update(updates)
    return value


def test_provider_requires_sandbox_https_and_api_key():
    invalid = [
        ("http://crm-sandbox.up.railway.app", "key"),
        ("https://crm-staging.up.railway.app", "key"),
        ("https://crm-production-sandbox.up.railway.app", "key"),
        ("https://crm-sandbox.example", "key"),
        ("https://user:pass@crm-sandbox.up.railway.app", "key"),
        ("https://crm-sandbox.up.railway.app?token=x", "key"),
        ("https://crm-sandbox.up.railway.app", ""),
    ]
    for url, key in invalid:
        with pytest.raises(ValueError):
            CRMIntakeProvider(url, key)


def test_contract_whitelists_fields_and_requires_stable_jarvis_identity():
    normalized = normalize_intake(payload())
    assert normalized["source_id"].startswith("jarvis:")
    assert normalized["urgency"] == "soon"
    assert normalized["followup_consent"] is False
    for invalid in (
        payload(source_id="random-id"),
        payload(name=""),
        payload(vehicle_year=True),
        payload(urgency="critical"),
        payload(followup_consent="yes"),
        payload(consent_recorded_at="caller-controlled"),
    ):
        with pytest.raises(DeliveryError):
            normalize_intake(invalid)


def test_create_lead_uses_crm_intake_contract_and_returns_minimal_receipt():
    captured = {}

    def opener(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return Response({
            "ok": True,
            "lead_id": 102,
            "status": "new",
            "customer_phone": "must-not-be-retained",
        })

    provider = CRMIntakeProvider(
        "https://crm-sandbox.up.railway.app", "synthetic-key", opener=opener
    )
    request_payload = payload()
    result = provider.create_lead(request_payload)
    assert captured["request"].get_method() == "POST"
    assert captured["request"].full_url == "https://crm-sandbox.up.railway.app/api/v1/leads"
    assert captured["request"].get_header("X-api-key") == "synthetic-key"
    assert captured["timeout"] == 8
    assert captured["body"]["source_id"] == request_payload["source_id"]
    assert result == {"receipt_id": "crm-lead:102", "lead_id": 102, "status": "new"}
    assert "customer_phone" not in repr(result)
    assert set(captured["body"]) == {
        "source_id", "name", "phone", "make", "model", "vehicle_year",
        "modification", "desired_date", "notes", "symptom", "urgency",
        "followup_consent",
    }


@pytest.mark.parametrize(("status", "classification"), [
    (429, Classification.RETRYABLE),
    (401, Classification.PERMANENT),
    (409, Classification.PERMANENT),
    (500, Classification.UNKNOWN),
])
def test_provider_classifies_http_errors_without_response_leaks(status, classification):
    def opener(request, timeout):
        raise HTTPError(
            request.full_url, status, "synthetic", {},
            io.BytesIO(b"private response body"),
        )

    provider = CRMIntakeProvider(
        "https://crm-sandbox.up.railway.app", "never-log-this", opener=opener
    )
    with pytest.raises(DeliveryError) as caught:
        provider.create_lead(payload())
    assert caught.value.classification == classification
    assert "never-log-this" not in str(caught.value)
    assert "private response body" not in str(caught.value)


def test_network_timeout_is_unknown_and_invalid_receipt_is_not_success():
    provider = CRMIntakeProvider(
        "https://crm-sandbox.up.railway.app",
        "synthetic-key",
        opener=lambda *args, **kwargs: (_ for _ in ()).throw(URLError("timeout")),
    )
    with pytest.raises(DeliveryError) as caught:
        provider.create_lead(payload())
    assert caught.value.classification == Classification.UNKNOWN

    malformed = CRMIntakeProvider(
        "https://crm-sandbox.up.railway.app",
        "synthetic-key",
        opener=lambda *args, **kwargs: Response({"ok": True, "status": "new"}),
    )
    with pytest.raises(DeliveryError) as caught:
        malformed.create_lead(payload())
    assert caught.value.classification == Classification.UNKNOWN
