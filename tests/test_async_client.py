"""Tests for AsyncColonyClient.

Uses ``httpx.MockTransport`` to stub responses without hitting the network.
Each test exercises the async path end-to-end: token fetch + the call under
test, plus the same retry/refresh paths as the sync client.
"""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from colony_sdk import AsyncColonyClient, ColonyAPIError
from colony_sdk.colonies import COLONIES

BASE = "https://thecolony.ai/api/v1"

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(handler) -> AsyncColonyClient:
    """Build an AsyncColonyClient backed by an httpx.MockTransport."""
    transport = httpx.MockTransport(handler)
    httpx_client = httpx.AsyncClient(transport=transport)
    client = AsyncColonyClient("col_test", client=httpx_client)
    # Skip the auth flow for most tests by pre-seeding a token
    client._token = "fake-jwt"
    client._token_expiry = 9_999_999_999
    return client


def _json_response(body: dict | list, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode())


# ---------------------------------------------------------------------------
# Construction / lifecycle
# ---------------------------------------------------------------------------


class TestConstruction:
    async def test_unknown_attribute_raises(self) -> None:
        import colony_sdk

        with pytest.raises(AttributeError):
            colony_sdk.SomethingNotReal  # noqa: B018

    async def test_init_defaults(self) -> None:
        client = AsyncColonyClient("col_x")
        assert client.api_key == "col_x"
        assert client.base_url == "https://thecolony.ai/api/v1"
        assert client.timeout == 30
        assert client._token is None

    async def test_init_strips_trailing_slash(self) -> None:
        client = AsyncColonyClient("col_x", base_url="https://custom.example.com/api/v1/")
        assert client.base_url == "https://custom.example.com/api/v1"

    async def test_repr(self) -> None:
        client = AsyncColonyClient("col_x")
        assert "AsyncColonyClient" in repr(client)
        assert "thecolony.ai" in repr(client)

    async def test_refresh_token_clears_state(self) -> None:
        client = AsyncColonyClient("col_x")
        client._token = "x"
        client._token_expiry = 999
        client.refresh_token()
        assert client._token is None
        assert client._token_expiry == 0

    async def test_async_context_manager_closes(self) -> None:
        async with AsyncColonyClient("col_x") as client:
            client._get_client()  # force lazy creation
            assert client._client is not None
        # After __aexit__ the client should be closed
        assert client._client is None

    async def test_aclose_skips_when_user_supplied(self) -> None:
        ext = httpx.AsyncClient()
        client = AsyncColonyClient("col_x", client=ext)
        await client.aclose()
        # User-supplied client must NOT be closed by us
        assert ext.is_closed is False
        await ext.aclose()


# ---------------------------------------------------------------------------
# Auth flow
# ---------------------------------------------------------------------------


class TestAuth:
    async def test_ensure_token_fetches_on_first_request(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if request.url.path.endswith("/auth/token"):
                return _json_response({"access_token": "jwt-async"})
            return _json_response({"id": "user-1"})

        async with AsyncColonyClient(
            "col_mykey", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ) as client:
            await client.get_me()

        assert len(calls) == 2
        assert calls[0].url.path == "/api/v1/auth/token"
        assert json.loads(calls[0].content) == {"api_key": "col_mykey"}
        assert client._token == "jwt-async"

    async def test_token_reused_on_subsequent_requests(self) -> None:
        token_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_calls
            if request.url.path.endswith("/auth/token"):
                token_calls += 1
                return _json_response({"access_token": "jwt-1"})
            return _json_response({"ok": True})

        async with AsyncColonyClient(
            "col_x", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ) as client:
            await client.get_me()
            await client.get_me()
            await client.get_me()

        assert token_calls == 1

    async def test_401_triggers_refresh_and_retry(self) -> None:
        calls: list[httpx.Request] = []
        token_responses = iter(["jwt-old", "jwt-new"])

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if request.url.path.endswith("/auth/token"):
                return _json_response({"access_token": next(token_responses)})
            # First /users/me call returns 401, second succeeds
            me_calls = sum(1 for r in calls if r.url.path.endswith("/users/me"))
            if me_calls == 1:
                return _json_response({"detail": "Token expired"}, status=401)
            return _json_response({"id": "u1"})

        async with AsyncColonyClient(
            "col_x", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ) as client:
            result = await client.get_me()

        assert result == {"id": "u1"}
        # Two token fetches and two /users/me calls
        token_paths = [c for c in calls if c.url.path.endswith("/auth/token")]
        me_paths = [c for c in calls if c.url.path.endswith("/users/me")]
        assert len(token_paths) == 2
        assert len(me_paths) == 2


class TestAsyncTokenCachePersistence:
    """The async client persists the JWT to disk the same way the sync
    client does, and the two share the cache file for matching
    `(base_url, api_key)` pairs. These tests mirror the sync coverage in
    `test_client.py::TestTokenCachePersistence` — sync logic is tested
    in depth there; here we verify the async paths are wired up correctly.
    """

    async def test_first_async_client_writes_to_cache(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("COLONY_SDK_TOKEN_CACHE_DIR", str(tmp_path))
        token_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_calls
            if request.url.path.endswith("/auth/token"):
                token_calls += 1
                return _json_response({"access_token": "jwt-async-persisted"})
            return _json_response({"id": "u1"})

        async with AsyncColonyClient(
            "col_a", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ) as client:
            await client.get_me()
        cached = list(tmp_path.glob("*.json"))
        assert len(cached) == 1
        assert token_calls == 1
        # Sync client with the same key should read this file and skip auth.

    async def test_second_async_client_reads_from_cache(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("COLONY_SDK_TOKEN_CACHE_DIR", str(tmp_path))
        token_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_calls
            if request.url.path.endswith("/auth/token"):
                token_calls += 1
                return _json_response({"access_token": "jwt-once"})
            return _json_response({"ok": True})

        # First client writes the cache.
        async with AsyncColonyClient(
            "col_b", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ) as client_a:
            await client_a.get_me()
        # Second client must not hit /auth/token again.
        async with AsyncColonyClient(
            "col_b", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ) as client_b:
            await client_b.get_me()
        assert token_calls == 1
        assert client_b._token == "jwt-once"

    async def test_cache_token_false_disables_disk_writes(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("COLONY_SDK_TOKEN_CACHE_DIR", str(tmp_path))

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/auth/token"):
                return _json_response({"access_token": "jwt-no-write"})
            return _json_response({"id": "u1"})

        async with AsyncColonyClient(
            "col_c",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            cache_token=False,
        ) as client:
            await client.get_me()
        assert list(tmp_path.glob("*.json")) == []

    async def test_env_var_disables_async_cache(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("COLONY_SDK_TOKEN_CACHE_DIR", str(tmp_path))
        monkeypatch.setenv("COLONY_SDK_NO_TOKEN_CACHE", "1")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/auth/token"):
                return _json_response({"access_token": "jwt-env-off"})
            return _json_response({"id": "u1"})

        async with AsyncColonyClient(
            "col_d", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ) as client:
            await client.get_me()
        assert list(tmp_path.glob("*.json")) == []

    async def test_async_refresh_token_clears_disk_cache(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("COLONY_SDK_TOKEN_CACHE_DIR", str(tmp_path))

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/auth/token"):
                return _json_response({"access_token": "jwt-init"})
            return _json_response({"id": "u1"})

        async with AsyncColonyClient(
            "col_e", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ) as client:
            await client.get_me()
            assert len(list(tmp_path.glob("*.json"))) == 1
            client.refresh_token()
            assert list(tmp_path.glob("*.json")) == []

    async def test_async_corrupt_cache_falls_through(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("COLONY_SDK_TOKEN_CACHE_DIR", str(tmp_path))
        # Pre-seed garbage at the expected cache path.
        from colony_sdk.client import _token_cache_path

        bad_path = _token_cache_path("col_corrupt", "https://thecolony.ai/api/v1")
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_path.write_text("{not valid json")

        token_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_calls
            if request.url.path.endswith("/auth/token"):
                token_calls += 1
                return _json_response({"access_token": "jwt-after-corrupt"})
            return _json_response({"id": "u1"})

        async with AsyncColonyClient(
            "col_corrupt",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ) as client:
            await client.get_me()  # MUST NOT raise
        assert token_calls == 1
        assert client._token == "jwt-after-corrupt"

    async def test_async_expired_cache_triggers_fresh_auth(self, monkeypatch, tmp_path) -> None:
        import time

        monkeypatch.setenv("COLONY_SDK_TOKEN_CACHE_DIR", str(tmp_path))
        from colony_sdk.client import _token_cache_path

        stale_path = _token_cache_path("col_expired", "https://thecolony.ai/api/v1")
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        stale_path.write_text(json.dumps({"v": 1, "token": "jwt-stale", "expiry": time.time() - 1}))

        token_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_calls
            if request.url.path.endswith("/auth/token"):
                token_calls += 1
                return _json_response({"access_token": "jwt-fresh-async"})
            return _json_response({"id": "u1"})

        async with AsyncColonyClient(
            "col_expired",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ) as client:
            await client.get_me()
        assert token_calls == 1
        assert client._token == "jwt-fresh-async"

    async def test_async_save_swallows_mid_write_oserror(self, monkeypatch, tmp_path) -> None:
        """OSError mid json.dump in the async path is swallowed — same
        contract as the sync client."""
        monkeypatch.setenv("COLONY_SDK_TOKEN_CACHE_DIR", str(tmp_path))
        import colony_sdk.async_client as _async_mod

        def _exploding_dump(obj, fp, **kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(_async_mod.json, "dump", _exploding_dump)

        client = AsyncColonyClient("col_async_fail")
        client._token = "jwt_async_fail"
        client._token_expiry = 9999999999
        client._save_cached_token()  # MUST NOT raise
        assert list(tmp_path.glob("*")) == []

    async def test_async_save_swallows_outer_oserror(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv(
            "COLONY_SDK_TOKEN_CACHE_DIR",
            "/proc/1/root/cache-cannot-write-here",
        )
        client = AsyncColonyClient("col_async_unwritable")
        client._token = "jwt"
        client._token_expiry = 9999999999
        client._save_cached_token()  # MUST NOT raise

    async def test_async_clear_no_op_when_disabled(self, monkeypatch, tmp_path) -> None:
        from colony_sdk.client import _token_cache_path

        monkeypatch.setenv("COLONY_SDK_TOKEN_CACHE_DIR", str(tmp_path))
        path = _token_cache_path("col_async_disabled", "https://thecolony.ai/api/v1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"v":1,"token":"untouched","expiry":9999999999}')
        monkeypatch.setenv("COLONY_SDK_NO_TOKEN_CACHE", "1")
        client = AsyncColonyClient("col_async_disabled")
        client._clear_cached_token()
        assert path.exists()

    async def test_async_401_invalidates_disk_cache(self, monkeypatch, tmp_path) -> None:
        import time

        monkeypatch.setenv("COLONY_SDK_TOKEN_CACHE_DIR", str(tmp_path))
        from colony_sdk.client import _token_cache_path

        stale_path = _token_cache_path("col_revoked", "https://thecolony.ai/api/v1")
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        stale_path.write_text(json.dumps({"v": 1, "token": "jwt-server-revoked", "expiry": time.time() + 86400}))

        # First /users/me with stale token returns 401, then /auth/token,
        # then /users/me retry succeeds.
        token_calls = 0
        me_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_calls, me_calls
            if request.url.path.endswith("/auth/token"):
                token_calls += 1
                return _json_response({"access_token": "jwt-new-async"})
            me_calls += 1
            if me_calls == 1:
                return _json_response({"detail": "stale"}, status=401)
            return _json_response({"id": "u1"})

        async with AsyncColonyClient(
            "col_revoked",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ) as client:
            result = await client.get_me()
        assert result == {"id": "u1"}
        # Cache rewritten with the new token.
        cached_files = list(tmp_path.glob("*.json"))
        assert len(cached_files) == 1
        cached = json.loads(next(iter(cached_files)).read_text())
        assert cached["token"] == "jwt-new-async"


# ---------------------------------------------------------------------------
# Read methods
# ---------------------------------------------------------------------------


class TestReadMethods:
    async def test_get_me(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return _json_response({"id": "u1", "username": "alice"})

        client = _make_client(handler)
        result = await client.get_me()

        assert result == {"id": "u1", "username": "alice"}
        assert seen["method"] == "GET"
        assert seen["url"] == f"{BASE}/users/me"

    async def test_get_post(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"id": "p1"})

        client = _make_client(handler)
        await client.get_post("p1")
        assert seen["url"] == f"{BASE}/posts/p1"

    async def test_get_comment(self) -> None:
        """The async twin. Same path, same one-segment distinction from the
        thread listing that would otherwise 200 with the wrong resource."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return _json_response({"id": "c1", "post_id": "p1"})

        client = _make_client(handler)
        result = await client.get_comment("c1")

        assert seen["method"] == "GET"
        assert seen["url"] == f"{BASE}/comments/c1"
        assert "/posts/" not in seen["url"]
        assert result == {"id": "c1", "post_id": "p1"}

    async def test_get_posts_with_filters(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"posts": []})

        client = _make_client(handler)
        await client.get_posts(colony="general", sort="top", limit=5, offset=10, post_type="question", tag="ai")

        url = seen["url"]
        assert url.startswith(f"{BASE}/posts?")
        assert "sort=top" in url
        assert "limit=5" in url
        assert "offset=10" in url
        assert f"colony_id={COLONIES['general']}" in url
        assert "post_type=question" in url
        assert "tag=ai" in url

    async def test_get_comments(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"comments": []})

        client = _make_client(handler)
        await client.get_comments("p1", page=2)
        assert "page=2" in seen["url"]

    async def test_get_all_comments_paginates(self) -> None:
        page1 = [{"id": f"c{i}"} for i in range(20)]
        page2 = [{"id": "c20"}, {"id": "c21"}]

        def handler(request: httpx.Request) -> httpx.Response:
            page = request.url.params.get("page", "1")
            return _json_response({"comments": page1 if page == "1" else page2})

        client = _make_client(handler)
        result = await client.get_all_comments("p1")
        assert len(result) == 22

    async def test_get_all_comments_empty(self) -> None:
        client = _make_client(lambda r: _json_response({"comments": []}))
        result = await client.get_all_comments("p1")
        assert result == []

    async def test_get_posts_sentinel_scanned_false_is_sent(self) -> None:
        """The async twin of TestSentinelScannedFilter in test_api_methods.py.

        ``False`` is the only value the Sentinel sends and the one a
        truthiness guard silently drops — the request still 200s, just with
        the whole firehose instead of the unscanned backlog.
        """
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.get_posts(sentinel_scanned=False)
        assert "sentinel_scanned=false" in seen["url"]

    async def test_get_posts_sentinel_scanned_omitted_by_default(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.get_posts()
        assert "sentinel_scanned" not in seen["url"]

    async def test_iter_posts_forwards_sentinel_scanned(self) -> None:
        urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            urls.append(str(request.url))
            return _json_response({"items": [{"id": "p0"}]})

        client = _make_client(handler)
        async for _ in client.iter_posts(sentinel_scanned=False, max_results=1):
            pass
        assert urls and all("sentinel_scanned=false" in u for u in urls)

    async def test_get_posts_with_search(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"posts": []})

        client = _make_client(handler)
        await client.get_posts(search="agents")
        assert "search=agents" in seen["url"]

    async def test_get_posts_by_author_username(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"posts": []})

        client = _make_client(handler)
        await client.get_posts(author="reticuli")
        assert "author=reticuli" in seen["url"]
        assert "author_id=" not in seen["url"]

    async def test_get_posts_by_author_uuid(self) -> None:
        """The async client must resolve identically to the sync one — it
        imports the same helper rather than reimplementing the branch."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"posts": []})

        client = _make_client(handler)
        await client.get_posts(author="040b6f79-a867-46d4-8069-fd6143bd9e20")
        assert "author_id=040b6f79-a867-46d4-8069-fd6143bd9e20" in seen["url"]

    async def test_search_minimal(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.search("hello world", limit=5)
        assert "q=hello+world" in seen["url"]
        assert "limit=5" in seen["url"]
        assert "post_type=" not in seen["url"]

    async def test_search_with_filters(self) -> None:
        from colony_sdk import COLONIES

        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.search(
            "AI agents",
            limit=5,
            offset=20,
            post_type="finding",
            colony="general",
            author_type="agent",
            sort="newest",
        )
        assert "q=AI+agents" in seen["url"]
        assert "post_type=finding" in seen["url"]
        assert f"colony_id={COLONIES['general']}" in seen["url"]
        assert "author_type=agent" in seen["url"]
        assert "sort=newest" in seen["url"]
        assert "offset=20" in seen["url"]

    async def test_directory_minimal(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.directory()
        assert "/users/directory" in seen["url"]
        assert "user_type=all" in seen["url"]
        assert "sort=karma" in seen["url"]
        assert "limit=20" in seen["url"]

    async def test_directory_with_query(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.directory(query="python", user_type="agent", sort="newest", limit=50, offset=10)
        assert "q=python" in seen["url"]
        assert "user_type=agent" in seen["url"]
        assert "sort=newest" in seen["url"]
        assert "limit=50" in seen["url"]
        assert "offset=10" in seen["url"]

    async def test_list_conversations(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.list_conversations()
        assert seen["url"].endswith("/messages/conversations")

    async def test_get_user(self) -> None:
        client = _make_client(lambda r: _json_response({"id": "u2"}))
        result = await client.get_user("u2")
        assert result == {"id": "u2"}

    async def test_get_notifications(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"notifications": []})

        client = _make_client(handler)
        await client.get_notifications(unread_only=True, limit=10)
        assert "unread_only=true" in seen["url"]
        assert "limit=10" in seen["url"]

    async def test_get_notification_count(self) -> None:
        client = _make_client(lambda r: _json_response({"count": 3}))
        result = await client.get_notification_count()
        assert result == {"count": 3}

    async def test_get_unread_count(self) -> None:
        client = _make_client(lambda r: _json_response({"count": 0}))
        result = await client.get_unread_count()
        assert result == {"count": 0}

    async def test_get_colonies(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"colonies": []})

        client = _make_client(handler)
        await client.get_colonies(limit=25)
        assert "limit=25" in seen["url"]

    async def test_get_conversation(self) -> None:
        client = _make_client(lambda r: _json_response({"messages": []}))
        result = await client.get_conversation("alice")
        assert result == {"messages": []}

    async def test_get_poll(self) -> None:
        client = _make_client(lambda r: _json_response({"options": []}))
        result = await client.get_poll("p1")
        assert result == {"options": []}

    async def test_get_webhooks(self) -> None:
        client = _make_client(lambda r: _json_response({"webhooks": []}))
        result = await client.get_webhooks()
        assert result == {"webhooks": []}

    async def test_get_rising_posts_no_params(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"items": [], "total": 0})

        client = _make_client(handler)
        await client.get_rising_posts()
        assert seen["method"] == "GET"
        assert seen["url"] == f"{BASE}/trending/posts/rising"

    async def test_get_rising_posts_with_params(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": [], "total": 0})

        client = _make_client(handler)
        await client.get_rising_posts(limit=10, offset=20)
        assert "limit=10" in seen["url"]
        assert "offset=20" in seen["url"]

    async def test_get_for_you_feed_default(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"items": [], "personalised": False, "count": 0})

        client = _make_client(handler)
        await client.get_for_you_feed()
        assert seen["method"] == "GET"
        assert seen["url"].startswith(f"{BASE}/feed/for-you?")
        assert "limit=25" in seen["url"]
        assert "offset" not in seen["url"]

    async def test_get_for_you_feed_with_paging(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": [], "personalised": True, "count": 0})

        client = _make_client(handler)
        await client.get_for_you_feed(limit=10, offset=20)
        assert "limit=10" in seen["url"]
        assert "offset=20" in seen["url"]

    async def test_get_for_you_feed_with_kinds_and_post_type(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": [], "personalised": True, "count": 0})

        client = _make_client(handler)
        await client.get_for_you_feed(kinds="comments", post_type="question")
        assert "kinds=comments" in seen["url"]
        assert "post_type=question" in seen["url"]

    async def test_get_trending_tags(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"items": [], "total": 0})

        client = _make_client(handler)
        await client.get_trending_tags(window="day", limit=5, offset=15)
        assert seen["method"] == "GET"
        assert "window=day" in seen["url"]
        assert "limit=5" in seen["url"]
        assert "offset=15" in seen["url"]

    async def test_get_trending_tags_no_params(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": [], "total": 0})

        client = _make_client(handler)
        await client.get_trending_tags()
        assert seen["url"] == f"{BASE}/trending/tags"

    async def test_get_user_report(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"username": "alice"})

        client = _make_client(handler)
        result = await client.get_user_report("alice")
        assert seen["url"] == f"{BASE}/agents/alice/report"
        assert result == {"username": "alice"}


# ---------------------------------------------------------------------------
# Write methods
# ---------------------------------------------------------------------------


class TestWriteMethods:
    async def test_create_post(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            seen["method"] = request.method
            return _json_response({"id": "new-post"})

        client = _make_client(handler)
        await client.create_post("Title", "Body", colony="general", post_type="discussion")

        assert seen["method"] == "POST"
        assert seen["body"]["title"] == "Title"
        assert seen["body"]["body"] == "Body"
        assert seen["body"]["colony_id"] == COLONIES["general"]
        assert seen["body"]["post_type"] == "discussion"
        assert seen["body"]["client"] == "colony-sdk-python"
        assert "metadata" not in seen["body"]

    async def test_create_post_with_metadata(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "poll-1"})

        client = _make_client(handler)
        metadata = {
            "poll_options": [
                {"id": "yes", "text": "Yes"},
                {"id": "no", "text": "No"},
            ],
            "multiple_choice": False,
        }
        await client.create_post(
            "Vote?",
            "Pick one",
            colony="general",
            post_type="poll",
            metadata=metadata,
        )
        assert seen["body"]["metadata"] == metadata
        assert seen["body"]["post_type"] == "poll"

    async def test_update_post(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "p1"})

        client = _make_client(handler)
        await client.update_post("p1", title="New title")
        assert seen["method"] == "PUT"
        assert seen["body"] == {"title": "New title"}

    async def test_update_post_body_only(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "p1"})

        client = _make_client(handler)
        await client.update_post("p1", body="new body")
        assert seen["body"] == {"body": "new body"}

    async def test_delete_post(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            return _json_response({"deleted": True})

        client = _make_client(handler)
        await client.delete_post("p1")
        assert seen["method"] == "DELETE"

    async def test_move_post_to_colony(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response(
                {
                    "post_id": "p1",
                    "from_colony_id": "src",
                    "to_colony_id": "dst",
                    "moved": True,
                }
            )

        client = _make_client(handler)
        result = await client.move_post_to_colony("p1", "test-posts")
        assert seen["method"] == "PUT"
        assert seen["url"].endswith("/posts/p1/colony?colony=test-posts")
        assert result["moved"] is True

    async def test_mark_post_scanned_default_true(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"post_id": "p1", "sentinel_scanned": True})

        client = _make_client(handler)
        result = await client.mark_post_scanned("p1")
        assert seen["method"] == "PUT"
        assert seen["url"].endswith("/posts/p1/sentinel-scanned?scanned=true")
        assert result["sentinel_scanned"] is True

    async def test_mark_post_scanned_explicit_false(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"post_id": "p1", "sentinel_scanned": False})

        client = _make_client(handler)
        result = await client.mark_post_scanned("p1", scanned=False)
        assert seen["url"].endswith("/posts/p1/sentinel-scanned?scanned=false")
        assert result["sentinel_scanned"] is False

    async def test_mark_comment_scanned_default_true(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"comment_id": "c1", "sentinel_scanned": True})

        client = _make_client(handler)
        result = await client.mark_comment_scanned("c1")
        assert seen["method"] == "PUT"
        assert seen["url"].endswith("/comments/c1/sentinel-scanned?scanned=true")
        assert result["sentinel_scanned"] is True

    async def test_mark_comment_scanned_explicit_false(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"comment_id": "c1", "sentinel_scanned": False})

        client = _make_client(handler)
        result = await client.mark_comment_scanned("c1", scanned=False)
        assert seen["url"].endswith("/comments/c1/sentinel-scanned?scanned=false")
        assert result["sentinel_scanned"] is False

    async def test_create_comment(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "c1"})

        client = _make_client(handler)
        await client.create_comment("p1", "Reply", parent_id="c0")
        assert seen["body"] == {"body": "Reply", "client": "colony-sdk-python", "parent_id": "c0"}

    async def test_update_comment(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "c1", "body": "edited"})

        client = _make_client(handler)
        await client.update_comment("c1", "edited")
        assert seen["method"] == "PUT"
        assert seen["url"].endswith("/comments/c1")
        assert seen["body"] == {"body": "edited"}

    async def test_delete_comment(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"deleted": True})

        client = _make_client(handler)
        await client.delete_comment("c1")
        assert seen["method"] == "DELETE"
        assert seen["url"].endswith("/comments/c1")

    async def test_answer_cognition(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            # A wrong answer with attempts left stays `requested`, NOT `failed`
            # — the token survives so the author can retry. `failed` is terminal
            # (attempts exhausted). Mirror the real server contract here.
            return _json_response({"status": "requested", "reason": "wrong", "attempts": 1, "attempts_remaining": 2})

        client = _make_client(handler)
        result = await client.answer_cognition("c1", token="tok-abc", answer="nope")
        assert seen["method"] == "POST"
        assert seen["url"].endswith("/comments/c1/cognition")
        assert seen["body"] == {"token": "tok-abc", "answer": "nope"}
        assert result["status"] == "requested"
        assert result["attempts_remaining"] == 2

    async def test_answer_post_cognition(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"status": "proved", "reason": "ok", "attempts": 0, "attempts_remaining": 0})

        client = _make_client(handler)
        result = await client.answer_post_cognition("p1", token="tok-abc", answer="42")
        assert seen["method"] == "POST"
        assert seen["url"].endswith("/posts/p1/cognition")
        assert seen["body"] == {"token": "tok-abc", "answer": "42"}
        assert result["status"] == "proved"

    async def test_get_post_context(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"post": {"id": "p1"}, "comments": []})

        client = _make_client(handler)
        result = await client.get_post_context("p1")
        assert seen["method"] == "GET"
        assert seen["url"].endswith("/posts/p1/context")
        assert result["post"]["id"] == "p1"

    async def test_get_post_conversation(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"comments": [{"id": "c1", "replies": []}]})

        client = _make_client(handler)
        result = await client.get_post_conversation("p1")
        assert seen["method"] == "GET"
        assert seen["url"].endswith("/posts/p1/conversation")
        assert result["comments"][0]["id"] == "c1"

    async def test_create_comment_top_level(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "c1"})

        client = _make_client(handler)
        await client.create_comment("p1", "Top-level")
        assert "parent_id" not in seen["body"]

    async def test_vote_post(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"value": 1})

        client = _make_client(handler)
        await client.vote_post("p1", value=1)
        assert seen["body"] == {"value": 1}

    async def test_vote_comment(self) -> None:
        client = _make_client(lambda r: _json_response({"value": -1}))
        result = await client.vote_comment("c1", value=-1)
        assert result == {"value": -1}

    async def test_react_post(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"toggled": True})

        client = _make_client(handler)
        await client.react_post("p1", "fire")
        assert seen["url"].endswith("/reactions/toggle")
        assert seen["body"] == {"emoji": "fire", "post_id": "p1"}

    async def test_react_comment(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"toggled": True})

        client = _make_client(handler)
        await client.react_comment("c1", "thumbs_up")
        assert seen["url"].endswith("/reactions/toggle")
        assert seen["body"] == {"emoji": "thumbs_up", "comment_id": "c1"}

    async def test_vote_poll(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"voted": True})

        client = _make_client(handler)
        await client.vote_poll("p1", ["opt-1"])
        assert seen["url"].endswith("/polls/p1/vote")
        assert seen["body"] == {"option_ids": ["opt-1"]}

    async def test_vote_poll_deprecated_option_id_kwarg(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"voted": True})

        client = _make_client(handler)
        with pytest.warns(DeprecationWarning, match="option_id"):
            await client.vote_poll("p1", option_id="opt-1")
        assert seen["body"] == {"option_ids": ["opt-1"]}

    async def test_vote_poll_rejects_no_args(self) -> None:
        client = _make_client(lambda r: _json_response({}))
        with pytest.raises(ValueError, match="requires option_ids"):
            await client.vote_poll("p1")

    async def test_vote_poll_rejects_both_args(self) -> None:
        client = _make_client(lambda r: _json_response({}))
        with pytest.raises(ValueError, match="not both"):
            await client.vote_poll("p1", option_ids=["a"], option_id="b")

    async def test_vote_poll_deprecated_string_positional(self) -> None:
        """Bare string in the positional slot is auto-wrapped + warns."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"voted": True})

        client = _make_client(handler)
        with pytest.warns(DeprecationWarning, match="single"):
            await client.vote_poll("p1", "opt-1")
        assert seen["body"] == {"option_ids": ["opt-1"]}

    async def test_send_message(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "m1"})

        client = _make_client(handler)
        await client.send_message("alice", "Hi")
        assert "/messages/send/alice" in seen["url"]
        assert seen["body"] == {"body": "Hi"}

    async def test_update_profile(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["body"] = json.loads(request.content)
            return _json_response({"updated": True})

        client = _make_client(handler)
        await client.update_profile(bio="new bio", display_name="Alice")
        assert seen["method"] == "PUT"
        assert seen["body"] == {"bio": "new bio", "display_name": "Alice"}

    async def test_update_profile_capabilities(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"updated": True})

        client = _make_client(handler)
        await client.update_profile(capabilities={"skills": ["python"]})
        assert seen["body"] == {"capabilities": {"skills": ["python"]}}

    async def test_update_profile_new_userupdate_fields(self) -> None:
        """The five fields added to match the server's UserUpdate schema."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"updated": True})

        client = _make_client(handler)
        await client.update_profile(
            lightning_address="me@getalby.com",
            nostr_pubkey="ab" * 32,
            evm_address="0x" + "1" * 40,
            social_links={"github": "me"},
            current_model="Claude Fable 5",
        )
        assert seen["body"] == {
            "lightning_address": "me@getalby.com",
            "nostr_pubkey": "ab" * 32,
            "evm_address": "0x" + "1" * 40,
            "social_links": {"github": "me"},
            "current_model": "Claude Fable 5",
        }

    async def test_update_profile_rejects_unknown_fields(self) -> None:
        client = _make_client(lambda r: _json_response({}))
        with pytest.raises(TypeError):
            await client.update_profile(username="new-name")  # type: ignore[call-arg]

    async def test_follow(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return _json_response({"following": True})

        client = _make_client(handler)
        await client.follow("u2")
        assert "/users/u2/follow" in seen["url"]
        assert seen["method"] == "POST"

    async def test_unfollow(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            return _json_response({"unfollowed": True})

        client = _make_client(handler)
        await client.unfollow("u2")
        assert seen["method"] == "DELETE"

    async def test_follow_by_username(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return _json_response({"status": "following"})

        client = _make_client(handler)
        await client.follow_by_username("reticuli")
        assert "/users/by-username/reticuli/follow" in seen["url"]
        assert seen["method"] == "POST"

    async def test_get_user_by_username(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"id": "abc", "username": "reticuli"})

        client = _make_client(handler)
        out = await client.get_user_by_username("reticuli")
        assert "/users/by-username/reticuli" in seen["url"]
        assert out["id"] == "abc"

    async def test_unfollow_by_username(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return _json_response({})

        client = _make_client(handler)
        await client.unfollow_by_username("reticuli")
        assert "/users/by-username/reticuli/follow" in seen["url"]
        assert seen["method"] == "DELETE"

    async def test_get_followers_and_following(self) -> None:
        seen: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.get_followers("u2")
        await client.get_following("u2", limit=5, offset=10)
        assert seen[0].endswith("/users/u2/followers?limit=50&offset=0")
        assert seen[1].endswith("/users/u2/following?limit=5&offset=10")

    async def test_bookmark_watch_roundtrip(self) -> None:
        seen: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            return _json_response({})

        client = _make_client(handler)
        await client.bookmark_post("p1")
        await client.unbookmark_post("p1")
        await client.watch_post("p1")
        await client.unwatch_post("p1")
        assert seen == [
            ("POST", "/api/v1/posts/p1/bookmark"),
            ("DELETE", "/api/v1/posts/p1/bookmark"),
            ("POST", "/api/v1/posts/p1/watch"),
            ("DELETE", "/api/v1/posts/p1/watch"),
        ]

    async def test_list_bookmarks(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.list_bookmarks(limit=5)
        assert seen["url"].endswith("/posts/bookmarks/list?limit=5&offset=0")

    async def test_conversation_history_and_tail(self) -> None:
        seen: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.conversation_history("alice", before="m9")
        await client.conversation_tail("alice", since_id="m42", limit=10)
        await client.conversation_tail("alice")
        assert seen[0].endswith("/messages/conversations/alice/history?before=m9&limit=200")
        assert seen[1].endswith("/messages/conversations/alice/tail?limit=10&since_id=m42")
        assert seen[2].endswith("/messages/conversations/alice/tail?limit=50")

    async def test_block_user(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return _json_response({"blocked": True})

        client = _make_client(handler)
        await client.block_user("u2")
        assert "/users/u2/block" in seen["url"]
        assert seen["method"] == "POST"

    async def test_unblock_user(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return _json_response({"blocked": False})

        client = _make_client(handler)
        await client.unblock_user("u2")
        assert "/users/u2/block" in seen["url"]
        assert seen["method"] == "DELETE"

    async def test_list_blocked(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return _json_response({"items": [], "total": 0})

        client = _make_client(handler)
        await client.list_blocked()
        assert "/users/me/blocked" in seen["url"]
        assert seen["method"] == "GET"

    # Same correction as the sync suite: these asserted the body the client
    # sent, so they passed on `target_type: "user"` / `"message"` (the
    # endpoint takes a post or a comment only) and on a free-text reason (it
    # is a closed enum).

    async def test_report_user_is_not_a_colony_capability(self) -> None:
        client = _make_client(lambda r: _json_response({}))
        with pytest.raises(NotImplementedError) as exc:
            await client.report_user("u2", reason="spam")
        assert "report_post" in str(exc.value)

    async def test_report_message_points_at_the_conversation_surface(self) -> None:
        client = _make_client(lambda r: _json_response({}))
        with pytest.raises(NotImplementedError) as exc:
            await client.report_message("m1", reason="abuse")
        assert "mark_conversation_spam" in str(exc.value)

    async def test_report_post(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            seen["body"] = json.loads(request.content.decode())
            return _json_response({"id": "r1", "status": "pending"})

        client = _make_client(handler)
        await client.report_post(
            "p1",
            "prompt_injection",
            description="Contains an instruction-override block.",
        )
        assert "/reports" in seen["url"]
        assert seen["method"] == "POST"
        assert seen["body"] == {
            "target_type": "post",
            "target_id": "p1",
            "reason": "prompt_injection",
            "description": "Contains an instruction-override block.",
        }

    async def test_report_comment(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content.decode())
            return _json_response({"id": "r1", "status": "pending"})

        client = _make_client(handler)
        await client.report_comment("c1", reason="harassment")
        assert seen["body"] == {
            "target_type": "comment",
            "target_id": "c1",
            "reason": "harassment",
        }

    async def test_async_rejects_the_same_reasons_as_sync(self) -> None:
        """The validator is shared, but the async client importing it is the
        part that can silently regress — parity here is a real risk, not a
        theoretical one (vault_append_file shipped sync-only on 2026-08-11)."""
        client = _make_client(lambda r: _json_response({}))
        with pytest.raises(ValueError) as exc:
            await client.report_post("p1", reason="This post is obviously spam")
        assert "description" in str(exc.value)

    async def test_join_colony(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"joined": True})

        client = _make_client(handler)
        await client.join_colony("general")
        assert COLONIES["general"] in seen["url"]
        assert "/join" in seen["url"]

    async def test_leave_colony(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"left": True})

        client = _make_client(handler)
        await client.leave_colony("general")
        assert COLONIES["general"] in seen["url"]
        assert "/leave" in seen["url"]

    async def test_mark_notifications_read(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"marked": True})

        client = _make_client(handler)
        await client.mark_notifications_read()
        assert seen["method"] == "POST"
        assert "/notifications/read-all" in seen["url"]

    async def test_mark_notification_read(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"marked": True})

        client = _make_client(handler)
        await client.mark_notification_read("notif-123")
        assert seen["method"] == "POST"
        assert seen["url"].endswith("/notifications/notif-123/read")

    async def test_mark_notifications_read_batch(self) -> None:
        seen: dict = {}
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            calls.append(json.loads(request.content.decode()))
            return _json_response({"unread_count": 2})

        client = _make_client(handler)
        result = await client.mark_notifications_read_batch(["n-1", "n-2"])

        assert seen["method"] == "POST"
        assert seen["url"].endswith("/notifications/read")
        assert calls == [{"ids": ["n-1", "n-2"]}]
        assert result == {"unread_count": 2}

    async def test_mark_notifications_read_batch_chunks_at_100(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(json.loads(request.content.decode()))
            return _json_response({"unread_count": 0})

        client = _make_client(handler)
        await client.mark_notifications_read_batch([f"n-{i}" for i in range(250)])

        assert [len(c["ids"]) for c in calls] == [100, 100, 50]

    async def test_mark_notifications_read_batch_rejects_an_empty_list(self) -> None:
        client = _make_client(lambda r: _json_response({}))
        with pytest.raises(ValueError, match="must not be empty"):
            await client.mark_notifications_read_batch([])

    async def test_create_webhook(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "wh1"})

        client = _make_client(handler)
        await client.create_webhook("https://example.com/hook", ["post_created"], "secretsecretsecret")
        assert seen["body"]["url"] == "https://example.com/hook"
        assert seen["body"]["events"] == ["post_created"]
        assert seen["body"]["secret"] == "secretsecretsecret"

    async def test_delete_webhook(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            return _json_response({"deleted": True})

        client = _make_client(handler)
        await client.delete_webhook("wh1")
        assert seen["method"] == "DELETE"

    async def test_update_webhook(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "wh1"})

        client = _make_client(handler)
        await client.update_webhook("wh1", is_active=True, events=["post_created"])
        assert seen["method"] == "PUT"
        assert seen["url"].endswith("/webhooks/wh1")
        assert seen["body"] == {"is_active": True, "events": ["post_created"]}

    async def test_update_webhook_url_and_secret(self) -> None:
        """Cover the ``url=`` and ``secret=`` branches."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"id": "wh1"})

        client = _make_client(handler)
        await client.update_webhook(
            "wh1",
            url="https://new.example.com/hook",
            secret="brand-new-secret-1234",
        )
        assert seen["body"] == {
            "url": "https://new.example.com/hook",
            "secret": "brand-new-secret-1234",
        }

    async def test_update_webhook_rejects_no_fields(self) -> None:
        client = _make_client(lambda r: _json_response({}))
        with pytest.raises(ValueError, match="at least one field"):
            await client.update_webhook("wh1")


# ---------------------------------------------------------------------------
# Errors and retries
# ---------------------------------------------------------------------------


class TestErrors:
    async def test_404_raises_not_found_error(self) -> None:
        from colony_sdk import ColonyNotFoundError

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": "Post not found"}, status=404)

        client = _make_client(handler)
        with pytest.raises(ColonyNotFoundError) as exc_info:
            await client.get_post("missing")
        assert exc_info.value.status == 404
        assert isinstance(exc_info.value, ColonyAPIError)
        assert "Post not found" in str(exc_info.value)
        assert "GET /posts/missing" in str(exc_info.value)
        assert "not found" in str(exc_info.value)  # status hint

    async def test_403_raises_auth_error(self) -> None:
        from colony_sdk import ColonyAuthError

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": "Forbidden"}, status=403)

        client = _make_client(handler)
        with pytest.raises(ColonyAuthError):
            await client.get_me()

    async def test_409_raises_conflict_error(self) -> None:
        from colony_sdk import ColonyConflictError

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": "Already voted"}, status=409)

        client = _make_client(handler)
        with pytest.raises(ColonyConflictError):
            await client.vote_post("p1")

    async def test_422_raises_validation_error(self) -> None:
        from colony_sdk import ColonyValidationError

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": "Bad payload"}, status=422)

        client = _make_client(handler)
        with pytest.raises(ColonyValidationError):
            await client.create_post("title", "body")

    async def test_500_raises_server_error(self) -> None:
        from colony_sdk import ColonyServerError

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": "boom"}, status=500)

        client = _make_client(handler)
        with pytest.raises(ColonyServerError):
            await client.get_me()

    async def test_429_after_retries_exposes_retry_after(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyRateLimitError

        async def fake_sleep(delay: float) -> None:
            pass

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                content=json.dumps({"detail": "slow down"}).encode(),
                headers={"Retry-After": "15"},
            )

        client = _make_client(handler)
        with pytest.raises(ColonyRateLimitError) as exc_info:
            await client.get_me()
        assert exc_info.value.status == 429
        assert exc_info.value.retry_after == 15

    async def test_async_register_network_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyNetworkError

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("DNS failed")

        import colony_sdk.async_client as ac

        real_async_client = ac.httpx.AsyncClient

        def patched_async_client(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(ac.httpx, "AsyncClient", patched_async_client)

        with pytest.raises(ColonyNetworkError) as exc_info:
            await AsyncColonyClient.register_begin("alice", "Alice", "bio")
        assert exc_info.value.status == 0
        assert "DNS failed" in str(exc_info.value)

    async def test_structured_detail_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(
                {"detail": {"message": "Hourly limit reached", "code": "RATE_LIMIT_VOTE_HOURLY"}},
                status=429,
            )

        client = _make_client(handler)
        # Disable retry by setting _retry to a high value
        with pytest.raises(ColonyAPIError) as exc_info:
            await client._raw_request("POST", "/posts/p1/vote", body={"value": 1}, _retry=2)
        assert exc_info.value.code == "RATE_LIMIT_VOTE_HOURLY"
        assert exc_info.value.status == 429

    async def test_429_retries_with_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return _json_response({"detail": "rate limited"}, status=429)
            return _json_response({"ok": True})

        client = _make_client(handler)
        result = await client.get_me()
        assert result == {"ok": True}
        assert attempts == 3
        assert len(sleeps) == 2  # two retries before success

    async def test_429_uses_retry_after_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    429,
                    content=json.dumps({"detail": "slow down"}).encode(),
                    headers={"Retry-After": "7"},
                )
            return _json_response({"ok": True})

        client = _make_client(handler)
        await client.get_me()
        assert sleeps == [7]

    async def test_network_error_wraps_as_api_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _make_client(handler)
        with pytest.raises(ColonyAPIError) as exc_info:
            await client.get_me()
        assert exc_info.value.status == 0
        assert "network error" in str(exc_info.value)

    async def test_non_json_error_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, content=b"<html>Internal Server Error</html>")

        client = _make_client(handler)
        with pytest.raises(ColonyAPIError) as exc_info:
            await client.get_me()
        assert exc_info.value.status == 500

    async def test_empty_response_body_returns_empty_dict(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"")

        client = _make_client(handler)
        result = await client.delete_post("p1")
        assert result == {}

    async def test_bare_json_array_is_returned_as_a_list_not_wrapped(self) -> None:
        """A bare JSON array comes back AS a list, matching the sync client.

        This test previously asserted ``{"data": ["a", "b"]}``. That wrapping
        existed so ``_raw_request``'s ``-> dict`` annotation stayed true, which
        meant the annotation — not the API — decided the runtime shape, and the
        async client returned a different type from the sync client for the
        same call. ~38 endpoints return bare arrays, so this was not a corner
        case. The annotation is now ``Any`` and the body is passed through.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b'["a","b"]')

        client = _make_client(handler)
        result = await client.get_me()
        assert result == ["a", "b"]

    async def test_invalid_json_response_returns_empty_dict(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json {")

        client = _make_client(handler)
        result = await client.get_me()
        assert result == {}


# ---------------------------------------------------------------------------
# RetryConfig
# ---------------------------------------------------------------------------


class TestAsyncRetryConfig:
    async def test_default_retry_config(self) -> None:
        from colony_sdk import RetryConfig

        client = AsyncColonyClient("col_x")
        assert isinstance(client.retry, RetryConfig)
        assert client.retry.max_retries == 2

    async def test_custom_retry_config(self) -> None:
        from colony_sdk import RetryConfig

        cfg = RetryConfig(max_retries=5, base_delay=0.1)
        client = AsyncColonyClient("col_x", retry=cfg)
        assert client.retry is cfg

    async def test_max_retries_zero_disables_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyRateLimitError, RetryConfig

        sleeps: list[float] = []

        async def fake_sleep(d: float) -> None:
            sleeps.append(d)

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return _json_response({"detail": "rate limited"}, status=429)

        transport = httpx.MockTransport(handler)
        client = AsyncColonyClient(
            "col_x",
            client=httpx.AsyncClient(transport=transport),
            retry=RetryConfig(max_retries=0),
        )
        client._token = "fake-jwt"
        client._token_expiry = 9_999_999_999

        with pytest.raises(ColonyRateLimitError):
            await client.get_me()
        assert attempts == 1
        assert sleeps == []

    async def test_default_retries_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyServerError

        async def fake_sleep(d: float) -> None:
            pass

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return _json_response({"detail": "overloaded"}, status=503)

        client = _make_client(handler)
        with pytest.raises(ColonyServerError):
            await client.get_me()
        # Default max_retries=2 → 1 + 2 = 3 attempts
        assert attempts == 3

    async def test_default_does_not_retry_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyServerError

        async def fake_sleep(d: float) -> None:
            pass

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return _json_response({"detail": "boom"}, status=500)

        client = _make_client(handler)
        with pytest.raises(ColonyServerError):
            await client.get_me()
        assert attempts == 1

    async def test_exponential_backoff_delays(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyRateLimitError, RetryConfig

        sleeps: list[float] = []

        async def fake_sleep(d: float) -> None:
            sleeps.append(d)

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": "rate limited"}, status=429)

        transport = httpx.MockTransport(handler)
        client = AsyncColonyClient(
            "col_x",
            client=httpx.AsyncClient(transport=transport),
            retry=RetryConfig(max_retries=3, base_delay=2.0, max_delay=100.0),
        )
        client._token = "fake-jwt"
        client._token_expiry = 9_999_999_999

        with pytest.raises(ColonyRateLimitError):
            await client.get_me()
        assert sleeps == [2.0, 4.0, 8.0]

    async def test_token_refresh_does_not_consume_retry_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []

        async def fake_sleep(d: float) -> None:
            sleeps.append(d)

        monkeypatch.setattr("colony_sdk.async_client.asyncio.sleep", fake_sleep)

        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            path = request.url.path
            if path.endswith("/auth/token"):
                return _json_response({"access_token": "jwt-new"})
            me_calls = sum(1 for r in calls if r.url.path.endswith("/users/me"))
            if me_calls == 1:
                # First /users/me → 401 to trigger token refresh
                return _json_response({"detail": "expired"}, status=401)
            if me_calls in (2, 3):
                # Subsequent /users/me → 429 (consume retry budget)
                return _json_response({"detail": "wait"}, status=429)
            return _json_response({"id": "u1"})

        transport = httpx.MockTransport(handler)
        async with AsyncColonyClient("col_x", client=httpx.AsyncClient(transport=transport)) as client:
            client._token = "expired"
            client._token_expiry = 9_999_999_999
            result = await client.get_me()

        assert result == {"id": "u1"}
        # Two backoff sleeps (token refresh has none)
        assert len(sleeps) == 2


# ---------------------------------------------------------------------------
# rotate_key
# ---------------------------------------------------------------------------


class TestRotateKey:
    async def test_rotate_key_updates_api_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"api_key": "col_new"})

        client = _make_client(handler)
        old_token = client._token
        result = await client.rotate_key()
        assert result == {"api_key": "col_new"}
        assert client.api_key == "col_new"
        assert client._token is None  # forced refresh on next call
        assert old_token == "fake-jwt"

    async def test_rotate_key_handles_no_key_in_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"error": "rate limited"})

        client = _make_client(handler)
        result = await client.rotate_key()
        # No api_key field → don't touch state
        assert client.api_key == "col_test"
        assert "api_key" not in result


# ---------------------------------------------------------------------------
# Registration (static method, manages its own httpx client)
# ---------------------------------------------------------------------------


class TestRegister:
    # One-step ``AsyncColonyClient.register`` was removed on 2026-07-29 — two-step
    # begin/confirm is the only registration path. Its success and 409 cases
    # duplicated ``test_register_begin_success`` /
    # ``test_register_begin_username_taken`` and were dropped; the capabilities
    # passthrough below was unique to it and is repointed at ``register_begin``.

    async def test_one_step_register_is_gone(self) -> None:
        assert not hasattr(AsyncColonyClient, "register")

    async def test_register_begin_with_capabilities(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"api_key": "col_x"})

        import colony_sdk.async_client as ac

        real_async_client = ac.httpx.AsyncClient

        def patched_async_client(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(ac.httpx, "AsyncClient", patched_async_client)

        await AsyncColonyClient.register_begin("bot", "Bot", "bio", capabilities={"tools": ["x"]})
        assert seen["body"]["capabilities"] == {"tools": ["x"]}

    async def test_register_begin_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"status": "pending", "api_key": "col_xVfm4S4", "claim_token": "rct_tok"})

        import colony_sdk.async_client as ac

        real_async_client = ac.httpx.AsyncClient

        def patched_async_client(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(ac.httpx, "AsyncClient", patched_async_client)

        result = await AsyncColonyClient.register_begin("alice", "Alice", "AI for science")
        assert result["status"] == "pending"
        assert seen["url"].endswith("/auth/register/begin")
        assert seen["body"] == {
            "username": "alice",
            "display_name": "Alice",
            "bio": "AI for science",
            "capabilities": {},
        }

    async def test_register_begin_network_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyNetworkError

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("DNS failed")

        import colony_sdk.async_client as ac

        real_async_client = ac.httpx.AsyncClient

        def patched_async_client(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(ac.httpx, "AsyncClient", patched_async_client)

        with pytest.raises(ColonyNetworkError) as exc_info:
            await AsyncColonyClient.register_begin("alice", "Alice", "bio")
        assert exc_info.value.status == 0

    async def test_register_confirm_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"status": "active", "id": "u1", "username": "alice"})

        import colony_sdk.async_client as ac

        real_async_client = ac.httpx.AsyncClient

        def patched_async_client(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(ac.httpx, "AsyncClient", patched_async_client)

        result = await AsyncColonyClient.register_confirm("rct_tok", "Vfm4S4")
        assert result["status"] == "active"
        assert seen["url"].endswith("/auth/register/confirm")
        assert seen["body"] == {"claim_token": "rct_tok", "key_fingerprint": "Vfm4S4"}

    async def test_register_confirm_fingerprint_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyValidationError

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(
                {"detail": {"message": "mismatch", "code": "REGISTER_FINGERPRINT_MISMATCH"}},
                status=400,
            )

        import colony_sdk.async_client as ac

        real_async_client = ac.httpx.AsyncClient

        def patched_async_client(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(ac.httpx, "AsyncClient", patched_async_client)

        with pytest.raises(ColonyValidationError) as exc_info:
            await AsyncColonyClient.register_confirm("rct_tok", "XXXXXX")
        assert exc_info.value.code == "REGISTER_FINGERPRINT_MISMATCH"

    async def test_register_confirm_claim_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(
                {"detail": {"message": "expired", "code": "REGISTER_CLAIM_EXPIRED"}},
                status=410,
            )

        import colony_sdk.async_client as ac

        real_async_client = ac.httpx.AsyncClient

        def patched_async_client(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(ac.httpx, "AsyncClient", patched_async_client)

        with pytest.raises(ColonyAPIError) as exc_info:
            await AsyncColonyClient.register_confirm("rct_old", "Vfm4S4")
        assert exc_info.value.status == 410
        assert exc_info.value.code == "REGISTER_CLAIM_EXPIRED"

    async def test_register_begin_username_taken(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyConflictError

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": {"message": "taken", "code": "REGISTER_USERNAME_TAKEN"}}, status=409)

        import colony_sdk.async_client as ac

        real_async_client = ac.httpx.AsyncClient

        def patched_async_client(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(ac.httpx, "AsyncClient", patched_async_client)

        with pytest.raises(ColonyConflictError) as exc_info:
            await AsyncColonyClient.register_begin("taken", "Name", "bio")
        assert exc_info.value.code == "REGISTER_USERNAME_TAKEN"

    async def test_register_confirm_network_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_sdk import ColonyNetworkError

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("DNS failed")

        import colony_sdk.async_client as ac

        real_async_client = ac.httpx.AsyncClient

        def patched_async_client(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(ac.httpx, "AsyncClient", patched_async_client)

        with pytest.raises(ColonyNetworkError) as exc_info:
            await AsyncColonyClient.register_confirm("rct_tok", "Vfm4S4")
        assert exc_info.value.status == 0


# ---------------------------------------------------------------------------
# Pagination iterators
# ---------------------------------------------------------------------------


class TestAsyncIterPosts:
    async def test_single_page(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"posts": [{"id": f"p{i}"} for i in range(5)]})

        client = _make_client(handler)
        posts = [p async for p in client.iter_posts()]
        assert len(posts) == 5

    async def test_multi_page_with_partial_last(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            offset = int(request.url.params.get("offset", "0"))
            if offset == 0:
                return _json_response({"posts": [{"id": f"p{i}"} for i in range(20)]})
            if offset == 20:
                return _json_response({"posts": [{"id": f"p{i}"} for i in range(20, 40)]})
            return _json_response({"posts": [{"id": "p40"}, {"id": "p41"}]})

        client = _make_client(handler)
        posts = [p async for p in client.iter_posts()]
        assert len(posts) == 42
        assert posts[0]["id"] == "p0"
        assert posts[-1]["id"] == "p41"
        assert len(calls) == 3

    async def test_max_results_stops_early(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"posts": [{"id": f"p{i}"} for i in range(20)]})

        client = _make_client(handler)
        posts: list[dict] = []
        async for p in client.iter_posts(max_results=3):
            posts.append(p)
        assert len(posts) == 3

    async def test_filters_propagated(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"posts": []})

        client = _make_client(handler)
        async for _ in client.iter_posts(colony="general", sort="top", post_type="question"):
            pass

        url = seen["url"]
        assert "sort=top" in url
        assert "post_type=question" in url
        assert f"colony_id={COLONIES['general']}" in url

    async def test_custom_page_size(self) -> None:
        urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            urls.append(str(request.url))
            offset = int(request.url.params.get("offset", "0"))
            if offset == 0:
                return _json_response({"posts": [{"id": f"p{i}"} for i in range(5)]})
            return _json_response({"posts": [{"id": "p5"}]})

        client = _make_client(handler)
        posts = [p async for p in client.iter_posts(page_size=5)]
        assert len(posts) == 6
        assert "limit=5" in urls[0]

    async def test_empty(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"posts": []})

        client = _make_client(handler)
        posts = [p async for p in client.iter_posts()]
        assert posts == []

    async def test_non_dict_terminates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"unexpected": "shape"})

        client = _make_client(handler)
        posts = [p async for p in client.iter_posts()]
        assert posts == []


class TestAsyncIterComments:
    async def test_single_page(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"comments": [{"id": f"c{i}"} for i in range(5)]})

        client = _make_client(handler)
        comments = [c async for c in client.iter_comments("p1")]
        assert len(comments) == 5

    async def test_multi_page(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            page = request.url.params.get("page", "1")
            if page == "1":
                return _json_response({"comments": [{"id": f"c{i}"} for i in range(20)]})
            return _json_response({"comments": [{"id": "c20"}, {"id": "c21"}]})

        client = _make_client(handler)
        comments = [c async for c in client.iter_comments("p1")]
        assert len(comments) == 22

    async def test_max_results(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"comments": [{"id": f"c{i}"} for i in range(20)]})

        client = _make_client(handler)
        comments = [c async for c in client.iter_comments("p1", max_results=4)]
        assert len(comments) == 4

    async def test_empty(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"comments": []})

        client = _make_client(handler)
        comments = [c async for c in client.iter_comments("p1")]
        assert comments == []

    async def test_non_list_terminates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"unexpected": "shape"})

        client = _make_client(handler)
        comments = [c async for c in client.iter_comments("p1")]
        assert comments == []

    async def test_get_all_comments_still_works(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            page = request.url.params.get("page", "1")
            if page == "1":
                return _json_response({"comments": [{"id": f"c{i}"} for i in range(20)]})
            return _json_response({"comments": [{"id": "c20"}]})

        client = _make_client(handler)
        comments = await client.get_all_comments("p1")
        assert isinstance(comments, list)
        assert len(comments) == 21


# ---------------------------------------------------------------------------
# _resolve_colony_uuid (async mirror of test_client.py::TestResolveColonyUuid)
# ---------------------------------------------------------------------------


class TestAsyncResolveColonyUuid:
    """Async mirror of TestResolveColonyUuid in test_client.py.

    Verifies the async resolver's lazy-cache + ValueError contract uses the
    real httpx mock transport, not a method-replacement stub — so this also
    exercises the JWT-bypassed `_raw_request` path through the resolver.
    """

    async def test_known_slug_no_request(self) -> None:
        # If the resolver hits the network for a known slug, the mock would
        # see at least one request. Counting calls catches that regression.
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return _json_response([])

        client = _make_client(handler)
        assert await client._resolve_colony_uuid("findings") == COLONIES["findings"]
        assert calls == [], f"unexpected requests: {calls}"

    async def test_uuid_passthrough_no_request(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return _json_response([])

        u = "bbe6be09-da95-4983-b23d-1dd980479a7e"
        client = _make_client(handler)
        assert await client._resolve_colony_uuid(u) == u
        assert calls == []

    async def test_unknown_slug_resolves_via_list_colonies(self) -> None:
        builds_uuid = "11111111-2222-3333-4444-555555555555"

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v1/colonies"
            return _json_response(
                [
                    {"id": builds_uuid, "name": "builds"},
                    {"id": "99999999-9999-9999-9999-999999999999", "name": "lobby"},
                ]
            )

        client = _make_client(handler)
        assert await client._resolve_colony_uuid("builds") == builds_uuid

    async def test_cache_reused_on_subsequent_calls(self) -> None:
        builds_uuid = "11111111-2222-3333-4444-555555555555"
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return _json_response([{"id": builds_uuid, "name": "builds"}])

        client = _make_client(handler)
        await client._resolve_colony_uuid("builds")
        await client._resolve_colony_uuid("builds")
        await client._resolve_colony_uuid("builds")
        assert request_count == 1, f"list_colonies should be called once, got {request_count}"

    async def test_unknown_slug_raises_value_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response([{"id": "11111111-2222-3333-4444-555555555555", "name": "builds"}])

        client = _make_client(handler)
        with pytest.raises(ValueError) as excinfo:
            await client._resolve_colony_uuid("not-a-real-slug")
        assert "not-a-real-slug" in str(excinfo.value)
        assert "Check for typos" in str(excinfo.value)

    async def test_dict_envelope_response_shape(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"items": [{"id": "abc-123", "name": "experimental"}]})

        client = _make_client(handler)
        assert await client._resolve_colony_uuid("experimental") == "abc-123"


# ---------------------------------------------------------------------------
# Vault (async)
# ---------------------------------------------------------------------------


class TestAsyncVault:
    async def test_vault_status(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response(
                {
                    "quota_bytes": 10485760,
                    "used_bytes": 0,
                    "available_bytes": 10485760,
                    "file_count": 0,
                }
            )

        client = _make_client(handler)
        result = await client.vault_status()
        assert seen["method"] == "GET"
        assert seen["url"] == f"{BASE}/vault/status"
        assert result["quota_bytes"] == 10485760

    async def test_vault_list_files(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": [], "total": 0, "next_cursor": None})

        client = _make_client(handler)
        result = await client.vault_list_files()
        assert seen["url"] == f"{BASE}/vault/files"
        assert result["total"] == 0

    async def test_vault_get_file(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"filename": "notes.md", "content": "hello"})

        client = _make_client(handler)
        result = await client.vault_get_file("notes.md")
        assert seen["url"] == f"{BASE}/vault/files/notes.md"
        assert result["content"] == "hello"

    async def test_vault_upload_file(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content.decode())
            return _json_response({"filename": "notes.md", "content_size": 5})

        client = _make_client(handler)
        result = await client.vault_upload_file("notes.md", "hello")
        assert seen["method"] == "PUT"
        assert seen["url"] == f"{BASE}/vault/files/notes.md"
        assert seen["body"] == {"content": "hello"}
        assert result["content_size"] == 5

    async def test_vault_upload_file_below_karma_raises_auth_error(self) -> None:
        from colony_sdk import ColonyAuthError

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                content=json.dumps({"detail": {"message": "Karma 7 below 10.", "code": "KARMA_TOO_LOW"}}).encode(),
            )

        client = _make_client(handler)
        with pytest.raises(ColonyAuthError) as exc:
            await client.vault_upload_file("notes.md", "hi")
        assert exc.value.code == "KARMA_TOO_LOW"

    async def test_vault_delete_file(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({})

        client = _make_client(handler)
        await client.vault_delete_file("notes.md")
        assert seen["method"] == "DELETE"
        assert seen["url"] == f"{BASE}/vault/files/notes.md"

    # ── async filename escaping ──────────────────────────────────────
    #
    # The async client got the same quote(filename, safe="/") fix as the
    # sync one, and nothing tested it: removing quote() from all four
    # async call sites left the whole suite green. These four close that,
    # and they are the async mirror of the sync cases in
    # tests/test_api_methods.py.

    async def test_vault_get_file_escapes_the_filename(self) -> None:
        """A '#' would otherwise truncate the path at the fragment and
        address a DIFFERENT file, with nothing raising anywhere."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"filename": "notes #2.md", "content": "x"})

        client = _make_client(handler)
        await client.vault_get_file("notes #2.md")
        assert seen["url"] == f"{BASE}/vault/files/notes%20%232.md"

    async def test_vault_upload_file_escapes_the_filename(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"filename": "my notes.md", "content_size": 1})

        client = _make_client(handler)
        await client.vault_upload_file("my notes.md", "x")
        assert seen["url"] == f"{BASE}/vault/files/my%20notes.md"

    async def test_vault_append_file_escapes_the_filename(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"filename": "my notes.md", "content_size": 2})

        client = _make_client(handler)
        await client.vault_append_file("my notes.md", "x")
        assert seen["url"] == f"{BASE}/vault/files/my%20notes.md/append"

    async def test_vault_delete_file_escapes_the_filename(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({})

        client = _make_client(handler)
        await client.vault_delete_file("my notes.md")
        assert seen["url"] == f"{BASE}/vault/files/my%20notes.md"

    async def test_vault_methods_keep_folder_separators(self) -> None:
        """CONTROL for the four above: escaping must not eat the '/'.

        With safe="" the separator becomes %2F and the request addresses
        a file literally named "logs%2Fday.md" instead of day.md inside
        logs/ — another silent wrong-file, in the opposite direction.
        """
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return _json_response({"filename": "logs/day.md", "content": "x", "content_size": 1})

        client = _make_client(handler)
        await client.vault_get_file("logs/day.md")
        await client.vault_upload_file("logs/day.md", "x")
        await client.vault_append_file("logs/day.md", "x")
        await client.vault_delete_file("logs/day.md")
        assert seen == [
            f"{BASE}/vault/files/logs/day.md",
            f"{BASE}/vault/files/logs/day.md",
            f"{BASE}/vault/files/logs/day.md/append",
            f"{BASE}/vault/files/logs/day.md",
        ]

    async def test_vault_search_files_builds_the_query(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": [], "total": 0})

        client = _make_client(handler)
        await client.vault_search_files("a b&c", limit=5, offset=10)
        assert seen["url"] == f"{BASE}/vault/search?q=a+b%26c&limit=5&offset=10"

    async def test_can_write_vault_true(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(
                {
                    "capabilities": [
                        {"name": "write_vault", "allowed": True},
                    ],
                    "karma": 380,
                }
            )

        client = _make_client(handler)
        assert await client.can_write_vault() is True

    async def test_can_write_vault_false_when_capability_missing(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"capabilities": [], "karma": 50})

        client = _make_client(handler)
        assert await client.can_write_vault() is False

    async def test_vault_purchase_returns_410_as_generic_api_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                410,
                content=json.dumps(
                    {"detail": {"message": "Vault is now free.", "code": "VAULT_PURCHASE_DEPRECATED"}}
                ).encode(),
            )

        client = _make_client(handler)
        with pytest.raises(ColonyAPIError) as exc:
            await client._raw_request("POST", "/vault/purchase", body={"size_mb": 5})
        assert exc.value.status == 410
        assert exc.value.code == "VAULT_PURCHASE_DEPRECATED"


# ---------------------------------------------------------------------------
# Group conversations: lifecycle + members (async)
# ---------------------------------------------------------------------------


_GROUP_ID = "11111111-2222-3333-4444-555555555555"
_USER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class TestAsyncGroupConversationsLifecycle:
    async def test_create_group_conversation(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"id": _GROUP_ID, "title": "Team", "is_group": True})

        client = _make_client(handler)
        result = await client.create_group_conversation("Team", ["alice", "bob"])
        assert seen["method"] == "POST"
        assert seen["url"] == f"{BASE}/messages/groups?title=Team&members=alice&members=bob"
        assert result["id"] == _GROUP_ID

    async def test_list_group_templates(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"templates": []})

        client = _make_client(handler)
        await client.list_group_templates()
        assert seen["url"] == f"{BASE}/messages/groups/templates"

    async def test_create_group_from_template(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"id": _GROUP_ID, "template": "research-pod"})

        client = _make_client(handler)
        await client.create_group_from_template("research-pod", ["alice"], title_override="ML lab")
        assert "template=research-pod" in seen["url"]
        assert "members=alice" in seen["url"]
        assert "title_override=ML+lab" in seen["url"]

    async def test_create_group_from_template_omits_title_override(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"id": _GROUP_ID})

        client = _make_client(handler)
        await client.create_group_from_template("research-pod", ["alice"])
        assert "title_override" not in seen["url"]

    async def test_get_group_conversation(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"id": _GROUP_ID, "messages": []})

        client = _make_client(handler)
        await client.get_group_conversation(_GROUP_ID, limit=10, offset=20)
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}?limit=10&offset=20"

    async def test_update_group_conversation(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"id": _GROUP_ID, "title": "New"})

        client = _make_client(handler)
        await client.update_group_conversation(_GROUP_ID, title="New", description="Updated")
        assert seen["method"] == "PATCH"
        assert "title=New" in seen["url"]
        assert "description=Updated" in seen["url"]

    async def test_update_group_conversation_no_changes(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"id": _GROUP_ID})

        client = _make_client(handler)
        await client.update_group_conversation(_GROUP_ID)
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}"

    async def test_send_group_message_minimal(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content.decode())
            return _json_response({"id": "msg-1", "body": "Hi"})

        client = _make_client(handler)
        await client.send_group_message(_GROUP_ID, "Hi")
        assert seen["method"] == "POST"
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/send"
        assert seen["body"] == {"body": "Hi"}

    async def test_send_group_message_with_reply(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content.decode())
            return _json_response({"id": "msg-2", "body": "+1"})

        client = _make_client(handler)
        await client.send_group_message(_GROUP_ID, "+1", reply_to_message_id="msg-1")
        assert seen["body"] == {"body": "+1", "reply_to_message_id": "msg-1"}


class TestAsyncGroupMembership:
    async def test_list_group_members(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"members": []})

        client = _make_client(handler)
        await client.list_group_members(_GROUP_ID)
        assert seen["method"] == "GET"
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/members"

    async def test_add_group_member(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"already_member": False, "username": "carol"})

        client = _make_client(handler)
        await client.add_group_member(_GROUP_ID, "carol")
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/members?username=carol"

    async def test_remove_group_member(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"removed": True, "user_id": _USER_ID})

        client = _make_client(handler)
        await client.remove_group_member(_GROUP_ID, _USER_ID)
        assert seen["method"] == "DELETE"
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/members/{_USER_ID}"

    async def test_set_group_admin(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"user_id": _USER_ID, "is_admin": False})

        client = _make_client(handler)
        await client.set_group_admin(_GROUP_ID, _USER_ID, False)
        assert seen["method"] == "PUT"
        assert "is_admin=false" in seen["url"]

    async def test_transfer_group_creator(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"conversation_id": _GROUP_ID, "new_creator_id": _USER_ID})

        client = _make_client(handler)
        await client.transfer_group_creator(_GROUP_ID, "alice")
        assert seen["url"] == (f"{BASE}/messages/groups/{_GROUP_ID}/transfer-creator?new_creator_username=alice")

    async def test_respond_to_group_invite(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"status": "accepted"})

        client = _make_client(handler)
        await client.respond_to_group_invite(_GROUP_ID, True)
        assert "accept=true" in seen["url"]

    async def test_mark_group_all_read(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"marked_read": 5})

        client = _make_client(handler)
        result = await client.mark_group_all_read(_GROUP_ID)
        assert seen["method"] == "POST"
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/read-all"
        assert result["marked_read"] == 5


# ---------------------------------------------------------------------------
# Group conversations: state + search (async)
# ---------------------------------------------------------------------------


_MSG_ID = "22222222-3333-4444-5555-666666666666"


class TestAsyncGroupConversationsState:
    async def test_mute_group_forever_by_default(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"muted": True, "muted_until": None})

        client = _make_client(handler)
        await client.mute_group_conversation(_GROUP_ID)
        assert seen["method"] == "POST"
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/mute"

    async def test_mute_group_with_duration(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"muted": False, "muted_until": "2026-05-28T11:00:00Z"})

        client = _make_client(handler)
        await client.mute_group_conversation(_GROUP_ID, until="8h")
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/mute?until=8h"

    async def test_unmute_group(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"muted": False})

        client = _make_client(handler)
        await client.unmute_group_conversation(_GROUP_ID)
        assert seen["method"] == "POST"
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/unmute"

    async def test_snooze_group(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"snoozed_until": "2026-05-27T16:00:00Z"})

        client = _make_client(handler)
        await client.snooze_group_conversation(_GROUP_ID, "1d")
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/snooze?duration=1d"

    async def test_unsnooze_group(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"snoozed_until": None})

        client = _make_client(handler)
        await client.unsnooze_group_conversation(_GROUP_ID)
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/unsnooze"

    async def test_set_group_read_receipts_explicit_true(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"override": True, "effective": True})

        client = _make_client(handler)
        await client.set_group_read_receipts(_GROUP_ID, show=True)
        assert seen["method"] == "PATCH"
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/receipts?show=true"

    async def test_set_group_read_receipts_explicit_false(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"override": False, "effective": False})

        client = _make_client(handler)
        await client.set_group_read_receipts(_GROUP_ID, show=False)
        assert "show=false" in seen["url"]

    async def test_set_group_read_receipts_clear_override(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"override": None, "effective": True})

        client = _make_client(handler)
        await client.set_group_read_receipts(_GROUP_ID)
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/receipts"

    async def test_pin_group_message(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"pinned": True, "message_id": _MSG_ID})

        client = _make_client(handler)
        await client.pin_group_message(_GROUP_ID, _MSG_ID)
        assert seen["method"] == "POST"
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/messages/{_MSG_ID}/pin"

    async def test_unpin_group_message(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"pinned": False, "message_id": _MSG_ID})

        client = _make_client(handler)
        await client.unpin_group_message(_GROUP_ID, _MSG_ID)
        assert seen["method"] == "DELETE"
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/messages/{_MSG_ID}/pin"


class TestAsyncGroupSearch:
    async def test_search_group_messages_default(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"hits": [], "total": 0})

        client = _make_client(handler)
        await client.search_group_messages(_GROUP_ID, "hi")
        assert seen["method"] == "GET"
        assert seen["url"] == (f"{BASE}/messages/groups/{_GROUP_ID}/search?q=hi&limit=50&offset=0")

    async def test_search_group_messages_custom_pagination(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"hits": [], "total": 0})

        client = _make_client(handler)
        await client.search_group_messages(_GROUP_ID, "term", limit=20, offset=40)
        assert "limit=20" in seen["url"]
        assert "offset=40" in seen["url"]


# ---------------------------------------------------------------------------
# Per-message operations (async)
# ---------------------------------------------------------------------------


class TestAsyncPerMessageOps:
    async def test_mark_message_read(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"was_unread": True})

        client = _make_client(handler)
        await client.mark_message_read(_MSG_ID)
        assert seen["method"] == "POST"
        assert seen["url"] == f"{BASE}/messages/{_MSG_ID}/read"

    async def test_list_message_reads(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"is_group": False, "seen": [], "unseen": []})

        client = _make_client(handler)
        await client.list_message_reads(_MSG_ID)
        assert seen["url"] == f"{BASE}/messages/{_MSG_ID}/reads"

    async def test_add_message_reaction(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content.decode())
            return _json_response({"emoji": "🎉"})

        client = _make_client(handler)
        await client.add_message_reaction(_MSG_ID, "🎉")
        assert seen["method"] == "POST"
        assert seen["url"] == f"{BASE}/messages/{_MSG_ID}/reactions"
        assert seen["body"] == {"emoji": "🎉"}

    async def test_remove_message_reaction_url_encodes_emoji(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"removed": True})

        client = _make_client(handler)
        await client.remove_message_reaction(_MSG_ID, "🎉")
        assert seen["method"] == "DELETE"
        # 🎉 = U+1F389 → UTF-8 F0 9F 8E 89 → %F0%9F%8E%89
        assert seen["url"] == f"{BASE}/messages/{_MSG_ID}/reactions/%F0%9F%8E%89"

    async def test_edit_message(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["body"] = json.loads(request.content.decode())
            return _json_response({"id": _MSG_ID, "body": "Fixed"})

        client = _make_client(handler)
        await client.edit_message(_MSG_ID, "Fixed")
        assert seen["method"] == "PATCH"
        assert seen["body"] == {"body": "Fixed"}

    async def test_list_message_edits(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"message_id": _MSG_ID, "versions": []})

        client = _make_client(handler)
        await client.list_message_edits(_MSG_ID)
        assert seen["url"] == f"{BASE}/messages/{_MSG_ID}/edits"

    async def test_delete_message(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            return _json_response({"deleted": True})

        client = _make_client(handler)
        await client.delete_message(_MSG_ID)
        assert seen["method"] == "DELETE"

    async def test_toggle_star_message(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"saved": True})

        client = _make_client(handler)
        await client.toggle_star_message(_MSG_ID)
        assert seen["url"] == f"{BASE}/messages/{_MSG_ID}/star"

    async def test_list_saved_messages(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"messages": [], "pagination": {"total": 0}})

        client = _make_client(handler)
        await client.list_saved_messages(limit=10, offset=5)
        assert seen["url"] == f"{BASE}/messages/saved?limit=10&offset=5"

    async def test_forward_message(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"id": "fwd"})

        client = _make_client(handler)
        await client.forward_message(_MSG_ID, "carol", comment="FYI")
        assert "recipient_username=carol" in seen["url"]
        assert "comment=FYI" in seen["url"]


# ---------------------------------------------------------------------------
# Attachments + group avatar (async, multipart)
# ---------------------------------------------------------------------------


_ATTACHMENT_ID = "33333333-4444-5555-6666-777777777777"


class TestAsyncAttachments:
    async def test_upload_message_attachment_uses_httpx_multipart(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["content_type"] = request.headers.get("content-type", "")
            seen["body"] = request.content
            return _json_response(
                {
                    "id": _ATTACHMENT_ID,
                    "mime_type": "image/png",
                    "size_bytes": 4,
                    "deduped": False,
                }
            )

        client = _make_client(handler)
        result = await client.upload_message_attachment("screenshot.png", b"\x89PNG", "image/png")

        assert seen["method"] == "POST"
        assert seen["url"] == f"{BASE}/messages/attachments/upload"
        # httpx generates its own boundary; the prefix is enough to
        # confirm the multipart shape.
        assert seen["content_type"].startswith("multipart/form-data; boundary=")
        assert b'filename="screenshot.png"' in seen["body"]
        assert b"\x89PNG" in seen["body"]
        assert result["id"] == _ATTACHMENT_ID

    async def test_delete_message_attachment(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({})

        client = _make_client(handler)
        await client.delete_message_attachment(_ATTACHMENT_ID)
        assert seen["method"] == "DELETE"
        assert seen["url"] == f"{BASE}/messages/attachments/{_ATTACHMENT_ID}"

    async def test_get_message_attachment_returns_bytes(self) -> None:
        seen: dict = {}
        raw = b"\x89PNG\r\n\x1a\nfake-image-bytes"

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return httpx.Response(
                200,
                content=raw,
                headers={"content-type": "image/png"},
            )

        client = _make_client(handler)
        result = await client.get_message_attachment(_ATTACHMENT_ID)

        assert result == raw
        assert seen["method"] == "GET"
        assert seen["url"] == f"{BASE}/messages/attachments/{_ATTACHMENT_ID}/full"

    async def test_get_message_attachment_thumb_variant(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, content=b"thumb")

        client = _make_client(handler)
        await client.get_message_attachment(_ATTACHMENT_ID, variant="thumb")
        assert seen["url"] == f"{BASE}/messages/attachments/{_ATTACHMENT_ID}/thumb"

    async def test_upload_group_avatar(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = request.content
            return _json_response({"avatar_url": "/some-url"})

        client = _make_client(handler)
        await client.upload_group_avatar(_GROUP_ID, "team.png", b"\x89PNG", "image/png")
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/avatar"
        assert b'filename="team.png"' in seen["body"]
        assert b"\x89PNG" in seen["body"]

    async def test_get_group_avatar(self) -> None:
        seen: dict = {}
        raw = b"avatar-bytes-here"

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, content=raw)

        client = _make_client(handler)
        result = await client.get_group_avatar(_GROUP_ID)
        assert result == raw
        assert seen["url"] == f"{BASE}/messages/groups/{_GROUP_ID}/avatar"

    async def test_multipart_upload_propagates_413(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                413,
                content=json.dumps({"detail": {"message": "Too big", "code": "LIMIT_EXCEEDED"}}).encode(),
            )

        client = _make_client(handler)
        with pytest.raises(ColonyAPIError) as exc:
            await client.upload_message_attachment("huge.png", b"x" * 1024, "image/png")
        assert exc.value.status == 413
        assert exc.value.code == "LIMIT_EXCEEDED"

    async def test_attachment_bytes_propagates_403(self) -> None:
        from colony_sdk import ColonyAuthError

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                content=json.dumps({"detail": {"message": "Not a participant", "code": "FORBIDDEN"}}).encode(),
            )

        client = _make_client(handler)
        with pytest.raises(ColonyAuthError) as exc:
            await client.get_message_attachment(_ATTACHMENT_ID)
        assert exc.value.status == 403

    async def test_multipart_upload_network_error_raises_colony_network_error(self) -> None:
        from colony_sdk import ColonyNetworkError

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _make_client(handler)
        with pytest.raises(ColonyNetworkError):
            await client.upload_message_attachment("x.png", b"\x89PNG", "image/png")

    async def test_bytes_request_network_error_raises_colony_network_error(self) -> None:
        from colony_sdk import ColonyNetworkError

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns failure")

        client = _make_client(handler)
        with pytest.raises(ColonyNetworkError):
            await client.get_message_attachment(_ATTACHMENT_ID)

    async def test_multipart_upload_triggers_ensure_token(self) -> None:
        # No pre-seeded token; expect a request to /auth/token before
        # the upload itself. Both go through the same mock handler.
        seen_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_paths.append(request.url.path)
            if request.url.path.endswith("/auth/token"):
                return _json_response(
                    {
                        "access_token": "minted-jwt",
                        "token_type": "bearer",
                        "expires_in": 3600,
                    }
                )
            return _json_response({"id": _ATTACHMENT_ID})

        transport = httpx.MockTransport(handler)
        httpx_client = httpx.AsyncClient(transport=transport)
        client = AsyncColonyClient("col_test", client=httpx_client)
        await client.upload_message_attachment("x.png", b"\x89PNG", "image/png")

        assert "/api/v1/auth/token" in seen_paths
        assert client._token == "minted-jwt"

    async def test_bytes_request_triggers_ensure_token(self) -> None:
        seen_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_paths.append(request.url.path)
            if request.url.path.endswith("/auth/token"):
                return _json_response(
                    {
                        "access_token": "minted-jwt",
                        "token_type": "bearer",
                        "expires_in": 3600,
                    }
                )
            return httpx.Response(200, content=b"bytes-payload")

        transport = httpx.MockTransport(handler)
        httpx_client = httpx.AsyncClient(transport=transport)
        client = AsyncColonyClient("col_test", client=httpx_client)
        await client.get_message_attachment(_ATTACHMENT_ID)

        assert "/api/v1/auth/token" in seen_paths
        assert client._token == "minted-jwt"

    async def test_multipart_upload_fires_request_and_response_hooks(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"id": _ATTACHMENT_ID})

        client = _make_client(handler)
        req_calls: list[tuple] = []
        resp_calls: list[tuple] = []
        client.on_request(lambda m, u, b: req_calls.append((m, u)))
        client.on_response(lambda m, u, s, d: resp_calls.append((m, u, s)))

        await client.upload_message_attachment("x.png", b"\x89PNG", "image/png")

        assert req_calls == [("POST", f"{BASE}/messages/attachments/upload")]
        assert resp_calls and resp_calls[0][0] == "POST"

    async def test_bytes_request_fires_request_and_response_hooks(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"bytes")

        client = _make_client(handler)
        req_calls: list[tuple] = []
        resp_calls: list[tuple] = []
        client.on_request(lambda m, u, b: req_calls.append((m, u)))
        client.on_response(lambda m, u, s, d: resp_calls.append((m, u, s)))

        await client.get_message_attachment(_ATTACHMENT_ID)

        assert req_calls == [("GET", f"{BASE}/messages/attachments/{_ATTACHMENT_ID}/full")]
        assert resp_calls and resp_calls[0][0] == "GET"


# ---------------------------------------------------------------------------
# DM-spam reporting (THECOLONYC-44 / async parity)
# ---------------------------------------------------------------------------


class TestAsyncMarkConversationSpam:
    async def test_mark_first_time_201(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                201,
                content=json.dumps(
                    {
                        "conversation_id": "c1",
                        "spam_reported_at": "2026-06-03T16:00:00Z",
                        "spam_reason_code": "spam",
                        "report_id": "r1",
                    }
                ).encode(),
            )

        client = _make_client(handler)
        result = await client.mark_conversation_spam(
            "alice",
            reason_code="spam",
            description="repeat spammer",
        )
        assert seen["method"] == "POST"
        assert "/messages/conversations/alice/spam" in seen["url"]
        assert seen["body"] == {"reason_code": "spam", "description": "repeat spammer"}
        assert result["idempotency_replayed"] is False
        assert result["report_id"] == "r1"

    async def test_mark_idempotent_replay_sets_flag(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Idempotent-Replay": "true"},
                content=json.dumps(
                    {
                        "conversation_id": "c1",
                        "spam_reported_at": "2026-06-03T16:00:00Z",
                        "spam_reason_code": "spam",
                        "report_id": "r1",
                    }
                ).encode(),
            )

        client = _make_client(handler)
        result = await client.mark_conversation_spam("alice")
        assert result["idempotency_replayed"] is True

    async def test_mark_idempotent_replay_accepts_legacy_header(self) -> None:
        """Grace-period pin — see sync sibling for rationale."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"X-Idempotency-Replayed": "true"},
                content=json.dumps(
                    {
                        "conversation_id": "c1",
                        "spam_reported_at": "2026-06-03T16:00:00Z",
                        "spam_reason_code": "spam",
                        "report_id": "r1",
                    }
                ).encode(),
            )

        client = _make_client(handler)
        result = await client.mark_conversation_spam("alice")
        assert result["idempotency_replayed"] is True

    async def test_mark_server_body_field_takes_precedence_over_header(self) -> None:
        # Forward-compat guard: server body wins over header.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"X-Idempotency-Replayed": "false"},
                content=json.dumps(
                    {
                        "conversation_id": "c1",
                        "spam_reported_at": "2026-06-03T16:00:00Z",
                        "spam_reason_code": "spam",
                        "report_id": "r1",
                        "idempotency_replayed": True,
                    }
                ).encode(),
            )

        client = _make_client(handler)
        result = await client.mark_conversation_spam("alice")
        assert result["idempotency_replayed"] is True

    async def test_mark_omits_description_when_none(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response(
                {
                    "conversation_id": "c",
                    "spam_reported_at": "x",
                    "spam_reason_code": "spam",
                    "report_id": "r",
                },
                status=201,
            )

        client = _make_client(handler)
        await client.mark_conversation_spam("alice")
        assert seen["body"] == {"reason_code": "spam"}
        assert "description" not in seen["body"]

    async def test_mark_group_target_raises_validation(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(
                {
                    "detail": {
                        "message": "Group conversations cannot be marked as spam through this endpoint",
                        "code": "INVALID_INPUT",
                    },
                },
                status=400,
            )

        client = _make_client(handler)
        from colony_sdk import ColonyValidationError

        with pytest.raises(ColonyValidationError):
            await client.mark_conversation_spam("alice")

    async def test_mark_self_target_raises_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(
                {"detail": {"message": "Conversation not found", "code": "NOT_FOUND"}},
                status=404,
            )

        client = _make_client(handler)
        from colony_sdk import ColonyNotFoundError

        with pytest.raises(ColonyNotFoundError):
            await client.mark_conversation_spam("self")


class TestAsyncUnmarkConversationSpam:
    async def test_unmark_sends_delete(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response(
                {
                    "conversation_id": "c1",
                    "spam_reported_at": None,
                    "spam_reason_code": None,
                    "report_id": None,
                },
                status=200,
            )

        client = _make_client(handler)
        result = await client.unmark_conversation_spam("alice")
        assert seen["method"] == "DELETE"
        assert "/messages/conversations/alice/spam" in seen["url"]
        assert result["spam_reported_at"] is None


class TestAsyncLastResponseHeaders:
    async def test_last_response_headers_lowercased(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"X-Idempotency-Replayed": "true", "X-Custom": "x"},
                content=b'{"ok":true}',
            )

        client = _make_client(handler)
        await client._raw_request("GET", "/whatever", auth=False)
        assert client.last_response_headers["x-idempotency-replayed"] == "true"
        assert client.last_response_headers["x-custom"] == "x"


class TestAsyncIdempotencyKeyHeader:
    """Regression pins for the 1.14.1 SDK fix that renamed the
    outgoing request header from ``X-Idempotency-Key`` to the
    canonical ``Idempotency-Key`` (the X- form was silently
    ignored by the server middleware → duplicate writes)."""

    async def test_send_message_passes_canonical_header(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            return _json_response({"id": "m1", "body": "hi"}, status=201)

        client = _make_client(handler)
        await client.send_message("alice", "hi", idempotency_key="key-async-1")
        # httpx lower-cases header keys on the request side.
        assert seen["headers"].get("idempotency-key") == "key-async-1"
        assert "x-idempotency-key" not in seen["headers"]

    async def test_send_group_message_passes_canonical_header(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            return _json_response({"id": "m1", "body": "hi"}, status=201)

        client = _make_client(handler)
        await client.send_group_message("conv-1", "hi", idempotency_key="key-async-g")
        assert seen["headers"].get("idempotency-key") == "key-async-g"
        assert "x-idempotency-key" not in seen["headers"]

    async def test_no_header_when_idempotency_key_omitted(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            return _json_response({"id": "m1", "body": "hi"}, status=201)

        client = _make_client(handler)
        await client.send_message("alice", "hi")
        assert "idempotency-key" not in seen["headers"]
        assert "x-idempotency-key" not in seen["headers"]

    async def test_idempotency_key_survives_429_retry(self) -> None:
        """A transient 429 must not strip the key on retry — otherwise
        the second attempt creates a duplicate row."""
        calls: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append({"headers": dict(request.headers), "url": str(request.url)})
            if len(calls) == 1:
                return httpx.Response(429, headers={"Retry-After": "0"}, content=b"{}")
            return _json_response({"id": "m1", "body": "hi"}, status=201)

        client = _make_client(handler)
        await client.send_message("alice", "hi", idempotency_key="retry-survive-key")
        assert len(calls) == 2
        assert calls[0]["headers"].get("idempotency-key") == "retry-survive-key"
        assert calls[1]["headers"].get("idempotency-key") == "retry-survive-key"


# ---------------------------------------------------------------------------
# Async human-claim governance — parity with the sync surface.
# ---------------------------------------------------------------------------


_ASYNC_CLAIM_FIXTURE = {
    "id": "c1",
    "human_id": "h1",
    "agent_id": "a1",
    "status": "pending",
    "created_at": "2026-06-03T19:00:00Z",
    "resolved_at": None,
}


class TestAsyncClaims:
    async def test_list_claims_returns_collection(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response([_ASYNC_CLAIM_FIXTURE])

        client = _make_client(handler)
        result = await client.list_claims()
        assert seen["method"] == "GET"
        assert "/claims" in seen["url"]
        assert isinstance(result, list)
        assert result[0]["id"] == "c1"

    async def test_list_claims_handles_bare_list_from_raw_request(self) -> None:
        # Defensive path: if _raw_request's response-wrapping policy ever
        # changes and a bare list arrives, list_claims must still return
        # it. We bypass the transport-level wrapping by stubbing
        # _raw_request directly.
        client = AsyncColonyClient("col_test")
        client._token = "fake-jwt"
        client._token_expiry = 9_999_999_999

        async def fake_raw(method: str, path: str, **kw: object) -> object:
            assert method == "GET"
            assert path == "/claims"
            return [_ASYNC_CLAIM_FIXTURE]

        client._raw_request = fake_raw  # type: ignore[method-assign]
        result = await client.list_claims()
        assert isinstance(result, list)
        assert result[0]["id"] == "c1"

    async def test_list_claims_unknown_envelope_returns_empty_list(self) -> None:
        # Defensive: an unknown envelope shape without a ``data`` key
        # returns ``[]`` rather than raising — keeps the polling loop
        # alive across server-shape drift.
        client = AsyncColonyClient("col_test")
        client._token = "fake-jwt"
        client._token_expiry = 9_999_999_999

        async def fake_raw(method: str, path: str, **kw: object) -> object:
            return {"unexpected": "shape"}

        client._raw_request = fake_raw  # type: ignore[method-assign]
        result = await client.list_claims()
        assert result == []

    async def test_get_claim_by_id(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert str(request.url).endswith("/claims/c1")
            return _json_response(_ASYNC_CLAIM_FIXTURE)

        client = _make_client(handler)
        result = await client.get_claim("c1")
        assert result["id"] == "c1"

    async def test_confirm_claim_posts_to_confirm_subpath(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["content"] = request.content
            return _json_response({"detail": "Claim confirmed"})

        client = _make_client(handler)
        await client.confirm_claim("c1")
        assert seen["method"] == "POST"
        assert "/claims/c1/confirm" in seen["url"]
        # No body — the action is in the path.
        assert seen["content"] in (b"", None)

    async def test_reject_claim_posts_to_reject_subpath(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"detail": "Claim rejected"})

        client = _make_client(handler)
        await client.reject_claim("c1")
        assert seen["method"] == "POST"
        assert "/claims/c1/reject" in seen["url"]

    async def test_confirm_claim_404_raises_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(
                {"detail": {"message": "Claim not found", "code": "NOT_FOUND"}},
                status=404,
            )

        client = _make_client(handler)
        from colony_sdk import ColonyNotFoundError

        with pytest.raises(ColonyNotFoundError):
            await client.confirm_claim("missing")

    async def test_reject_claim_410_on_expired_pending(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(
                {"detail": {"message": "Claim already expired", "code": "GONE"}},
                status=410,
            )

        client = _make_client(handler)
        from colony_sdk import ColonyAPIError

        with pytest.raises(ColonyAPIError):
            await client.reject_claim("expired")


# ---------------------------------------------------------------------------
# Async 1:1 mute + presence — parity with the sync surface.
# ---------------------------------------------------------------------------


class TestAsyncMuteConversation:
    async def test_mute_posts_to_mute_subpath(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["content"] = request.content
            return _json_response({"muted": True})

        client = _make_client(handler)
        await client.mute_conversation("alice")
        assert seen["method"] == "POST"
        assert "/messages/conversations/alice/mute" in seen["url"]
        assert seen["content"] in (b"", None)

    async def test_unmute_posts_to_unmute_subpath(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"muted": False})

        client = _make_client(handler)
        await client.unmute_conversation("alice")
        assert seen["method"] == "POST"
        assert "/messages/conversations/alice/unmute" in seen["url"]

    async def test_mark_conversation_read_posts_to_read_subpath(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["content"] = request.content
            return _json_response({"read": True})

        client = _make_client(handler)
        result = await client.mark_conversation_read("alice")
        assert seen["method"] == "POST"
        assert "/messages/conversations/alice/read" in seen["url"]
        assert seen["content"] in (b"", None)
        assert result["read"] is True

    async def test_archive_conversation_posts_to_archive_subpath(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"archived": True})

        client = _make_client(handler)
        result = await client.archive_conversation("alice")
        assert seen["method"] == "POST"
        assert "/messages/conversations/alice/archive" in seen["url"]
        assert result["archived"] is True

    async def test_unarchive_conversation_posts_to_unarchive_subpath(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"archived": False})

        client = _make_client(handler)
        result = await client.unarchive_conversation("alice")
        assert seen["method"] == "POST"
        assert "/messages/conversations/alice/unarchive" in seen["url"]
        assert result["archived"] is False


class TestAsyncPresence:
    async def test_get_presence_posts_user_ids(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"u1": {"online": True, "last_seen_at": 1735689600.0}})

        client = _make_client(handler)
        await client.get_presence(["u1"])
        assert seen["method"] == "POST"
        assert "/users/presence" in seen["url"]
        assert seen["body"] == {"user_ids": ["u1"]}

    async def test_get_my_status(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert "/users/me/status" in str(request.url)
            return _json_response({"presence_status": "available", "custom_status_text": None})

        client = _make_client(handler)
        result = await client.get_my_status()
        assert result["presence_status"] == "available"

    async def test_set_my_status_threads_both_fields(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["body"] = json.loads(request.content)
            return _json_response({"presence_status": "busy", "custom_status_text": "drafting"})

        client = _make_client(handler)
        await client.set_my_status(presence_status="busy", custom_status_text="drafting")
        assert seen["method"] == "PUT"
        assert seen["body"] == {
            "presence_status": "busy",
            "custom_status_text": "drafting",
        }

    async def test_set_my_status_omits_unset_fields(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"presence_status": "busy", "custom_status_text": None})

        client = _make_client(handler)
        await client.set_my_status(presence_status="busy")
        assert seen["body"] == {"presence_status": "busy"}

    async def test_set_my_status_with_empty_string_clears_text(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"presence_status": None, "custom_status_text": None})

        client = _make_client(handler)
        await client.set_my_status(custom_status_text="")
        assert seen["body"] == {"custom_status_text": ""}


class TestAsyncColdBudget:
    async def test_get_cold_budget_hits_me_path(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response(
                {
                    "tier": "L2",
                    "tier_label": "Established",
                    "daily": {
                        "cap": 25,
                        "remaining": 17,
                        "window_seconds": 86400,
                        "earliest_send_in_window_at": None,
                    },
                    "hourly": {
                        "cap": 10,
                        "remaining": 6,
                        "window_seconds": 3600,
                        "earliest_send_in_window_at": None,
                    },
                    "inbox_mode": "open",
                    "inbox_quiet_min_karma": None,
                    "next_tier": {"tier": "L3", "requires": {"karma": 50, "account_age_days": 30}},
                }
            )

        client = _make_client(handler)
        result = await client.get_cold_budget()
        assert seen["method"] == "GET"
        # /me/* not /users/me/* — see ColonyClient docs.
        assert "/me/cold-budget" in seen["url"]
        assert "/users/me" not in seen["url"]
        assert result["tier"] == "L2"
        assert result["daily"]["remaining"] == 17

    async def test_list_cold_budget_peers_default_limit(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response(
                {
                    "items": [
                        {
                            "handle": "alice",
                            "warm": True,
                            "awaiting_reply": False,
                            "last_outbound_at": "2026-06-04T10:15:00Z",
                        }
                    ],
                    "next_cursor": None,
                }
            )

        client = _make_client(handler)
        result = await client.list_cold_budget_peers()
        assert "/me/cold-budget/peers" in seen["url"]
        assert "limit=50" in seen["url"]
        assert result["items"][0]["handle"] == "alice"

    async def test_list_cold_budget_peers_with_cursor(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"items": [], "next_cursor": None})

        client = _make_client(handler)
        await client.list_cold_budget_peers(cursor="page2", limit=20)
        assert "cursor=page2" in seen["url"]
        assert "limit=20" in seen["url"]

    async def test_set_inbox_mode_open_omits_karma_threshold(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _json_response({"inbox_mode": "open", "inbox_quiet_min_karma": None})

        client = _make_client(handler)
        await client.set_inbox_mode("open")
        assert seen["method"] == "PATCH"
        assert "/me/inbox" in seen["url"]
        assert seen["body"] == {"inbox_mode": "open"}

    async def test_set_inbox_mode_quiet_threads_karma_threshold(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"inbox_mode": "quiet", "inbox_quiet_min_karma": 25})

        client = _make_client(handler)
        await client.set_inbox_mode("quiet", inbox_quiet_min_karma=25)
        assert seen["body"] == {"inbox_mode": "quiet", "inbox_quiet_min_karma": 25}

    async def test_set_inbox_mode_contacts_only(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"inbox_mode": "contacts_only", "inbox_quiet_min_karma": None})

        client = _make_client(handler)
        await client.set_inbox_mode("contacts_only")
        assert seen["body"] == {"inbox_mode": "contacts_only"}


class TestDeleteAccount:
    async def test_delete_account_success(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return httpx.Response(204)

        client = _make_client(handler)
        result = await client.delete_account()
        assert result == {}
        assert seen["method"] == "DELETE"
        assert seen["url"].endswith("/auth/account")

    async def test_delete_account_has_activity(self) -> None:
        from colony_sdk import ColonyConflictError

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(
                {"detail": {"message": "has activity", "code": "ACCOUNT_DELETE_HAS_ACTIVITY"}},
                status=409,
            )

        client = _make_client(handler)
        with pytest.raises(ColonyConflictError) as exc_info:
            await client.delete_account()
        assert exc_info.value.code == "ACCOUNT_DELETE_HAS_ACTIVITY"

    async def test_delete_account_agent_only(self) -> None:
        from colony_sdk import ColonyAuthError

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(
                {"detail": {"message": "agent only", "code": "AUTH_AGENT_ONLY"}},
                status=403,
            )

        client = _make_client(handler)
        with pytest.raises(ColonyAuthError) as exc_info:
            await client.delete_account()
        assert exc_info.value.status == 403
        assert exc_info.value.code == "AUTH_AGENT_ONLY"


class TestPremium:
    """Async premium membership account-management methods (THECOLONYC-411)."""

    async def test_get_premium_status(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return _json_response({"is_premium": True, "auto_renew": False})

        client = _make_client(handler)
        result = await client.get_premium_status()

        assert result["is_premium"] is True
        assert seen["method"] == "GET"
        assert seen["url"] == f"{BASE}/premium/status"

    async def test_get_premium_pricing(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"program_enabled": True, "plans": []})

        client = _make_client(handler)
        result = await client.get_premium_pricing()

        assert result["program_enabled"] is True
        assert seen["url"] == f"{BASE}/premium/pricing"

    async def test_get_premium_history(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response([{"id": "m1", "status": "active"}])

        client = _make_client(handler)
        result = await client.get_premium_history()

        assert result == [{"id": "m1", "status": "active"}]
        assert seen["url"] == f"{BASE}/premium/history"

    async def test_get_premium_history_handles_bare_list_from_raw_request(self) -> None:
        # Defensive path (mirrors list_claims): if _raw_request's
        # response-wrapping policy ever changes and a bare list arrives,
        # get_premium_history must still return it.
        client = AsyncColonyClient("col_test")
        client._token = "fake-jwt"
        client._token_expiry = 9_999_999_999

        async def fake_raw(method: str, path: str, **kw: object) -> object:
            return [{"id": "m1"}]

        client._raw_request = fake_raw  # type: ignore[method-assign]
        result = await client.get_premium_history()
        assert result == [{"id": "m1"}]

    async def test_get_premium_history_unknown_envelope_returns_empty(self) -> None:
        # Defensive: an envelope without a ``data`` key yields ``[]``.
        client = AsyncColonyClient("col_test")
        client._token = "fake-jwt"
        client._token_expiry = 9_999_999_999

        async def fake_raw(method: str, path: str, **kw: object) -> object:
            return {"unexpected": "shape"}

        client._raw_request = fake_raw  # type: ignore[method-assign]
        result = await client.get_premium_history()
        assert result == []

    async def test_subscribe_premium(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            seen["body"] = json.loads(request.content)
            return _json_response({"status": "pending"})

        client = _make_client(handler)
        result = await client.subscribe_premium("annual")

        assert result["status"] == "pending"
        assert seen["method"] == "POST"
        assert seen["url"] == f"{BASE}/premium/subscribe"
        assert seen["body"] == {"period": "annual"}

    async def test_get_premium_invoice(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _json_response({"payment_hash": "abc", "status": "active"})

        client = _make_client(handler)
        result = await client.get_premium_invoice("abc")

        assert result["status"] == "active"
        assert seen["url"] == f"{BASE}/premium/invoice/abc"

    async def test_set_premium_auto_renew(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            seen["body"] = json.loads(request.content)
            return _json_response({"auto_renew": True})

        client = _make_client(handler)
        result = await client.set_premium_auto_renew(True)

        assert result["auto_renew"] is True
        assert seen["method"] == "POST"
        assert seen["url"] == f"{BASE}/premium/auto-renew"
        assert seen["body"] == {"enabled": True}


class TestRecoveryEmailAsync:
    """Async recovery email + lost-key recovery (THECOLONYC-262)."""

    async def test_get_recovery_email(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return _json_response({"email": "a@example.com", "email_verified": True})

        client = _make_client(handler)
        result = await client.get_recovery_email()

        assert seen["method"] == "GET"
        assert seen["path"] == "/api/v1/auth/email"
        assert result == {"email": "a@example.com", "email_verified": True}

    async def test_set_recovery_email(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return _json_response({"email": "a@example.com", "verification_sent": True})

        client = _make_client(handler)
        result = await client.set_recovery_email("a@example.com")

        assert seen["method"] == "POST"
        assert seen["path"] == "/api/v1/auth/email"
        assert seen["body"] == {"email": "a@example.com"}
        assert result["verification_sent"] is True

    async def test_recover_key_is_unauthenticated(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            seen["auth"] = request.headers.get("Authorization")
            return _json_response({"message": "If that account..."})

        client = _make_client(handler)
        result = await client.recover_key("lost-agent")

        assert seen["method"] == "POST"
        assert seen["path"] == "/api/v1/auth/recover-key"
        assert seen["body"] == {"username": "lost-agent"}
        # auth=False — the bearer token must not be attached.
        assert seen["auth"] is None
        assert "message" in result

    async def test_confirm_key_recovery_updates_api_key(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return _json_response({"api_key": "col_recovered_key"})

        client = _make_client(handler)
        client._token = "stale-token"
        client._token_expiry = 9_999_999_999
        result = await client.confirm_key_recovery("recovery-token")

        assert seen["method"] == "POST"
        assert seen["path"] == "/api/v1/auth/recover-key/confirm"
        assert seen["body"] == {"token": "recovery-token"}
        assert result == {"api_key": "col_recovered_key"}
        assert client.api_key == "col_recovered_key"
        assert client._token is None
        assert client._token_expiry == 0

    async def test_confirm_key_recovery_invalid_token(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"detail": {"message": "invalid", "code": "INVALID_INPUT"}}, status=400)

        client = _make_client(handler)
        from colony_sdk import ColonyValidationError

        with pytest.raises(ColonyValidationError) as exc_info:
            await client.confirm_key_recovery("bad")
        assert exc_info.value.status == 400
        assert exc_info.value.code == "INVALID_INPUT"
        # The existing key is untouched on failure.
        assert client.api_key == "col_test"


# ---------------------------------------------------------------------------
# Post lifecycle (crosspost / pin / close / reopen / language)
#
# These five async methods had no coverage at all — the sync twins are tested,
# the async ones never were. Added here because the UUID guard put a new line in
# each of them and Codecov (correctly) refused to let an untested line land.
# ---------------------------------------------------------------------------

_POST_ID = "2a2579a2-c0db-486a-ba05-3ef7ea05fc3d"


class TestPostLifecycle:
    @pytest.mark.asyncio
    async def test_crosspost_posts_to_the_crosspost_path(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return _json_response({"id": _POST_ID})

        client = _make_client(handler)
        await client.crosspost(_POST_ID, "general", title="Retitled")
        assert seen["method"] == "POST"
        assert seen["path"] == f"/api/v1/posts/{_POST_ID}/crosspost"
        assert seen["body"]["colony_id"] == "general"
        assert seen["body"]["title"] == "Retitled"

    @pytest.mark.asyncio
    async def test_crosspost_omits_title_when_not_given(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _json_response({"id": _POST_ID})

        client = _make_client(handler)
        await client.crosspost(_POST_ID, "general")
        assert "title" not in seen["body"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "path"),
        [("pin_post", "pin"), ("close_post", "close"), ("reopen_post", "reopen")],
    )
    async def test_post_toggles(self, method: str, path: str) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return _json_response({"id": _POST_ID})

        client = _make_client(handler)
        await getattr(client, method)(_POST_ID)
        assert seen["method"] == "POST"
        assert seen["path"] == f"/api/v1/posts/{_POST_ID}/{path}"

    @pytest.mark.asyncio
    async def test_set_post_language_puts_the_code_in_the_query(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return _json_response({"post_id": _POST_ID, "language": "en"})

        client = _make_client(handler)
        result = await client.set_post_language(_POST_ID, "en")
        assert seen["method"] == "PUT"
        assert f"/posts/{_POST_ID}/language" in seen["url"]
        assert "language=en" in seen["url"]
        assert result["language"] == "en"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["crosspost", "pin_post", "close_post", "reopen_post", "set_post_language"])
    async def test_rejects_a_truncated_post_id(self, method: str) -> None:
        """The guard fires before any request is built — a truncated id never leaves the process."""

        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
            raise AssertionError("request should not have been made")

        client = _make_client(handler)
        args = {"crosspost": ("general",), "set_post_language": ("en",)}.get(method, ())
        with pytest.raises(ValueError, match="truncated UUID"):
            await getattr(client, method)("2a2579a2", *args)


class TestEchoes:
    """The async echo surface, exercised rather than merely present.

    ``test_sync_async_parity`` proves these methods EXIST on both
    clients. It cannot prove they work: it reads names off the class.
    The bug that ratchet was written for — ``vault_append_file`` shipped
    sync-only — is one of two failure modes, and the other is a method
    that exists on both and behaves differently on one.
    """

    async def test_create_posts_the_body(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _json_response({"id": "echo-1", "commentary": "worth reading"})

        client = _make_client(handler)
        await client.create_echo("11111111-1111-1111-1111-111111111111", "worth reading")
        assert seen[0].method == "POST"
        assert str(seen[0].url) == f"{BASE}/echoes"
        assert json.loads(seen[0].content) == {
            "post_id": "11111111-1111-1111-1111-111111111111",
            "commentary": "worth reading",
        }

    async def test_over_length_commentary_never_reaches_the_network(self) -> None:
        """Same local check as the sync client. Three attempts per day
        means a drift here costs a real allowance, not a round-trip."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _json_response({})

        client = _make_client(handler)
        with pytest.raises(ValueError):
            await client.create_echo("11111111-1111-1111-1111-111111111111", "x" * 301)
        assert seen == []

    async def test_list_and_iterate(self) -> None:
        pages = [
            {"items": [{"id": "e0"}, {"id": "e1"}], "total": 3, "has_more": True},
            {"items": [{"id": "e2"}], "total": 3, "has_more": False},
        ]
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return _json_response(pages[len(seen) - 1])

        client = _make_client(handler)
        got = [e async for e in client.iter_echoes(page_size=2)]
        assert [e["id"] for e in got] == ["e0", "e1", "e2"]
        assert "limit=2" in seen[0] and "offset" not in seen[0]
        assert "offset=2" in seen[1]

    async def test_delete_uses_the_echo_id(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _json_response({})

        client = _make_client(handler)
        await client.delete_echo("22222222-2222-2222-2222-222222222222")
        assert seen[0].method == "DELETE"
        assert str(seen[0].url).endswith("/echoes/22222222-2222-2222-2222-222222222222")

    async def test_a_day_long_retry_after_raises_instead_of_sleeping(self, monkeypatch) -> None:
        """The async twin of the 48-hour block. ``asyncio.sleep`` does
        not hold the event loop the way ``time.sleep`` holds a thread,
        but a coroutine that returns tomorrow is no more useful to its
        caller than one that blocks."""
        import asyncio

        from colony_sdk import ColonyRateLimitError

        slept: list[float] = []

        async def _fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                headers={"Retry-After": "86400"},
                content=b'{"detail":{"message":"nope","code":"RATE_LIMIT_ECHO_CREATE"}}',
            )

        client = _make_client(handler)
        with pytest.raises(ColonyRateLimitError) as exc:
            await client.create_echo("11111111-1111-1111-1111-111111111111", "worth reading")
        assert slept == []
        assert exc.value.retry_after == 86400
