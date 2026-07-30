from unittest.mock import MagicMock

import requests

from generator.url_validator import URLValidator


def test_verify_url_uses_ssl_fallback_for_trusted_host() -> None:
    validator = URLValidator(timeout=1)
    ok_resp = MagicMock()
    ok_resp.status_code = 200

    def fake_head(url, **kwargs):
        if kwargs.get("verify", True):
            raise requests.exceptions.SSLError("certificate verify failed")
        return ok_resp

    validator.session.head = MagicMock(side_effect=fake_head)

    ok, status = validator.verify_url("https://www.blm.gov/visit/wilson-arch")

    assert ok is True
    assert status == 200


def test_verify_url_does_not_use_ssl_fallback_for_untrusted_host() -> None:
    validator = URLValidator(timeout=1)
    validator.session.head = MagicMock(side_effect=requests.exceptions.SSLError("certificate verify failed"))

    ok, status = validator.verify_url("https://example.com/page")

    assert ok is False
    assert "certificate" in str(status).lower()


def test_get_text_uses_ssl_fallback_for_trusted_host() -> None:
    validator = URLValidator(timeout=1)
    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.text = "Wilson Arch visitor information"

    def fake_get(url, **kwargs):
        if kwargs.get("verify", True):
            raise requests.exceptions.SSLError("certificate verify failed")
        return ok_resp

    validator.session.get = MagicMock(side_effect=fake_get)

    ok, status, text = validator.get_text("https://www.blm.gov/visit/wilson-arch")

    assert ok is True
    assert status == 200
    assert "Wilson Arch" in text


def test_get_text_records_final_redirect_url() -> None:
    validator = URLValidator(timeout=1)
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "Trail page"
    resp.url = "https://www.alltrails.com/trail/us/colorado/penrose-trail"
    validator.session.get = MagicMock(return_value=resp)

    ok, status, text = validator.get_text("https://www.alltrails.com/trail/us/colorado/bear-creek-trail")

    assert ok is True
    assert status == 200
    assert text == "Trail page"
    assert getattr(validator, "_last_final_url", "") == "https://www.alltrails.com/trail/us/colorado/penrose-trail"
