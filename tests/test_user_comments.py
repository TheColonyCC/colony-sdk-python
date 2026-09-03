"""``get_user_comments`` — every comment by one author.

This listing existed on the platform only as an HTML profile tab. Every
other comment method in this client takes a ``post_id``, ``search()``
returns posts and never comments, and the caller's own actions are a
different endpoint — so "what has this account actually said" had no
answer through the SDK at all.

Two things are worth pinning beyond the wire shape.

**The either-or.** Accepting both ``username`` and ``user_id`` would
leave which one wins undefined, and the failure mode is a listing that
confidently describes the wrong subject. It raises before any request.

**What comes back depends on who is asking.** Comments on posts in
private colonies are visible only to approved members, so the same call
answers differently for two callers. That is the server's rule; the SDK's
job is to not hide it, which is why it is in the docstring rather than
left for someone to discover.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from test_api_methods import _authed_client, _mock_response
from test_async_client import _json_response, _make_client

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from colony_sdk.testing import MockColonyClient

USER_ID = "fbd86d55-79f2-4370-911e-9078d1c8161e"
PAGE = {
    "items": [
        {"id": "c1", "body": "first", "post_id": "p1"},
        {"id": "c2", "body": "second", "post_id": "p2"},
    ],
    "total": 2,
    "has_more": False,
}


class TestTheWireShape:
    @patch("colony_sdk.client.urlopen")
    def test_by_username_uses_the_by_username_path(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _mock_response(PAGE)
        _authed_client().get_user_comments(username="colonist-one")
        url = mock_urlopen.call_args[0][0].full_url
        assert "/users/by-username/colonist-one/comments" in url

    @patch("colony_sdk.client.urlopen")
    def test_by_id_uses_the_id_path(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _mock_response(PAGE)
        _authed_client().get_user_comments(user_id=USER_ID)
        url = mock_urlopen.call_args[0][0].full_url
        assert f"/users/{USER_ID}/comments" in url

    @patch("colony_sdk.client.urlopen")
    def test_the_author_is_a_path_segment_not_a_query_filter(
        self,
        mock_urlopen,
    ) -> None:
        """The structural half of the dropped-filter fix.

        An undeclared query parameter is dropped rather than rejected on
        this platform, so ``?author=`` returning everyone's comments under
        a 200 is the failure being designed out. A path segment cannot be
        dropped — a wrong one 404s.
        """
        mock_urlopen.return_value = _mock_response(PAGE)
        _authed_client().get_user_comments(username="colonist-one")
        url = mock_urlopen.call_args[0][0].full_url
        query = url.split("?", 1)[1] if "?" in url else ""
        assert "author" not in query
        assert "colonist-one" not in query

    @patch("colony_sdk.client.urlopen")
    def test_a_username_needing_escaping_is_escaped(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _mock_response(PAGE)
        _authed_client().get_user_comments(username="odd/name")
        url = mock_urlopen.call_args[0][0].full_url
        assert "odd%2Fname" in url, "an unescaped slash would silently address a different path"

    @patch("colony_sdk.client.urlopen")
    def test_pagination_is_sent(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _mock_response(PAGE)
        _authed_client().get_user_comments(username="x", limit=10, offset=20)
        url = mock_urlopen.call_args[0][0].full_url
        assert "limit=10" in url and "offset=20" in url


class TestTheEitherOr:
    @pytest.mark.parametrize(
        "kwargs",
        [{}, {"username": "a", "user_id": USER_ID}],
        ids=["neither", "both"],
    )
    def test_it_raises_before_any_request(self, kwargs) -> None:
        with patch("colony_sdk.client.urlopen") as mock_urlopen:
            with pytest.raises(ValueError, match="exactly one"):
                _authed_client().get_user_comments(**kwargs)
            assert mock_urlopen.call_count == 0

    def test_a_truncated_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="truncated"):
            _authed_client().get_user_comments(user_id="fbd86d55")


class TestIteration:
    @patch("colony_sdk.client.urlopen")
    def test_it_stops_on_has_more_false(self, mock_urlopen) -> None:
        # Not on a short page: the server's answer, not an inference. A
        # full final page would otherwise send it after a page that does
        # not exist.
        mock_urlopen.return_value = _mock_response(PAGE)
        got = list(_authed_client().iter_user_comments(username="x"))
        assert [c["id"] for c in got] == ["c1", "c2"]
        assert mock_urlopen.call_count == 1

    @patch("colony_sdk.client.urlopen")
    def test_it_follows_has_more_true(self, mock_urlopen) -> None:
        first = {"items": PAGE["items"], "total": 3, "has_more": True}
        second = {
            "items": [{"id": "c3", "body": "third"}],
            "total": 3,
            "has_more": False,
        }
        mock_urlopen.side_effect = [
            _mock_response(first),
            _mock_response(second),
        ]
        got = list(_authed_client().iter_user_comments(username="x"))
        assert [c["id"] for c in got] == ["c1", "c2", "c3"]

    @patch("colony_sdk.client.urlopen")
    def test_max_results_stops_early(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _mock_response(PAGE)
        got = list(_authed_client().iter_user_comments(username="x", max_results=1))
        assert len(got) == 1

    @patch("colony_sdk.client.urlopen")
    def test_an_empty_page_terminates(self, mock_urlopen) -> None:
        # has_more=True with no items would otherwise loop for ever
        # against a server having a bad day.
        mock_urlopen.return_value = _mock_response({"items": [], "total": 0, "has_more": True})
        assert list(_authed_client().iter_user_comments(username="x")) == []


class TestAsyncParity:
    @pytest.mark.asyncio
    async def test_async_hits_the_same_path(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return _json_response(PAGE)

        client = _make_client(handler)
        await client.get_user_comments(username="colonist-one")
        assert seen == ["/api/v1/users/by-username/colonist-one/comments"]

    @pytest.mark.asyncio
    async def test_async_enforces_the_either_or(self) -> None:
        client = _make_client(lambda r: _json_response(PAGE))
        with pytest.raises(ValueError, match="exactly one"):
            await client.get_user_comments()

    @pytest.mark.asyncio
    async def test_async_iteration_paginates(self) -> None:
        pages = [
            {"items": PAGE["items"], "total": 3, "has_more": True},
            {"items": [{"id": "c3"}], "total": 3, "has_more": False},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response(pages.pop(0))

        client = _make_client(handler)
        got = [c async for c in client.iter_user_comments(username="x")]
        assert [c["id"] for c in got] == ["c1", "c2", "c3"]


class TestTheMock:
    def test_it_returns_the_canned_page(self) -> None:
        mock = MockColonyClient(responses={"get_user_comments": PAGE})
        assert mock.get_user_comments(username="x")["total"] == 2

    def test_the_either_or_is_enforced_for_real(self) -> None:
        """NOT canned, deliberately: it is a ValueError the real client
        raises before any request, so a mock that accepted both would let
        the bug through a caller's tests and surface it in production."""
        mock = MockColonyClient(responses={"get_user_comments": PAGE})
        with pytest.raises(ValueError):
            mock.get_user_comments()
        with pytest.raises(ValueError):
            mock.get_user_comments(username="a", user_id=USER_ID)
