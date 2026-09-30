from __future__ import annotations

import random
import time
from datetime import datetime, timezone

import httpx
import pytest

from wity._retry import MAX_RETRY_AFTER, backoff, delay_before_retry, retry_after, should_retry_status

NOON = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def headers(value: str | None = None) -> httpx.Headers:
    return httpx.Headers({} if value is None else {"Retry-After": value})


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 599])
def test_retries(status: int) -> None:
    assert should_retry_status(status)


@pytest.mark.parametrize("status", [200, 301, 400, 401, 402, 404, 422])
def test_doesnt_retry(status: int) -> None:
    assert not should_retry_status(status)


class TestRetryAfter:
    def test_reads_seconds(self) -> None:
        assert retry_after(headers("1")) == 1.0
        assert retry_after(headers("0.5")) == 0.5
        assert retry_after(headers("0")) == 0.0

    def test_reads_an_http_date(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(time, "time", lambda: NOON)
        assert retry_after(headers("Wed, 30 Sep 2026 12:00:05 GMT")) == 5.0

    def test_treats_a_date_in_the_past_as_no_wait(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(time, "time", lambda: NOON)
        assert retry_after(headers("Wed, 30 Sep 2026 11:00:00 GMT")) == 0.0

    @pytest.mark.parametrize("value", [None, "", "soon", "inf", "nan"])
    def test_ignores(self, value: str | None) -> None:
        assert retry_after(headers(value)) is None

    def test_treats_negative_seconds_as_no_wait(self) -> None:
        assert retry_after(headers("-5")) == 0.0


class TestBackoff:
    def test_doubles_from_half_a_second_and_jitter_takes_off_up_to_25_percent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(random, "random", lambda: 1.0)
        assert [backoff(n) for n in range(4)] == [0.5, 1.0, 2.0, 4.0]
        monkeypatch.setattr(random, "random", lambda: 0.0)
        assert backoff(0) == 0.375

    def test_never_waits_more_than_8_s(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(random, "random", lambda: 1.0)
        assert backoff(10) == 8.0
        assert backoff(10_000) == 8.0


class TestDelayBeforeRetry:
    def test_uses_retry_after_when_the_server_sends_one(self) -> None:
        assert delay_before_retry(0, headers("2")) == 2.0

    def test_falls_back_to_backoff_without_retry_after(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(random, "random", lambda: 1.0)
        assert delay_before_retry(1, headers()) == 1.0

    def test_stops_retrying_when_retry_after_asks_for_more_than_60_s(self) -> None:
        assert delay_before_retry(0, headers(str(int(MAX_RETRY_AFTER)))) == MAX_RETRY_AFTER
        assert delay_before_retry(0, headers("90")) is None
