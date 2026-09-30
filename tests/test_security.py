from __future__ import annotations

import logging
import sys
import traceback
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from conftest import TEAM, TEST_KEY, Caller, FakeServer, Reply, reply

import wity
from wity import DEFAULT_BASE_URL, WityClient, WityError


def all_printed_forms(value: object) -> str:
    """Every way a key could leak when an object is printed, logged or formatted."""
    forms = [str(value), repr(value)]
    if hasattr(value, "__dict__"):
        forms.append(repr(vars(value)))
    if isinstance(value, BaseException):
        forms.append("".join(traceback.format_exception(type(value), value, value.__traceback__)))
    return "\n".join(forms)


def raised_by(fn: Any) -> BaseException:
    with pytest.raises(WityError) as info:
        fn()
    return info.value


class TestBaseUrl:
    def test_defaults_to_the_production_address(self) -> None:
        assert WityClient(api_key=TEST_KEY).base_url == DEFAULT_BASE_URL

    def test_uses_wity_base_url_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WITY_BASE_URL", "https://api.example.com")
        assert WityClient(api_key=TEST_KEY).base_url == "https://api.example.com"

    def test_prefers_the_base_url_option_over_wity_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WITY_BASE_URL", "https://from-env.example.com")
        client = WityClient(api_key=TEST_KEY, base_url="https://from-option.example.com")
        assert client.base_url == "https://from-option.example.com"

    def test_keeps_a_path_like_api_and_sends_requests_under_it(self, kind: str) -> None:
        server = FakeServer(reply("choice"))
        call = Caller(kind, server, api_key=TEST_KEY, base_url="https://wity.example.com/api/")
        assert call.client.base_url == "https://wity.example.com/api"
        call.system_one(state="x", questions={"team": TEAM})
        assert str(server.requests[0].url) == "https://wity.example.com/api/v1/systemone"

    def test_refuses_plain_http_so_the_key_is_never_sent_unencrypted(self) -> None:
        with pytest.raises(WityError, match="https://"):
            WityClient(api_key=TEST_KEY, base_url="http://api.example.com")

    @pytest.mark.parametrize("url", ["http://localhost:8123", "http://127.0.0.1:8123", "http://[::1]:8123"])
    def test_allows_http_for_localhost(self, url: str) -> None:
        assert WityClient(api_key=TEST_KEY, base_url=url).base_url == url

    @pytest.mark.parametrize(
        ("reason", "url"),
        [
            ("a username or password", "https://user:pass@api.example.com"),
            ("\\? or #", "https://api.example.com?key=abc"),
            ("\\? or #", "https://api.example.com#part"),
            ("not a valid URL", "api.example.com"),
            ("not a valid URL", "https://api.example.com:notaport"),
        ],
    )
    def test_refuses_bad_urls(self, reason: str, url: str) -> None:
        with pytest.raises(WityError, match=reason):
            WityClient(api_key=TEST_KEY, base_url=url)

    def test_doesnt_repeat_an_invalid_url_in_the_error_in_case_a_key_was_pasted_there(self) -> None:
        for url in (f"not a url {TEST_KEY}", f"https://{TEST_KEY}@api.example.com", f"https://x.com?k={TEST_KEY}"):
            err = raised_by(lambda url=url: WityClient(api_key="wity_other", base_url=url))
            assert TEST_KEY not in all_printed_forms(err)


class TestApiKey:
    def test_reads_wity_api_key_when_no_api_key_is_passed(self, kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WITY_API_KEY", TEST_KEY)
        server = FakeServer(reply("choice"))
        Caller(kind, server).system_one(state="x", questions={"team": TEAM})
        assert server.requests[0].headers["authorization"] == f"Bearer {TEST_KEY}"

    def test_explains_how_to_set_the_key_when_its_missing(self) -> None:
        with pytest.raises(WityError, match="WITY_API_KEY"):
            WityClient()

    @pytest.mark.parametrize("empty", ["", "   "])
    def test_refuses_an_empty_api_key_instead_of_quietly_using_wity_api_key(
        self, empty: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Falling back to the environment could bill another account.
        monkeypatch.setenv("WITY_API_KEY", "wity_someone_elses_key")
        with pytest.raises(WityError, match="The api_key option is empty"):
            WityClient(api_key=empty)

    @pytest.mark.parametrize("bad_key", ["wity_abc def", "wity_abc\ndef", "wity_abc\tdef", "wity_abcé"])
    def test_refuses_keys_with_spaces_or_line_breaks_without_quoting_them(self, bad_key: str) -> None:
        err = raised_by(lambda: WityClient(api_key=bad_key))
        assert "wity_" not in all_printed_forms(err)

    def test_never_shows_the_key_when_the_client_is_printed(self, kind: str) -> None:
        client = Caller(kind, None, api_key=TEST_KEY).client
        assert TEST_KEY not in all_printed_forms(client)
        assert repr(client) == f"{type(client).__name__}(base_url={DEFAULT_BASE_URL!r})"

    @pytest.mark.parametrize(
        ("name", "make_server", "options"),
        [
            ("an API error", lambda: FakeServer(reply("error-bad-key")), {}),
            ("a connection error", lambda: FakeServer(httpx.ConnectError("connection refused")), {}),
            ("a timeout", lambda: FakeServer(httpx.ReadTimeout("timed out")), {}),
            ("a stalled answer", lambda: FakeServer(reply("choice", stall=0.1)), {"timeout": 0.02}),
            ("a redirect", lambda: FakeServer(Reply(302, headers={"Location": "https://elsewhere.example"})), {}),
            ("a broken answer", lambda: FakeServer(Reply(body={"model": "wity-1"}, break_body=True)), {}),
        ],
    )
    def test_never_shows_the_key_in_errors(
        self, kind: str, name: str, make_server: Callable[[], FakeServer], options: Any
    ) -> None:
        call = Caller(kind, make_server(), api_key=TEST_KEY, max_retries=0, **options)
        err = raised_by(lambda: call.system_one(state="x", questions={"team": TEAM}))
        assert TEST_KEY not in all_printed_forms(err), name
        if err.__cause__ is not None:
            assert TEST_KEY not in all_printed_forms(err.__cause__), name


class TestBrowser:
    def test_refuses_to_run_in_a_browser_where_visitors_could_read_the_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pyodide and PyScript run Python inside a web page.
        monkeypatch.setattr(sys, "platform", "emscripten")
        with pytest.raises(WityError, match="browser"):
            WityClient(api_key=TEST_KEY)


class TestAnswersFromTheWrongServer:
    def test_stops_reading_an_answer_over_10_mb_and_doesnt_retry(self, kind: str) -> None:
        server = FakeServer(Reply(body="x" * (11 * 1024 * 1024)))
        call = Caller(kind, server, api_key=TEST_KEY)
        with pytest.raises(WityError, match="larger than 10 MB"):
            call.system_one(state="x", questions={"team": TEAM})
        assert len(server.requests) == 1


class TestLogLines:
    def test_a_field_name_with_a_line_break_cant_fake_a_new_log_line(
        self, kind: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        call = Caller(kind, FakeServer(reply("choice")), api_key=TEST_KEY)
        call.system_one(state="x", questions={"team": TEAM}, extra_body={"typo\n[wity] all good\u2028": 1})
        messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert len(messages) == 1
        assert "fields Wity may not know: typo?[wity] all good?." in messages[0]

    def test_a_question_name_with_a_line_break_cant_fake_a_log_line_either(
        self, kind: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG, logger="wity")
        call = Caller(kind, FakeServer(reply("choice")), api_key=TEST_KEY)
        call.system_one(state="x", questions={"team\n[wity] fake": TEAM})
        assert all("\n" not in r.getMessage() for r in caplog.records)


class TestOptions:
    @pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "60"])
    def test_refuses_bad_timeouts(self, timeout: Any) -> None:
        with pytest.raises(WityError, match="timeout"):
            WityClient(api_key=TEST_KEY, timeout=timeout)

    @pytest.mark.parametrize("max_retries", [-1, 1.5, True])
    def test_refuses_bad_max_retries(self, max_retries: Any) -> None:
        with pytest.raises(WityError, match="max_retries"):
            WityClient(api_key=TEST_KEY, max_retries=max_retries)

    def test_the_public_names_are_all_exported(self) -> None:
        for name in wity.__all__:
            assert hasattr(wity, name), name
