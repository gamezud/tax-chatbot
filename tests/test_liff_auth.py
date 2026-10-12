"""utils/liff_auth.py — mock requests.get ทุกเทส ไม่ต่อ LINE จริง"""
import traceback

import pytest
import requests

from utils import liff_auth
from utils.liff_auth import InvalidTokenError, LineUnavailableError, verify_liff_token

TOKEN = "eyJTEST.secret-token-value"
CHANNEL_ID = "1234567890"


class FakeResponse:
    def __init__(self, status_code, data=None, text=None):
        self.status_code = status_code
        self._data = data
        self._text = text

    def json(self):
        if self._text is not None:
            raise ValueError("not json")
        return self._data


class FakeLine:
    """แทน requests.get — ตั้งคำตอบด้วย respond(...) หรือให้ล้มด้วย fail(...)"""

    def __init__(self):
        self.calls = []
        self.response = None
        self.error = None

    def respond(self, *args, **kwargs):
        self.response = FakeResponse(*args, **kwargs)

    def fail(self, error):
        self.error = error

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def line(monkeypatch):
    fake = FakeLine()
    monkeypatch.setattr(liff_auth.requests, "get", fake.get)
    return fake


def valid_body(**overrides):
    body = {"scope": "chat_message.write", "client_id": CHANNEL_ID, "expires_in": 2591659}
    body.update(overrides)
    return body


def assert_token_not_leaked(exc_info):
    # traceback เต็มแบบที่ traceback.print_exc() ใน webhook() พิมพ์ — รวม exception ที่พ่วงมาด้วย
    full = "".join(traceback.format_exception(exc_info.value))
    assert TOKEN not in full


# ---------------------------------------------------------------------------
# token ถูก
# ---------------------------------------------------------------------------
def test_valid_token(line):
    line.respond(200, valid_body())
    assert verify_liff_token(TOKEN, CHANNEL_ID) is None


def test_request_shape(line):
    line.respond(200, valid_body())
    verify_liff_token(TOKEN, CHANNEL_ID)
    [(url, kwargs)] = line.calls
    assert url == "https://api.line.me/oauth2/v2.1/verify"
    assert kwargs["params"] == {"access_token": TOKEN}
    # ข้อ 1: รอรวมไม่เกิน 3 วินาที
    assert sum(kwargs["timeout"]) <= 3


def test_client_id_number_matches_string(line):
    # LINE อาจตอบ client_id เป็นตัวเลขหรือข้อความ เทียบเป็นข้อความทั้งคู่
    line.respond(200, valid_body(client_id=int(CHANNEL_ID)))
    assert verify_liff_token(TOKEN, CHANNEL_ID) is None


# ---------------------------------------------------------------------------
# token ผิด → InvalidTokenError (ให้เปิดลิงก์ใหม่จาก LINE)
# ---------------------------------------------------------------------------
def test_client_id_mismatch(line):
    line.respond(200, valid_body(client_id="9999999999"))
    with pytest.raises(InvalidTokenError) as exc_info:
        verify_liff_token(TOKEN, CHANNEL_ID)
    assert_token_not_leaked(exc_info)


@pytest.mark.parametrize("expires_in", [0, -5, None, "2591659"])
def test_expired_or_bad_expires_in(line, expires_in):
    line.respond(200, valid_body(expires_in=expires_in))
    with pytest.raises(InvalidTokenError):
        verify_liff_token(TOKEN, CHANNEL_ID)


def test_line_says_token_invalid(line):
    # LINE ตอบ 400 เมื่อ token ผิดหรือหมดอายุ
    line.respond(400, {"error": "invalid_request", "error_description": "access token expired"})
    with pytest.raises(InvalidTokenError) as exc_info:
        verify_liff_token(TOKEN, CHANNEL_ID)
    assert_token_not_leaked(exc_info)


@pytest.mark.parametrize("token", ["", None])
def test_missing_token_does_not_call_line(line, token):
    with pytest.raises(InvalidTokenError):
        verify_liff_token(token, CHANNEL_ID)
    assert line.calls == []


def test_missing_channel_id_is_config_error(line):
    with pytest.raises(ValueError):
        verify_liff_token(TOKEN, "")
    assert line.calls == []


# ---------------------------------------------------------------------------
# เรียก LINE ไม่ได้ → LineUnavailableError (ให้พิมพ์ยอดเอง)
# ---------------------------------------------------------------------------
# exception ของ requests ใส่ URL ที่มี token ไว้ในข้อความจริง ๆ — จำลองแบบเดียวกัน
URL_WITH_TOKEN = f"https://api.line.me/oauth2/v2.1/verify?access_token={TOKEN}"


@pytest.mark.parametrize("error", [
    requests.Timeout(f"Read timed out. url: {URL_WITH_TOKEN}"),
    requests.ConnectionError(f"Max retries exceeded with url: {URL_WITH_TOKEN}"),
], ids=["timeout", "connection"])
def test_network_error(line, error):
    line.fail(error)
    with pytest.raises(LineUnavailableError) as exc_info:
        verify_liff_token(TOKEN, CHANNEL_ID)
    assert_token_not_leaked(exc_info)


@pytest.mark.parametrize("status", [500, 502, 503, 429])
def test_line_server_error(line, status):
    line.respond(status, {"error": "server_error"})
    with pytest.raises(LineUnavailableError):
        verify_liff_token(TOKEN, CHANNEL_ID)


def test_line_returns_non_json(line):
    line.respond(200, text="<html>maintenance</html>")
    with pytest.raises(LineUnavailableError) as exc_info:
        verify_liff_token(TOKEN, CHANNEL_ID)
    assert_token_not_leaked(exc_info)


def test_line_returns_non_dict(line):
    line.respond(200, ["unexpected"])
    with pytest.raises(LineUnavailableError):
        verify_liff_token(TOKEN, CHANNEL_ID)


# ---------------------------------------------------------------------------
# ข้อ 2: ไม่ print token
# ---------------------------------------------------------------------------
def test_nothing_printed(line, capsys):
    line.respond(200, valid_body())
    verify_liff_token(TOKEN, CHANNEL_ID)
    line.fail(requests.ConnectionError(URL_WITH_TOKEN))
    with pytest.raises(LineUnavailableError):
        verify_liff_token(TOKEN, CHANNEL_ID)
    out, err = capsys.readouterr()
    assert out == "" and err == ""
