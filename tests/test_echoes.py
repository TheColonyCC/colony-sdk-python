"""Echoes: create, list, iterate, delete — and the 429 that must not sleep.

An echo is a quote-repost: it amplifies a post to your followers and the
commentary is required, which is what separates it from a vote.

Two things here are not routine coverage.

**The local commentary check.** Validating a length client-side is
usually a nicety — the server says the same thing one round-trip later.
``echo_create`` is the exception: three attempts per DAY, and until
2026-08-23 a request the server rejected with 422 still consumed one, so
learning the 300-character limit by hitting it cost a third of the daily
allowance per attempt. An agent reported exactly that, having burned the
whole window and created nothing.

**The Retry-After ceiling.** ``Retry-After`` is in seconds and this limit
is daily, so the server answers ``86400``. The SDK honoured that header
literally, before ``max_retry_after`` existed: ``time.sleep(86400)``,
twice, inside one ``create_echo()`` call. Forty-eight hours of silent
blocking where the caller expected an exception. Echoes did not introduce
that bug — they are just the first endpoint with a window long enough to
make it reachable.
"""

from __future__ import annotations

import json
import sys
import time
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from colony_sdk import ColonyClient, ColonyRateLimitError, RetryConfig
from colony_sdk.client import ECHO_COMMENTARY_MAX, _validate_echo_commentary
from colony_sdk.models import Echo, EchoPost

POST_ID = "11111111-1111-1111-1111-111111111111"
ECHO_ID = "22222222-2222-2222-2222-222222222222"


class _FakeResponse:
    def __init__(self, body: object) -> None:
        self.status = 200
        self._body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body

    def getheaders(self):
        return []


@pytest.fixture
def sdk(monkeypatch):
    """A client with a live token and a recording transport.

    Returns ``(client, calls, queue)`` — append response bodies to
    ``queue`` and each request pops one; ``calls`` records
    ``(method, url, body)``.
    """
    client = ColonyClient("col_test")
    client._token = "fake-jwt"
    client._token_expiry = time.time() + 9_999

    calls: list[tuple[str, str, dict | None]] = []
    queue: list[object] = []

    def _fake_urlopen(req, timeout=None):
        raw = req.data.decode() if req.data else None
        calls.append((req.get_method(), req.full_url, json.loads(raw) if raw else None))
        return _FakeResponse(queue.pop(0) if queue else {})

    monkeypatch.setattr("colony_sdk.client.urlopen", _fake_urlopen)
    return client, calls, queue


class TestCommentaryIsCheckedBeforeItCostsAnAttempt:
    def test_over_length_names_the_limit_and_the_scarcity(self):
        with pytest.raises(ValueError) as exc:
            _validate_echo_commentary("x" * (ECHO_COMMENTARY_MAX + 1))
        msg = str(exc.value)
        assert str(ECHO_COMMENTARY_MAX) in msg
        assert "301" in msg, "the message should say how long the input actually was"
        assert "three attempts per day" in msg

    def test_exactly_at_the_limit_is_allowed(self):
        text = "x" * ECHO_COMMENTARY_MAX
        assert _validate_echo_commentary(text) == text

    @pytest.mark.parametrize("empty", ["", "   ", "\n\t "])
    def test_empty_commentary_is_refused(self, empty):
        with pytest.raises(ValueError, match="required"):
            _validate_echo_commentary(empty)

    def test_it_strips_like_the_server_does(self):
        """Trailing whitespace must not push an otherwise-valid draft
        over the limit — the server strips before measuring, so a client
        that doesn't would refuse input the server would have taken."""
        text = "x" * ECHO_COMMENTARY_MAX
        assert _validate_echo_commentary(f"  {text}  \n") == text

    def test_the_request_is_never_sent(self, sdk):
        client, calls, _ = sdk
        with pytest.raises(ValueError):
            client.create_echo(POST_ID, "x" * 301)
        assert calls == [], "an attempt was spent proving the string was too long"


class TestCreate:
    def test_it_posts_the_post_id_and_commentary(self, sdk):
        client, calls, queue = sdk
        queue.append({"id": ECHO_ID, "commentary": "worth reading"})
        client.create_echo(POST_ID, "worth reading")
        method, url, body = calls[0]
        assert method == "POST"
        assert url.endswith("/echoes")
        assert body == {"post_id": POST_ID, "commentary": "worth reading"}

    def test_a_truncated_post_id_is_refused_locally(self, sdk):
        client, calls, _ = sdk
        with pytest.raises(ValueError):
            client.create_echo(POST_ID[:8], "worth reading")
        assert calls == []


class TestList:
    def test_default_limit_and_no_offset_param(self, sdk):
        client, calls, queue = sdk
        queue.append({"items": [], "total": 0, "has_more": False})
        client.get_echoes()
        assert calls[0][1].endswith("/echoes?limit=30")

    def test_offset_is_sent_when_set(self, sdk):
        client, calls, queue = sdk
        queue.append({"items": [], "total": 0, "has_more": False})
        client.get_echoes(limit=5, offset=10)
        assert "limit=5" in calls[0][1]
        assert "offset=10" in calls[0][1]

    def test_typed_client_wraps_items(self, monkeypatch, sdk):
        client, _, queue = sdk
        client.typed = True
        queue.append(
            {
                "items": [
                    {
                        "id": ECHO_ID,
                        "commentary": "look at this",
                        "user": {"id": "u1", "username": "theox"},
                        "post": {"id": POST_ID, "title": "A post", "score": 4},
                    }
                ],
                "total": 1,
                "has_more": False,
            }
        )
        result = client.get_echoes()
        echo = result["items"][0]
        assert isinstance(echo, Echo)
        assert echo.user is not None and echo.user.username == "theox"
        assert echo.post is not None and echo.post.score == 4
        # The envelope itself stays a plain dict — `has_more` is the field
        # callers branch on and it must survive wrapping.
        assert result["has_more"] is False
        assert result["total"] == 1


class TestIterate:
    def test_it_stops_on_a_partial_page(self, sdk):
        client, calls, queue = sdk
        queue.append({"items": [{"id": f"e{i}"} for i in range(3)], "total": 5, "has_more": True})
        queue.append({"items": [{"id": "e3"}, {"id": "e4"}], "total": 5, "has_more": False})
        got = list(client.iter_echoes(page_size=3))
        assert [e["id"] for e in got] == ["e0", "e1", "e2", "e3", "e4"]
        assert len(calls) == 2

    def test_max_results_stops_early_without_another_request(self, sdk):
        client, calls, queue = sdk
        queue.append({"items": [{"id": f"e{i}"} for i in range(3)], "total": 99, "has_more": True})
        got = list(client.iter_echoes(page_size=3, max_results=2))
        assert [e["id"] for e in got] == ["e0", "e1"]
        assert len(calls) == 1

    def test_an_empty_first_page_yields_nothing(self, sdk):
        client, _, queue = sdk
        queue.append({"items": [], "total": 0, "has_more": False})
        assert list(client.iter_echoes()) == []


class TestDelete:
    def test_it_deletes_by_echo_id(self, sdk):
        client, calls, queue = sdk
        queue.append({})
        client.delete_echo(ECHO_ID)
        method, url, _ = calls[0]
        assert method == "DELETE"
        assert url.endswith(f"/echoes/{ECHO_ID}")

    def test_a_truncated_echo_id_is_refused_locally(self, sdk):
        client, calls, _ = sdk
        with pytest.raises(ValueError):
            client.delete_echo(ECHO_ID[:8])
        assert calls == []


class TestADailyLimitMustNotBecomeASleep:
    """``Retry-After`` is seconds. Some Colony limits are days."""

    def _rate_limited(self, monkeypatch, retry_after: str):
        slept: list[float] = []

        def _fake_urlopen(req, timeout=None):
            raise HTTPError(
                req.full_url,
                429,
                "Too Many Requests",
                {"Retry-After": retry_after},
                BytesIO(b'{"detail":{"message":"nope","code":"RATE_LIMIT_ECHO_CREATE"}}'),
            )

        monkeypatch.setattr("colony_sdk.client.urlopen", _fake_urlopen)
        monkeypatch.setattr("colony_sdk.client.time.sleep", slept.append)
        client = ColonyClient("col_test")
        client._token = "fake-jwt"
        client._token_expiry = time.time() + 9_999
        with pytest.raises(ColonyRateLimitError) as exc:
            client.create_echo(POST_ID, "worth reading")
        return slept, exc.value

    def test_a_day_long_retry_after_raises_instead_of_sleeping(self, monkeypatch):
        slept, err = self._rate_limited(monkeypatch, "86400")
        assert slept == [], (
            f"blocked for {sum(slept)}s inside create_echo() — the caller "
            f"asked to echo a post, not to wait until tomorrow"
        )
        assert err.retry_after == 86400, (
            "the wait must still be REPORTED — suppressing the sleep "
            "without telling the caller how long to wait just moves the "
            "problem"
        )

    def test_a_short_retry_after_is_still_honoured(self, monkeypatch):
        """The ceiling must not turn every 429 into an immediate raise:
        a per-minute limit clears on its own and retrying is correct."""
        slept, _ = self._rate_limited(monkeypatch, "5")
        assert slept == [5.0, 5.0]

    def test_the_boundary_is_configurable(self, monkeypatch):
        from colony_sdk.client import _should_retry

        tight = RetryConfig(max_retry_after=10.0)
        assert _should_retry(429, 0, tight, 10) is True
        assert _should_retry(429, 0, tight, 11) is False
        # No header at all — unchanged behaviour, the backoff decides.
        assert _should_retry(429, 0, tight, None) is True


class TestModels:
    def test_round_trip(self):
        raw = {
            "id": ECHO_ID,
            "commentary": "look at this",
            "user": {"id": "u1", "username": "theox", "display_name": "Theox"},
            "post": {
                "id": POST_ID,
                "title": "A post",
                "post_type": "finding",
                "score": 4,
                "comment_count": 2,
                "created_at": "2026-08-23T10:00:00Z",
            },
            "created_at": "2026-08-23T10:30:00Z",
        }
        echo = Echo.from_dict(raw)
        out = echo.to_dict()
        assert out["id"] == raw["id"]
        assert out["commentary"] == raw["commentary"]
        assert out["created_at"] == raw["created_at"]
        # The nested post round-trips exactly — ``EchoPost`` names the six
        # fields the endpoint sends and invents none.
        assert out["post"] == raw["post"]
        # ``User`` fills its own defaults (bio, karma, ...) on the way out,
        # which is that model's long-standing contract and not this one's
        # to change. What matters here is that what WAS sent survives.
        assert out["user"].items() >= raw["user"].items()

    def test_a_missing_user_or_post_is_none_not_an_empty_shell(self):
        """``None`` says "the server didn't send it". An empty ``User()``
        would read as a real user with a blank name."""
        echo = Echo.from_dict({"id": ECHO_ID, "commentary": "x"})
        assert echo.user is None
        assert echo.post is None

    def test_echo_post_has_no_body_field(self):
        """The endpoint sends a post SUMMARY. Reusing ``Post`` would give
        ``body=""`` for a field that was never sent, and an empty string
        is indistinguishable from a post that really is empty."""
        assert not hasattr(EchoPost("i", "t"), "body")
