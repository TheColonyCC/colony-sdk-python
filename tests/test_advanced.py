"""Tests for advanced features: proxy, idempotency, hooks, circuit breaker, cache, batch."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from colony_sdk import ColonyClient, ColonyNetworkError

# ── Helpers ──────────────────────────────────────────────────────────


def _make_client(**kwargs):
    client = ColonyClient("col_test", **kwargs)
    client._token = "fake"
    client._token_expiry = 9999999999
    return client


def _mock_response(data: dict):
    class FakeResponse:
        def __init__(self):
            self._data = json.dumps(data).encode()

        def read(self):
            return self._data

        def getheaders(self):
            return [("Content-Type", "application/json")]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    return FakeResponse()


# ── Proxy ────────────────────────────────────────────────────────────


class TestProxy:
    def test_proxy_param_stored(self) -> None:
        client = ColonyClient("col_test", proxy="http://proxy:8080")
        assert client.proxy == "http://proxy:8080"

    def test_no_proxy_by_default(self) -> None:
        client = ColonyClient("col_test")
        assert client.proxy is None

    def test_proxy_handler_used(self) -> None:
        """Verify that when a proxy is set, build_opener is called and used."""
        client = _make_client(proxy="http://proxy.test:8080")

        captured_handlers: list = []

        class FakeOpener:
            def open(self, req: object, timeout: object = None) -> object:
                return _mock_response({"id": "u1", "username": "alice"})

        def fake_build_opener(handler: object) -> object:
            captured_handlers.append(handler)
            return FakeOpener()

        with patch("urllib.request.build_opener", side_effect=fake_build_opener):
            result = client.get_me()

        # build_opener was called once with a ProxyHandler
        assert len(captured_handlers) == 1
        import urllib.request

        assert isinstance(captured_handlers[0], urllib.request.ProxyHandler)
        assert result["username"] == "alice"


# ── Hooks ────────────────────────────────────────────────────────────


class TestHooks:
    def test_on_request_called(self) -> None:
        client = _make_client()
        calls: list = []
        client.on_request(lambda method, url, body: calls.append((method, url)))

        with patch("colony_sdk.client.urlopen", return_value=_mock_response({"id": "u1"})):
            client.get_me()

        assert len(calls) == 1
        assert calls[0][0] == "GET"
        assert "/users/me" in calls[0][1]

    def test_on_response_called(self) -> None:
        client = _make_client()
        calls: list = []
        client.on_response(lambda method, url, status, data: calls.append((method, status, data)))

        with patch("colony_sdk.client.urlopen", return_value=_mock_response({"id": "u1"})):
            client.get_me()

        assert len(calls) == 1
        assert calls[0][1] == 200
        assert calls[0][2]["id"] == "u1"

    def test_multiple_hooks(self) -> None:
        client = _make_client()
        calls: list = []
        client.on_request(lambda m, u, b: calls.append("hook1"))
        client.on_request(lambda m, u, b: calls.append("hook2"))

        with patch("colony_sdk.client.urlopen", return_value=_mock_response({})):
            client.get_me()

        assert calls == ["hook1", "hook2"]


# ── Circuit Breaker ──────────────────────────────────────────────────


class TestCircuitBreaker:
    def test_disabled_by_default(self) -> None:
        client = _make_client()
        assert client._circuit_breaker_threshold == 0

    def test_enable(self) -> None:
        client = _make_client()
        client.enable_circuit_breaker(3)
        assert client._circuit_breaker_threshold == 3
        assert client._consecutive_failures == 0

    def test_opens_after_threshold(self) -> None:
        client = _make_client()
        client.enable_circuit_breaker(2)
        client._consecutive_failures = 2  # Simulate 2 failures

        with pytest.raises(ColonyNetworkError, match="Circuit breaker open"):
            client.get_me()

    def test_resets_on_success(self) -> None:
        client = _make_client()
        client.enable_circuit_breaker(5)
        client._consecutive_failures = 3

        with patch("colony_sdk.client.urlopen", return_value=_mock_response({"id": "u1"})):
            client.get_me()

        assert client._consecutive_failures == 0

    def test_disable(self) -> None:
        client = _make_client()
        client.enable_circuit_breaker(5)
        client.enable_circuit_breaker(0)  # Disable
        client._consecutive_failures = 100

        # Should not raise even with many failures
        with patch("colony_sdk.client.urlopen", return_value=_mock_response({"id": "u1"})):
            client.get_me()


# ── Cache ────────────────────────────────────────────────────────────


class TestCache:
    def test_disabled_by_default(self) -> None:
        client = _make_client()
        assert client._cache_ttl == 0

    def test_enable(self) -> None:
        client = _make_client()
        client.enable_cache(30.0)
        assert client._cache_ttl == 30.0

    def test_get_cached(self) -> None:
        client = _make_client()
        client.enable_cache(60.0)
        call_count = 0

        def counting_urlopen(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _mock_response({"id": "u1", "username": "agent"})

        with patch("colony_sdk.client.urlopen", side_effect=counting_urlopen):
            result1 = client.get_me()
            result2 = client.get_me()  # Should be cached

        assert call_count == 1  # Only one real call
        assert result1 == result2

    def test_write_invalidates_cache(self) -> None:
        client = _make_client()
        client.enable_cache(60.0)
        call_count = 0

        def counting_urlopen(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _mock_response({"id": "p1"})

        with patch("colony_sdk.client.urlopen", side_effect=counting_urlopen):
            client.get_me()  # Cached
            client.create_post("Title", "Body")  # Invalidates cache
            client.get_me()  # Must fetch again

        assert call_count == 3

    def test_clear_cache(self) -> None:
        client = _make_client()
        client.enable_cache(60.0)

        with patch("colony_sdk.client.urlopen", return_value=_mock_response({"id": "u1"})):
            client.get_me()

        assert len(client._cache) > 0
        client.clear_cache()
        assert len(client._cache) == 0

    def test_expired_cache_refetches(self) -> None:
        client = _make_client()
        client.enable_cache(0.01)  # 10ms TTL
        call_count = 0

        def counting_urlopen(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _mock_response({"id": "u1"})

        with patch("colony_sdk.client.urlopen", side_effect=counting_urlopen):
            client.get_me()
            time.sleep(0.02)  # Wait for TTL to expire
            client.get_me()  # Should refetch

        assert call_count == 2


# ── Batch Helpers ────────────────────────────────────────────────────


class TestBatchHelpers:
    def test_get_posts_by_ids(self) -> None:
        client = _make_client()
        responses = [
            _mock_response({"id": "p1", "title": "Post 1", "body": "B1"}),
            _mock_response({"id": "p2", "title": "Post 2", "body": "B2"}),
        ]

        with patch("colony_sdk.client.urlopen", side_effect=responses):
            results = client.get_posts_by_ids(["p1", "p2"])

        assert len(results) == 2
        assert results[0]["id"] == "p1"
        assert results[1]["id"] == "p2"

    def test_get_posts_by_ids_skips_404(self) -> None:
        from urllib.error import HTTPError

        client = _make_client()

        def side_effect(*args, **kwargs):
            req = args[0]
            if "p2" in req.full_url:
                err = HTTPError(req.full_url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
                err.read = lambda: b'{"detail": "Not found"}'  # type: ignore[assignment]
                raise err
            return _mock_response({"id": "p1", "title": "Post 1", "body": "B1"})

        with patch("colony_sdk.client.urlopen", side_effect=side_effect):
            results = client.get_posts_by_ids(["p1", "p2"])

        assert len(results) == 1

    def test_get_users_by_ids(self) -> None:
        client = _make_client()
        responses = [
            _mock_response({"id": "u1", "username": "alice"}),
            _mock_response({"id": "u2", "username": "bob"}),
        ]

        with patch("colony_sdk.client.urlopen", side_effect=responses):
            results = client.get_users_by_ids(["u1", "u2"])

        assert len(results) == 2
        assert results[0]["username"] == "alice"

    def test_get_users_by_ids_skips_404(self) -> None:
        from urllib.error import HTTPError

        client = _make_client()

        def side_effect(*args, **kwargs):
            req = args[0]
            if "u2" in req.full_url:
                err = HTTPError(req.full_url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
                err.read = lambda: b'{"detail": "Not found"}'  # type: ignore[assignment]
                raise err
            return _mock_response({"id": "u1", "username": "alice"})

        with patch("colony_sdk.client.urlopen", side_effect=side_effect):
            results = client.get_users_by_ids(["u1", "u2"])

        assert len(results) == 1


# ── Idempotency ──────────────────────────────────────────────────────


class TestIdempotency:
    def test_idempotency_key_sent_on_post(self) -> None:
        client = _make_client()
        captured_headers: dict = {}

        def capture_urlopen(req, **kwargs):
            captured_headers.update(dict(req.headers))
            return _mock_response({"id": "p1"})

        with patch("colony_sdk.client.urlopen", side_effect=capture_urlopen):
            client._raw_request("POST", "/posts", body={"title": "T"}, idempotency_key="key-123")

        assert captured_headers.get("X-idempotency-key") == "key-123"

    def test_idempotency_key_not_sent_on_get(self) -> None:
        client = _make_client()
        captured_headers: dict = {}

        def capture_urlopen(req, **kwargs):
            captured_headers.update(dict(req.headers))
            return _mock_response({"id": "u1"})

        with patch("colony_sdk.client.urlopen", side_effect=capture_urlopen):
            client._raw_request("GET", "/users/me", idempotency_key="key-123")

        assert "X-idempotency-key" not in captured_headers


# ── py.typed ─────────────────────────────────────────────────────────


class TestAsyncCircuitBreaker:
    def test_enable(self) -> None:
        from colony_sdk import AsyncColonyClient

        client = AsyncColonyClient("col_test")
        client.enable_circuit_breaker(3)
        assert client._circuit_breaker_threshold == 3

    @pytest.mark.asyncio
    async def test_opens_after_threshold(self) -> None:
        from colony_sdk import AsyncColonyClient

        client = AsyncColonyClient("col_test")
        client.enable_circuit_breaker(2)
        client._consecutive_failures = 2

        with pytest.raises(ColonyNetworkError, match="Circuit breaker open"):
            await client.get_me()


class TestAsyncHooks:
    def test_register_hooks(self) -> None:
        from colony_sdk import AsyncColonyClient

        client = AsyncColonyClient("col_test")
        calls: list = []
        client.on_request(lambda m, u, b: calls.append("req"))
        client.on_response(lambda m, u, s, d: calls.append("resp"))
        assert len(client._on_request) == 1
        assert len(client._on_response) == 1

    @pytest.mark.asyncio
    async def test_on_request_called(self) -> None:
        import httpx

        from colony_sdk import AsyncColonyClient

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "u1", "username": "alice"})

        transport = httpx.MockTransport(mock_handler)
        calls: list = []
        async with AsyncColonyClient("col_test", client=httpx.AsyncClient(transport=transport)) as client:
            client._token = "fake"
            client._token_expiry = 9999999999
            client.on_request(lambda m, u, b: calls.append((m, u)))
            await client.get_me()

        assert len(calls) == 1
        assert calls[0][0] == "GET"
        assert "/users/me" in calls[0][1]

    @pytest.mark.asyncio
    async def test_on_response_called(self) -> None:
        import httpx

        from colony_sdk import AsyncColonyClient

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "u1", "username": "alice"})

        transport = httpx.MockTransport(mock_handler)
        calls: list = []
        async with AsyncColonyClient("col_test", client=httpx.AsyncClient(transport=transport)) as client:
            client._token = "fake"
            client._token_expiry = 9999999999
            client.on_response(lambda m, u, s, d: calls.append((m, s, d)))
            await client.get_me()

        assert len(calls) == 1
        assert calls[0][1] == 200
        assert calls[0][2]["username"] == "alice"


class TestAsyncBatchHelpers:
    @pytest.mark.asyncio
    async def test_get_posts_by_ids(self) -> None:
        import httpx

        from colony_sdk import AsyncColonyClient

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            if "p1" in str(request.url):
                return httpx.Response(200, json={"id": "p1", "title": "Post 1"})
            return httpx.Response(200, json={"id": "p2", "title": "Post 2"})

        transport = httpx.MockTransport(mock_handler)
        async with AsyncColonyClient("col_test", client=httpx.AsyncClient(transport=transport)) as client:
            client._token = "fake"
            client._token_expiry = 9999999999
            results = await client.get_posts_by_ids(["p1", "p2"])

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_get_posts_by_ids_skips_404(self) -> None:
        import httpx

        from colony_sdk import AsyncColonyClient, RetryConfig

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            if "p2" in str(request.url):
                return httpx.Response(404, json={"detail": "Not found"})
            return httpx.Response(200, json={"id": "p1", "title": "Post 1"})

        transport = httpx.MockTransport(mock_handler)
        async with AsyncColonyClient(
            "col_test",
            client=httpx.AsyncClient(transport=transport),
            retry=RetryConfig(max_retries=0),
        ) as client:
            client._token = "fake"
            client._token_expiry = 9999999999
            results = await client.get_posts_by_ids(["p1", "p2"])

        assert len(results) == 1
        assert results[0]["id"] == "p1"

    @pytest.mark.asyncio
    async def test_get_users_by_ids_skips_404(self) -> None:
        import httpx

        from colony_sdk import AsyncColonyClient

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            if "u2" in str(request.url):
                return httpx.Response(404, json={"detail": "Not found"})
            return httpx.Response(200, json={"id": "u1", "username": "alice"})

        from colony_sdk import RetryConfig

        transport = httpx.MockTransport(mock_handler)
        async with AsyncColonyClient(
            "col_test",
            client=httpx.AsyncClient(transport=transport),
            retry=RetryConfig(max_retries=0),
        ) as client:
            client._token = "fake"
            client._token_expiry = 9999999999
            results = await client.get_users_by_ids(["u1", "u2"])

        assert len(results) == 1
        assert results[0]["username"] == "alice"


class TestPyTyped:
    def test_marker_exists(self) -> None:
        import importlib.resources

        # py.typed should be accessible as a package resource
        files = importlib.resources.files("colony_sdk")
        py_typed = files / "py.typed"
        assert py_typed.is_file()
