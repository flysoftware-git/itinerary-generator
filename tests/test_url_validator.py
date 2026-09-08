import threading
from concurrent.futures import ThreadPoolExecutor
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


def test_final_redirect_url_is_not_shared_between_threads() -> None:
    """One validator is shared by every page-fetch worker thread, so the
    record of "where did that URL redirect to" must belong to the calling
    thread and not to the instance. The interleaving here is forced rather
    than raced: thread A finishes its own call, then blocks until thread B
    has finished its call, and only then reads the record back -- which is
    exactly the window the caller occupies between `get_text` returning and
    it reading the final URL. With one shared attribute, A reads B's
    redirect and attributes it to A's URL."""
    validator = URLValidator(timeout=1)

    url_a = "https://example.com/a"
    url_b = "https://example.org/b"
    final_a = "https://example.com/a-final"
    final_b = "https://example.org/b-final"
    finals = {url_a: final_a, url_b: final_b}

    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "page"
        resp.url = finals[url]
        return resp

    validator.session.get = MagicMock(side_effect=fake_get)

    a_called = threading.Event()
    b_called = threading.Event()

    def call_a():
        validator.get_text(url_a)
        a_called.set()
        assert b_called.wait(timeout=10), "thread B never ran"
        return getattr(validator, "_last_final_url", "")

    def call_b():
        assert a_called.wait(timeout=10), "thread A never ran"
        validator.get_text(url_b)
        b_called.set()
        return getattr(validator, "_last_final_url", "")

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(call_a)
        future_b = pool.submit(call_b)
        seen_a = future_a.result(timeout=15)
        seen_b = future_b.result(timeout=15)

    assert seen_a == final_a, f"thread A read thread B's redirect: {seen_a}"
    assert seen_b == final_b


def test_final_redirect_url_is_cleared_when_the_request_fails() -> None:
    """Same misattribution, one thread and two calls apart: a request that
    raises never records a final URL, so without a reset the caller reads
    the *previous* call's redirect and files it against this URL."""
    validator = URLValidator(timeout=1)

    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.text = "page"
    ok_resp.url = "https://example.com/first-final"

    def fake_get(url, **kwargs):
        if url == "https://example.com/first":
            return ok_resp
        raise requests.exceptions.ConnectionError("boom")

    validator.session.get = MagicMock(side_effect=fake_get)

    validator.get_text("https://example.com/first")
    assert getattr(validator, "_last_final_url", "") == "https://example.com/first-final"

    ok, _status, _text = validator.get_text("https://example.com/second")

    assert ok is False
    assert getattr(validator, "_last_final_url", "") == ""
