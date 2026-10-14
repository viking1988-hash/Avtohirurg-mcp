import math
import pytest
from jarvis_return_worker.automation_policy import route
from jarvis_return_worker.benchmark import benchmark, classify, corpus

@pytest.mark.parametrize("channel", ["email","telegram","sms","crm"])
def test_auto_prepares_approval_writes_and_human_handles_uncertainty(channel):
    assert route(channel,"draft",confidence=.99,synthetic=True).mode == "AUTO"
    assert route(channel,"send",confidence=.99,synthetic=True).mode == "APPROVAL"
    assert route(channel,"send",confidence=.99,synthetic=True,unknown_outcome=True).mode == "HUMAN"
    assert route(channel,"draft",confidence=.99,synthetic=True,safety_flag=True).mode == "HUMAN"
    assert route(channel,"send",confidence=.99,synthetic=False).mode == "HUMAN"

@pytest.mark.parametrize("confidence",[None,True,False,math.nan,math.inf,-1,1.1,".99",.89])
def test_missing_or_low_confidence_fails_closed(confidence):
    assert route("crm","update",confidence=confidence,synthetic=True).mode == "HUMAN"

def test_100_synthetic_fixture_baseline():
    report=benchmark()
    assert report["count"]==report["unique_texts"]==100
    assert report["status"]=="PASS" and report["unsafe_routes"]==0
    assert not report["model_tested"]
    assert classify("Неизвестная марка, неясная жалоба")["mode"]=="HUMAN"

def test_benchmark_rejects_client_data_and_wrong_fixture_size():
    with pytest.raises(ValueError): benchmark(corpus()[:99])
    rows=corpus(); rows[0]["synthetic"]=False
    with pytest.raises(ValueError): benchmark(rows)

@pytest.mark.parametrize("kind",["RETRYABLE","PERMANENT","UNKNOWN"])
def test_durable_receipt_precedes_late_provider_error(kind):
    from jarvis_return_worker.policy import PolicyEngine
    from jarvis_return_worker.transport import Classification,DeliveryError
    decision=PolicyEngine().delivery_failure(DeliveryError(Classification(kind),"late_error"),{"delivery_receipt":"durable-proof"})
    assert decision.action=="complete"
