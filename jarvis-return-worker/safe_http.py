"""HTTP helpers for authenticated, non-production provider calls."""
from urllib.request import HTTPRedirectHandler, build_opener


class NoRedirectHandler(HTTPRedirectHandler):
    """Reject redirects so credentials cannot cross the configured origin."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_without_redirects(request, timeout):
    """Open one request without following any HTTP redirect."""
    return build_opener(NoRedirectHandler()).open(request, timeout=timeout)
