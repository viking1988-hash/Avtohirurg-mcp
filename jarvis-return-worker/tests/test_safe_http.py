from urllib.request import Request

from jarvis_return_worker import safe_http
from jarvis_return_worker.safe_http import NoRedirectHandler


def test_redirect_handler_refuses_redirects():
    handler = NoRedirectHandler()
    request = Request("https://crm-sandbox.up.railway.app/api")
    assert handler.redirect_request(
        request, None, 302, "Found", {}, "https://attacker.example/collect"
    ) is None


def test_open_without_redirects_installs_rejecting_handler(monkeypatch):
    captured = {}

    class FakeOpener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return "response"

    def fake_build_opener(handler):
        captured["handler"] = handler
        return FakeOpener()

    monkeypatch.setattr(safe_http, "build_opener", fake_build_opener)
    request = Request("https://crm-sandbox.up.railway.app/api")
    assert safe_http.open_without_redirects(request, timeout=4) == "response"
    assert isinstance(captured["handler"], NoRedirectHandler)
    assert captured["request"] is request
    assert captured["timeout"] == 4
