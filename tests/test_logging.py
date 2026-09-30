from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from conftest import TEAM, TEST_KEY, Caller, FakeServer, Reply, reply

from wity import WityError

pytestmark = pytest.mark.usefixtures("fast_retries")

STATE = "PRIVATE_CUSTOMER_TEXT_987 I was charged twice"
"""Stands in for customer data. It must never appear in logs."""


def wity_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "wity"]


def retry_then_answer() -> FakeServer:
    return FakeServer(Reply(429, {"detail": "slow"}, {"Retry-After": "0"}), reply("choice"))


class TestLevels:
    def test_defaults_to_warnings_only_so_its_quiet_when_things_work(
        self, kind: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        Caller(kind, retry_then_answer(), api_key=TEST_KEY).system_one(state=STATE, questions={"team": TEAM})
        # A retry is logged at info, so nothing shows at the default level.
        assert wity_lines(caplog) == []

    def test_off_logs_nothing_not_even_warnings(
        self, kind: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WITY_LOG_LEVEL", "off")
        call = Caller(kind, FakeServer(reply("choice")), api_key=TEST_KEY)
        call.system_one(state=STATE, questions={"team": TEAM}, extra_body={"typo_field": 1})
        assert wity_lines(caplog) == []

    def test_info_shows_retries_with_the_reason_and_the_wait(
        self, kind: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WITY_LOG_LEVEL", "info")
        Caller(kind, retry_then_answer(), api_key=TEST_KEY).system_one(state=STATE, questions={"team": TEAM})
        lines = wity_lines(caplog)
        assert len(lines) == 1
        assert lines[0].startswith("/v1/systemone: got 429. Retrying in 0 ms (retry 1 of 2).")

    def test_debug_shows_each_requests_summary_status_and_request_id(
        self, kind: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WITY_LOG_LEVEL", "debug")
        server = FakeServer(reply("choice", headers={"x-railway-request-id": "req_42"}))
        Caller(kind, server, api_key=TEST_KEY).system_one(state=STATE, questions={"team": TEAM}, reasoning="off")
        text = "\n".join(wity_lines(caplog))
        assert "/v1/systemone: sending (attempt 1 of 3)" in text
        assert "questions=['team']" in text
        assert "reasoning='off'" in text
        assert "/v1/systemone: got 200" in text
        assert "req_42" in text

    def test_reads_wity_log_level_in_any_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WITY_LOG_LEVEL", " DEBUG ")
        Caller("sync", None, api_key=TEST_KEY)
        assert logging.getLogger("wity").level == logging.DEBUG

    @pytest.mark.parametrize("name", ["warn", "warning"])
    def test_accepts_warn_and_warning(self, name: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WITY_LOG_LEVEL", name)
        Caller("sync", None, api_key=TEST_KEY)
        assert logging.getLogger("wity").level == logging.WARNING

    def test_refuses_unknown_levels_and_lists_the_valid_ones(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WITY_LOG_LEVEL", "verbose")
        with pytest.raises(WityError, match="must be one of: debug, info, warn, error, off"):
            Caller("sync", None, api_key=TEST_KEY)

    def test_without_logging_set_up_debug_prints_to_stderr_with_a_prefix(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Like an app that hasn't set up logging. pytest adds its own handlers, so hide them.
        monkeypatch.setattr(logging.getLogger(), "handlers", [])
        monkeypatch.setenv("WITY_LOG_LEVEL", "debug")
        Caller("sync", FakeServer(reply("choice")), api_key=TEST_KEY).system_one(state=STATE, questions={"team": TEAM})
        Caller("sync", FakeServer(reply("choice")), api_key=TEST_KEY)  # a second client adds no second handler
        Caller("sync", FakeServer(reply("choice")), api_key=TEST_KEY).system_one(state=STATE, questions={"team": TEAM})
        err = capsys.readouterr().err
        assert err.startswith("[wity] /v1/systemone: sending (attempt 1 of 3)")
        assert err.count("sending") == 2

    def test_an_app_that_set_up_logging_gets_no_extra_handler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WITY_LOG_LEVEL", "debug")
        Caller("sync", None, api_key=TEST_KEY)
        # pytest's own handlers stand in for the app's.
        assert logging.getLogger("wity").handlers == []


class TestWhatLogsNeverContain:
    @pytest.mark.parametrize(
        ("name", "make_server", "options"),
        [
            ("a success", lambda: FakeServer(reply("choice")), {}),
            ("a retried 429", lambda: retry_then_answer(), {}),
            ("a retried connection error", lambda: FakeServer(httpx.ConnectError("refused"), reply("choice")), {}),
            (
                "a broken answer",
                lambda: FakeServer(Reply(body={"model": "wity-1"}, break_body=True)),
                {"max_retries": 0},
            ),
            ("an API error", lambda: FakeServer(reply("error-bad-key")), {}),
            ("a timeout", lambda: FakeServer(httpx.ReadTimeout("timed out")), {}),
        ],
    )
    def test_at_debug_no_api_key_and_no_state_text(
        self,
        kind: str,
        name: str,
        make_server: Callable[[], FakeServer],
        options: Any,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("WITY_LOG_LEVEL", "debug")
        call = Caller(kind, make_server(), api_key=TEST_KEY, **options)
        for request in (
            lambda: call.system_one(state=STATE, questions={"team": TEAM}, extra_body={"typo_field": 1}),
            lambda: call.generate(state=STATE, instructions="Reply"),
        ):
            with contextlib.suppress(WityError):
                request()
        lines = wity_lines(caplog)
        assert len(lines) > 0, name
        text = "\n".join(lines) + "\n" + caplog.text
        assert TEST_KEY not in text, name
        assert "PRIVATE_CUSTOMER_TEXT" not in text, name
