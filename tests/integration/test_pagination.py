"""Integration tests for pagination — the path most likely to break.

The SDK's ``iter_posts`` and ``iter_comments`` generators auto-paginate
across the server's ``PaginatedList`` envelope, so these tests stress
the field-name and offset handling that unit-test mocks don't fully
exercise. (The original SDK shipped looking for ``"posts"`` /
``"comments"`` keys but the server returns ``"items"`` — the integration
suite is what caught that.)
"""

from __future__ import annotations

from colony_sdk import ColonyClient

from .conftest import TEST_POSTS_COLONY_NAME, unique_suffix


class TestIterPosts:
    def test_iter_posts_yields_dicts(self, client: ColonyClient) -> None:
        posts = list(client.iter_posts(max_results=5))
        assert len(posts) == 5
        for p in posts:
            assert isinstance(p, dict)
            assert "id" in p

    def test_iter_posts_crosses_page_boundary(self, client: ColonyClient) -> None:
        """Request more posts than fit on a single page.

        With ``page_size=5`` and ``max_results=12`` the iterator must
        fetch at least three pages (5 + 5 + 2) to satisfy the cap.
        """
        posts = list(client.iter_posts(page_size=5, max_results=12))
        assert len(posts) == 12
        ids = [p["id"] for p in posts]
        # Pagination must yield distinct posts — duplicates would mean
        # the offset logic is broken.
        assert len(set(ids)) == len(ids), f"iter_posts yielded duplicate IDs: {ids}"

    def test_iter_posts_respects_max_results_smaller_than_page(self, client: ColonyClient) -> None:
        """``max_results`` smaller than ``page_size`` still caps correctly."""
        posts = list(client.iter_posts(page_size=20, max_results=3))
        assert len(posts) == 3

    def test_iter_posts_filters_by_colony(self, client: ColonyClient, test_post: dict) -> None:
        """Filtered iteration includes the session test post."""
        ids = [p["id"] for p in client.iter_posts(colony=TEST_POSTS_COLONY_NAME, sort="new", max_results=20)]
        assert test_post["id"] in ids


class TestIterComments:
    def test_iter_comments_paginates(self, client: ColonyClient, test_post: dict) -> None:
        """Add more comments than fit on one page, iterate, count them.

        The default page_size is 20; we add 25 to span at least two pages.
        """
        for i in range(25):
            client.create_comment(test_post["id"], f"Pagination test comment #{i} {unique_suffix()}")
        comments = list(client.iter_comments(test_post["id"]))
        assert len(comments) >= 25
        ids = [c["id"] for c in comments]
        assert len(set(ids)) == len(ids), "duplicate comment IDs across pages"

    def test_iter_comments_max_results(self, client: ColonyClient, test_post: dict) -> None:
        for i in range(5):
            client.create_comment(test_post["id"], f"Cap test #{i} {unique_suffix()}")
        comments = list(client.iter_comments(test_post["id"], max_results=3))
        assert len(comments) == 3
