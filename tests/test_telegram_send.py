import json

from mt5_trader.live.telegram_send import build_request


def test_request_targets_bot_api_with_payload():
    req = build_request("123:ABC", "456", "hello desk")
    assert req.full_url == "https://api.telegram.org/bot123:ABC/sendMessage"
    payload = json.loads(req.data.decode())
    assert payload == {"chat_id": "456", "text": "hello desk"}
    assert req.headers["Content-type"] == "application/json"


def test_parse_mode_included_when_set():
    req = build_request("123:ABC", "456", "<b>hi</b>", parse_mode="HTML")
    assert json.loads(req.data.decode())["parse_mode"] == "HTML"
