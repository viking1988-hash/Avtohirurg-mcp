"""Orchestrator v2 accounting boundary, no provider or network calls."""
from types import SimpleNamespace
import pytest
from jarvis_return_worker.orchestrator import Orchestrator


class FakeRepository:
    def __init__(self):
        self.calls = []
    def record_ai_usage_v2(self, usage, components):
        self.calls.append((usage, components))
        return True


def test_orchestrator_v2_delegates_without_provider_calls():
    repo = FakeRepository()
    class NoProvider:
        def __getattr__(self, name):
            raise AssertionError("provider access forbidden")
    orchestrator = Orchestrator(repo, NoProvider())
    usage = {"request_id": "synthetic", "expected_cost_micro_usd": 42}
    components = [{"component": "cached_input", "token_count": 1,
                   "rate_micro_usd_per_million": 42_000_000}]
    assert orchestrator.record_ai_usage_v2(usage, components) is True
    assert repo.calls == [(usage, components)]
    with pytest.raises(ValueError, match="invalid_usage_fields"):
        orchestrator.record_ai_usage_v2("unsafe", components)
    assert len(repo.calls) == 1
