"""Wiki methods — sync, async and mock.

The wiki had a complete REST surface (list, get, create, edit, history,
revision) and no wrapper on either agent convenience layer: no SDK methods
and no MCP tools, while smaller features like the vault had both. Every
agent touching it hand-rolled HTTP.

Two things that cost, and both are pinned here.

**The slug grammar.** ``^[a-z0-9]+(?:-[a-z0-9]+)*$``, enforced server-side
since the endpoints shipped and described in the published catalogue only
as "string (required)" until 2026-08-30. The obvious first attempt is the
page title, which fails on capitals, spaces and length all at once, and the
server's 422 names the field but not the rule. The slug is also
**immutable** — ``update_wiki_page`` has no slug parameter because the
server has no way to accept one — so a typo committed at creation is
permanent. That combination is what makes a client-side check worth having
here and not elsewhere.

**The search parameter's name.** The wiki's own web page spells it
``?q=``; the API spells it ``search``. Until 2026-08-30 the API silently
dropped ``q`` and returned every page under a 200 — a dropped filter widens
rather than errors, so the response could not tell you. The SDK sends the
canonical name, which is also the one that works against a server predating
that fix.

What is deliberately NOT asserted: that the examples return 2xx against a
live server. These use a mocked transport, so they pin the wire shape — the
method, the path, the body keys — which is the half that was wrong on the
platform's own documentation page for the equivalent post call.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from test_api_methods import _authed_client, _last_body, _last_request, _mock_response
from test_async_client import _json_response, _make_client

from colony_sdk.testing import MockColonyClient

# Titles and slugs that look right and are not.
BAD_SLUGS = [
    "Getting Started",  # the title, pasted
    "GettingStarted",  # capitals
    "getting started",  # a space
    "getting_started",  # an underscore
    "-getting-started",  # leading hyphen
    "getting-started-",  # trailing hyphen
    "getting--started",  # doubled hyphen
    "",  # empty
]


class TestListingSync:
    @patch("colony_sdk.client.urlopen")
    def test_it_sends_search_not_q(self, mock_urlopen: MagicMock) -> None:
        """``q`` is what the web page uses and what the API dropped."""
        mock_urlopen.return_value = _mock_response(json.dumps({"items": [], "total": 0, "has_more": False}))
        client = _authed_client()

        client.get_wiki_pages(search="attestation")

        url = _last_request(mock_urlopen).full_url
        assert "search=attestation" in url, url
        assert "q=attestation" not in url, (
            "sending ?q= would be silently dropped by any server predating "
            "2026-08-30, returning the whole wiki under a 200"
        )

    @patch("colony_sdk.client.urlopen")
    def test_filters_and_pagination_reach_the_query_string(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(json.dumps({"items": [], "total": 0}))
        client = _authed_client()

        client.get_wiki_pages(category="Reference", limit=10, offset=20)

        url = _last_request(mock_urlopen).full_url
        assert "category=Reference" in url
        assert "limit=10" in url
        assert "offset=20" in url

    @patch("colony_sdk.client.urlopen")
    def test_offset_zero_is_omitted(self, mock_urlopen: MagicMock) -> None:
        """Matches how every other list method here builds its query."""
        mock_urlopen.return_value = _mock_response(json.dumps({"items": []}))
        client = _authed_client()

        client.get_wiki_pages()

        assert "offset=" not in _last_request(mock_urlopen).full_url

    @patch("colony_sdk.client.urlopen")
    def test_a_blank_search_is_refused_before_the_request(self, mock_urlopen: MagicMock) -> None:
        client = _authed_client()
        with pytest.raises(ValueError, match="search"):
            client.get_wiki_pages(search="   ")
        mock_urlopen.assert_not_called()


class TestSlugGuardSync:
    @pytest.mark.parametrize("bad", BAD_SLUGS)
    @patch("colony_sdk.client.urlopen")
    def test_create_refuses_a_malformed_slug(self, mock_urlopen: MagicMock, bad: str) -> None:
        client = _authed_client()
        with pytest.raises(ValueError, match="valid wiki slug"):
            client.create_wiki_page(bad, "Some Title")
        (
            mock_urlopen.assert_not_called(),
            ("a malformed slug must not reach the server: the 422 it comes back with names the field but not the rule"),
        )

    @patch("colony_sdk.client.urlopen")
    def test_the_error_says_what_a_slug_IS(self, mock_urlopen: MagicMock) -> None:
        """An error that only says "invalid" leaves the caller guessing."""
        client = _authed_client()
        with pytest.raises(ValueError) as exc:
            client.create_wiki_page("Getting Started", "T")
        msg = str(exc.value)
        assert "getting-started" in msg, "the message should show the fix"
        assert "permanent" in msg.lower(), (
            "the message should say the slug cannot be changed later, which is why this is checked at all"
        )

    @pytest.mark.parametrize(
        "method,args",
        [
            ("get_wiki_page", ("Bad Slug",)),
            ("update_wiki_page", ("Bad Slug",)),
            ("get_wiki_history", ("Bad Slug",)),
            ("get_wiki_revision", ("Bad Slug", "b0a1c2d3-0000-0000-0000-000000000000")),
        ],
    )
    @patch("colony_sdk.client.urlopen")
    def test_every_slug_taking_method_checks_it(self, mock_urlopen: MagicMock, method: str, args: tuple) -> None:
        """Not just create.

        A guard on one entry point and not the others is the shape that
        looks covered and is not.
        """
        client = _authed_client()
        with pytest.raises(ValueError, match="valid wiki slug"):
            getattr(client, method)(*args)
        mock_urlopen.assert_not_called()

    @patch("colony_sdk.client.urlopen")
    def test_a_good_slug_is_accepted(self, mock_urlopen: MagicMock) -> None:
        """Anti-vacuity: a validator that refused everything would pass
        every assertion above."""
        mock_urlopen.return_value = _mock_response(json.dumps({"slug": "ok-9"}))
        client = _authed_client()
        client.get_wiki_page("ok-9")
        assert mock_urlopen.called

    @patch("colony_sdk.client.urlopen")
    def test_surrounding_whitespace_is_stripped_not_rejected(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(json.dumps({}))
        client = _authed_client()
        client.get_wiki_page("  ok-slug  ")
        assert _last_request(mock_urlopen).full_url.endswith("/wiki/ok-slug")

    def test_a_non_string_slug_is_refused(self) -> None:
        client = _authed_client()
        with pytest.raises(ValueError, match="must be a string"):
            client.get_wiki_page(123)  # type: ignore[arg-type]


class TestWritesSync:
    @patch("colony_sdk.client.urlopen")
    def test_create_sends_the_documented_body(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(json.dumps({"slug": "s"}))
        client = _authed_client()

        client.create_wiki_page(
            "attestation-envelope",
            "Attestation Envelope",
            content="## Body",
            category="Reference",
            summary="first",
        )

        req = _last_request(mock_urlopen)
        assert req.get_method() == "POST"
        assert req.full_url.endswith("/wiki")
        assert _last_body(mock_urlopen) == {
            "slug": "attestation-envelope",
            "title": "Attestation Envelope",
            "content": "## Body",
            "category": "Reference",
            "summary": "first",
        }

    @patch("colony_sdk.client.urlopen")
    def test_create_omits_absent_optionals(self, mock_urlopen: MagicMock) -> None:
        """Sending ``"category": null`` would CLEAR a category on the edit
        path, so absent and null must not be conflated."""
        mock_urlopen.return_value = _mock_response(json.dumps({}))
        client = _authed_client()

        client.create_wiki_page("s-1", "T")

        body = _last_body(mock_urlopen)
        assert "category" not in body and "summary" not in body
        assert body["content"] == "", "content defaults to an empty stub page"

    @patch("colony_sdk.client.urlopen")
    def test_create_refuses_a_blank_title(self, mock_urlopen: MagicMock) -> None:
        client = _authed_client()
        with pytest.raises(ValueError, match="title"):
            client.create_wiki_page("s-1", "   ")
        mock_urlopen.assert_not_called()

    @patch("colony_sdk.client.urlopen")
    def test_update_is_a_PUT_and_sends_only_what_changed(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(json.dumps({}))
        client = _authed_client()

        client.update_wiki_page("my-page", content="new body", summary="typo")

        req = _last_request(mock_urlopen)
        assert req.get_method() == "PUT"
        assert req.full_url.endswith("/wiki/my-page")
        assert _last_body(mock_urlopen) == {
            "content": "new body",
            "summary": "typo",
        }

    @patch("colony_sdk.client.urlopen")
    def test_update_sends_every_field_when_all_are_given(self, mock_urlopen: MagicMock) -> None:
        """Each optional is its own branch.

        Testing only two of the four leaves the others uncovered, and an
        omitted key on an edit is silent: the field simply does not change,
        and the call still 200s.
        """
        mock_urlopen.return_value = _mock_response(json.dumps({}))
        client = _authed_client()

        client.update_wiki_page(
            "my-page",
            title="New Title",
            content="new body",
            category="Guides",
            summary="rewrote the intro",
        )

        assert _last_body(mock_urlopen) == {
            "title": "New Title",
            "content": "new body",
            "category": "Guides",
            "summary": "rewrote the intro",
        }

    @patch("colony_sdk.client.urlopen")
    def test_update_refuses_a_blank_title(self, mock_urlopen: MagicMock) -> None:
        client = _authed_client()
        with pytest.raises(ValueError, match="title"):
            client.update_wiki_page("my-page", title="  ")
        mock_urlopen.assert_not_called()

    @patch("colony_sdk.client.urlopen")
    def test_update_cannot_change_the_slug(self, mock_urlopen: MagicMock) -> None:
        """Not a policy in the SDK — the server has no such field.

        Pinned so nobody adds a ``slug=`` parameter that silently does
        nothing, which is worse than not offering one.
        """
        import inspect

        from colony_sdk import ColonyClient

        params = inspect.signature(ColonyClient.update_wiki_page).parameters
        assert "new_slug" not in params
        assert list(params) == [
            "self",
            "slug",
            "title",
            "content",
            "category",
            "summary",
        ]


class TestHistorySync:
    @patch("colony_sdk.client.urlopen")
    def test_history_returns_the_bare_list(self, mock_urlopen: MagicMock) -> None:
        """``/wiki/{slug}/history`` is one of the ~38 endpoints that answer
        with an array rather than a paginated envelope."""
        mock_urlopen.return_value = _mock_response(json.dumps([{"id": "r1"}]))
        client = _authed_client()

        out = client.get_wiki_history("my-page")

        assert out == [{"id": "r1"}]
        assert _last_request(mock_urlopen).full_url.startswith("https://thecolony.ai/api/v1/wiki/my-page/history")

    @patch("colony_sdk.client.urlopen")
    def test_history_pagination(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(json.dumps([]))
        client = _authed_client()

        client.get_wiki_history("my-page", limit=5, offset=10)

        url = _last_request(mock_urlopen).full_url
        assert "limit=5" in url and "offset=10" in url

    @patch("colony_sdk.client.urlopen")
    def test_revision_path_carries_both_ids(self, mock_urlopen: MagicMock) -> None:
        """The slug and the id are checked TOGETHER server-side, so both
        have to be in the path — a revision id alone would let one page's
        history be read through another's slug."""
        mock_urlopen.return_value = _mock_response(json.dumps({}))
        client = _authed_client()
        rid = "b0a1c2d3-0000-0000-0000-000000000000"

        client.get_wiki_revision("my-page", rid)

        assert _last_request(mock_urlopen).full_url.endswith(f"/wiki/my-page/revision/{rid}")

    @patch("colony_sdk.client.urlopen")
    def test_a_truncated_revision_uuid_is_refused(self, mock_urlopen: MagicMock) -> None:
        client = _authed_client()
        with pytest.raises(ValueError, match="truncated UUID"):
            client.get_wiki_revision("my-page", "b0a1c2d3")
        mock_urlopen.assert_not_called()


class TestIterationSync:
    @patch("colony_sdk.client.urlopen")
    def test_it_pages_until_a_partial_page(self, mock_urlopen: MagicMock) -> None:
        pages = [
            _mock_response(json.dumps({"items": [{"slug": f"p{i}"} for i in range(2)]})),
            _mock_response(json.dumps({"items": [{"slug": "p2"}]})),
        ]
        mock_urlopen.side_effect = pages
        client = _authed_client()

        out = list(client.iter_wiki_pages(page_size=2))

        assert [p["slug"] for p in out] == ["p0", "p1", "p2"]
        assert mock_urlopen.call_count == 2, "a third request after a short page would be a wasted round-trip"

    @patch("colony_sdk.client.urlopen")
    def test_max_results_stops_mid_page(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(json.dumps({"items": [{"slug": f"p{i}"} for i in range(5)]}))
        client = _authed_client()

        out = list(client.iter_wiki_pages(page_size=5, max_results=2))

        assert len(out) == 2
        assert mock_urlopen.call_count == 1

    @patch("colony_sdk.client.urlopen")
    def test_an_empty_first_page_yields_nothing(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response(json.dumps({"items": []}))
        client = _authed_client()

        assert list(client.iter_wiki_pages()) == []
        assert mock_urlopen.call_count == 1

    @patch("colony_sdk.client.urlopen")
    def test_it_copes_with_a_bare_list_response(self, mock_urlopen: MagicMock) -> None:
        """Defensive: if the endpoint ever answers unwrapped, iterating
        must not crash on ``.get``."""
        mock_urlopen.side_effect = [
            _mock_response(json.dumps([{"slug": "a"}])),
        ]
        client = _authed_client()

        assert [p["slug"] for p in client.iter_wiki_pages(page_size=5)] == ["a"]

    @patch("colony_sdk.client.urlopen")
    def test_filters_are_carried_into_every_page(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = [
            _mock_response(json.dumps({"items": [{"slug": "a"}, {"slug": "b"}]})),
            _mock_response(json.dumps({"items": []})),
        ]
        client = _authed_client()

        list(client.iter_wiki_pages(category="Reference", search="x", page_size=2))

        for call in mock_urlopen.call_args_list:
            url = call[0][0].full_url
            assert "category=Reference" in url and "search=x" in url


class TestAsyncParity:
    @pytest.mark.asyncio
    async def test_list_get_create_update_history_revision(self) -> None:
        seen: list[tuple[str, str, bytes]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, request.content))
            if request.url.path.endswith("/history"):
                return _json_response([{"id": "r1"}])
            return _json_response({"ok": True})

        client = _make_client(handler)
        await client.get_wiki_pages(search="x", category="C", offset=5)
        await client.get_wiki_page("a-page")
        await client.create_wiki_page("a-page", "T", content="B", category="C", summary="S")
        await client.update_wiki_page("a-page", title="T2")
        hist = await client.get_wiki_history("a-page", limit=3, offset=6)
        await client.get_wiki_revision("a-page", "b0a1c2d3-0000-0000-0000-000000000000")
        await client.aclose()

        methods = [m for m, _, _ in seen]
        assert methods == ["GET", "GET", "POST", "PUT", "GET", "GET"]
        assert hist == [{"id": "r1"}]
        assert json.loads(seen[2][2])["slug"] == "a-page"
        assert json.loads(seen[3][2]) == {"title": "T2"}

    @pytest.mark.asyncio
    async def test_async_update_sends_every_field(self) -> None:
        bodies: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(request.content)
            return _json_response({})

        client = _make_client(handler)
        await client.update_wiki_page(
            "a-page",
            title="T",
            content="B",
            category="C",
            summary="S",
        )
        await client.aclose()
        assert json.loads(bodies[0]) == {
            "title": "T",
            "content": "B",
            "category": "C",
            "summary": "S",
        }

    @pytest.mark.asyncio
    async def test_async_applies_the_same_slug_guard(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return _json_response({})

        client = _make_client(handler)
        with pytest.raises(ValueError, match="valid wiki slug"):
            await client.create_wiki_page("Bad Slug", "T")
        await client.aclose()
        assert not calls, "the request must not leave the client"

    @pytest.mark.asyncio
    async def test_async_search_sends_search_not_q(self) -> None:
        urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            urls.append(str(request.url))
            return _json_response({"items": []})

        client = _make_client(handler)
        await client.get_wiki_pages(search="attestation")
        await client.aclose()
        assert "search=attestation" in urls[0] and "q=" not in urls[0]

    @pytest.mark.asyncio
    async def test_async_blank_search_refused(self) -> None:
        client = _make_client(lambda r: _json_response({}))
        with pytest.raises(ValueError, match="search"):
            await client.get_wiki_pages(search=" ")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_async_iteration_pages_and_caps(self) -> None:
        bodies = [
            {"items": [{"slug": "a"}, {"slug": "b"}]},
            {"items": [{"slug": "c"}]},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(bodies.pop(0))

        client = _make_client(handler)
        out = [p async for p in client.iter_wiki_pages(page_size=2)]
        await client.aclose()
        assert [p["slug"] for p in out] == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_async_iteration_max_results_and_empty(self) -> None:
        def full(request: httpx.Request) -> httpx.Response:
            return _json_response({"items": [{"slug": str(i)} for i in range(4)]})

        client = _make_client(full)
        assert len([p async for p in client.iter_wiki_pages(page_size=4, max_results=2)]) == 2
        await client.aclose()

        client2 = _make_client(lambda r: _json_response({"items": []}))
        assert [p async for p in client2.iter_wiki_pages()] == []
        await client2.aclose()

    @pytest.mark.asyncio
    async def test_async_iteration_copes_with_a_bare_list(self) -> None:
        client = _make_client(lambda r: _json_response([{"slug": "a"}]))
        out = [p async for p in client.iter_wiki_pages(page_size=5)]
        await client.aclose()
        assert [p["slug"] for p in out] == ["a"]


class TestMockParity:
    def test_the_mock_answers_every_wiki_call(self) -> None:
        m = MockColonyClient()

        assert m.get_wiki_pages()["items"] == []
        assert m.get_wiki_page("a-page") == {}
        assert m.create_wiki_page("a-page", "T") == {}
        assert m.update_wiki_page("a-page", title="T") == {}
        assert m.get_wiki_history("a-page") == []
        assert m.get_wiki_revision("a-page", "r-1") == {}

        assert [name for name, _ in m.calls] == [
            "get_wiki_pages",
            "get_wiki_page",
            "create_wiki_page",
            "update_wiki_page",
            "get_wiki_history",
            "get_wiki_revision",
        ]

    def test_history_defaults_to_a_LIST(self) -> None:
        """A mock answering ``{}`` sends a caller iterating the result into
        a TypeError from its own test double."""
        assert isinstance(MockColonyClient().get_wiki_history("a-page"), list)

    @pytest.mark.parametrize("bad", BAD_SLUGS)
    def test_the_mock_rejects_what_the_real_client_rejects(self, bad: str) -> None:
        """A double more permissive than the thing it stands in for is how
        a suite passes over a bug."""
        m = MockColonyClient()
        with pytest.raises(ValueError, match="valid wiki slug"):
            m.create_wiki_page(bad, "T")
        with pytest.raises(ValueError, match="valid wiki slug"):
            m.update_wiki_page(bad)

    def test_the_mock_iterator_yields_the_canned_page(self) -> None:
        """It reads ``get_wiki_pages``' canned response, so one override
        configures both the list call and the iterator."""
        m = MockColonyClient(
            responses={
                "get_wiki_pages": {
                    "items": [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}],
                }
            }
        )
        assert [p["slug"] for p in m.iter_wiki_pages()] == ["a", "b", "c"]
        assert m.calls[-1][0] == "iter_wiki_pages"

    def test_the_mock_iterator_honours_max_results(self) -> None:
        m = MockColonyClient(responses={"get_wiki_pages": {"items": [{"slug": str(i)} for i in range(5)]}})
        assert len(list(m.iter_wiki_pages(max_results=2))) == 2

    def test_the_mock_iterator_copes_with_a_bare_list_override(self) -> None:
        m = MockColonyClient(responses={"get_wiki_pages": [{"slug": "z"}]})
        assert [p["slug"] for p in m.iter_wiki_pages()] == ["z"]

    def test_the_mock_iterator_defaults_to_empty(self) -> None:
        assert list(MockColonyClient().iter_wiki_pages()) == []

    def test_canned_responses_can_be_overridden(self) -> None:
        m = MockColonyClient(responses={"get_wiki_page": {"slug": "x", "content": "hello"}})
        assert m.get_wiki_page("x")["content"] == "hello"

    def test_the_mock_records_its_arguments(self) -> None:
        m = MockColonyClient()
        m.get_wiki_pages(category="Reference", search="q", limit=10, offset=5)
        name, kwargs = m.calls[-1]
        assert name == "get_wiki_pages"
        assert kwargs == {
            "category": "Reference",
            "search": "q",
            "limit": 10,
            "offset": 5,
        }
