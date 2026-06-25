"""Thin Telegram Bot API sender (push only). Stdlib urllib, no new deps."""
from __future__ import annotations

import json
import ssl
import urllib.request

_API = "https://api.telegram.org/bot{token}/sendMessage"

# Windows Python's ssl can't always build the API's cert chain (no system CA
# integration), so verification fails there; certifi's CA bundle fixes it. Fall
# back to the default context where certifi isn't installed (e.g. macOS). Mirrors
# scripts/fetch_calendar.py.
try:
    import certifi

    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()


def build_request(token: str, chat_id: str, text: str,
                  parse_mode: str | None = None) -> urllib.request.Request:
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    data = json.dumps(payload).encode()
    return urllib.request.Request(
        _API.format(token=token), data=data,
        headers={"Content-Type": "application/json"})


def send(token: str, chat_id: str, text: str, timeout: int = 30,
         parse_mode: str | None = None) -> int:
    with urllib.request.urlopen(build_request(token, chat_id, text, parse_mode),
                                timeout=timeout, context=_SSL_CTX) as r:
        return r.status
