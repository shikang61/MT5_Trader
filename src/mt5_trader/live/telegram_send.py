"""Thin Telegram Bot API sender (push only). Stdlib urllib, no new deps."""
from __future__ import annotations

import json
import urllib.request

_API = "https://api.telegram.org/bot{token}/sendMessage"


def build_request(token: str, chat_id: str, text: str) -> urllib.request.Request:
    data = json.dumps({"chat_id": chat_id, "text": text}).encode()
    return urllib.request.Request(
        _API.format(token=token), data=data,
        headers={"Content-Type": "application/json"})


def send(token: str, chat_id: str, text: str, timeout: int = 30) -> int:
    with urllib.request.urlopen(build_request(token, chat_id, text), timeout=timeout) as r:
        return r.status
