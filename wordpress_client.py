"""Safe WordPress REST API client for Avtohirurg MCP.

Credentials are read only from environment variables and are never logged.
"""
import base64
import json
import os
import uuid
import urllib.error
import urllib.request
from typing import Any

WP_URL = os.environ.get("WORDPRESS_URL", "https://avtohirurg-tver.ru").rstrip("/")
WP_USERNAME = os.environ.get("WORDPRESS_USERNAME", "")
WP_APP_PASSWORD = os.environ.get("WORDPRESS_APP_PASSWORD", "")


def _auth_header() -> str:
    if not WP_USERNAME or not WP_APP_PASSWORD:
        raise RuntimeError("WordPress credentials are not configured")
    token = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
    return f"Basic {token}"


def _multipart_payload(payload: dict[str, Any]) -> tuple[bytes, str]:
    boundary = f"----AvtohirurgMCP{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for key, value in payload.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{value if value is not None else ''}\r\n"
            ).encode("utf-8")
        )
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def request(
    path: str,
    method: str = "GET",
    payload: Any | None = None,
    auth_required: bool = True,
) -> dict:
    url = f"{WP_URL}{path}"
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "Avtohirurg-MCP/1.0",
    }
    if auth_required:
        headers["Authorization"] = _auth_header()
    if payload is not None:
        if method == "POST" and path.startswith("/wp-json/contact-form-7/"):
            data, content_type = _multipart_payload(payload)
            headers["Content-Type"] = content_type
        else:
            data = json.dumps(payload, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = raw
            return {"ok": True, "status": response.status, "url": url, "data": body}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw
        return {"ok": False, "status": exc.code, "url": url, "error": body}
    except Exception as exc:
        return {"ok": False, "url": url, "error": str(exc)}


def health() -> dict:
    return request("/wp-json/", auth_required=False)


def current_user() -> dict:
    return request("/wp-json/wp/v2/users/me")


def get_page(page_id: int) -> dict:
    return request(f"/wp-json/wp/v2/pages/{page_id}")


def update_page(page_id: int, fields: dict) -> dict:
    allowed = {"title", "content", "excerpt", "status", "slug", "meta"}
    clean = {k: v for k, v in fields.items() if k in allowed}
    if not clean:
        raise ValueError("No supported WordPress page fields supplied")
    return request(f"/wp-json/wp/v2/pages/{page_id}", "POST", clean)


def cf7_feedback(form_id: int, fields: dict) -> dict:
    return request(
        f"/wp-json/contact-form-7/v1/contact-forms/{form_id}/feedback",
        "POST",
        fields,
    )
