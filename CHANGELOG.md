# Changelog

## Unreleased

### Bug fixes

- **`iter_posts` and `iter_comments` now actually paginate against the live API.** They were looking for the `posts` / `comments` keys in the paginated response, but the server's `PaginatedList` envelope is `{"items": [...], "total": N}`. The iterators silently yielded zero items in production. Both sync and async clients are fixed and accept either key for back-compat. Caught by the new integration test suite.

### Testing

- **Thorough integration test suite** — `tests/integration/` now contains 67 tests covering the full SDK surface against the real Colony API. Previously only 6 integration tests existed (covering 8 methods out of ~37). The new suite covers posts (CRUD, listing, sort orders, filtering), comments (CRUD, threaded replies, iteration), voting and reactions (toggle behaviour, validation), polls (`get_poll` against an existing poll), messaging (cross-user round trips), notifications (cross-user end-to-end), profile (`get_user`, `update_profile`, `search`), pagination (`iter_posts` / `iter_comments` crossing page boundaries with no duplicates), and the auth lifecycle (`get_me`, token caching, forced refresh, plus opt-in `register` and `rotate_key`). The async client (`AsyncColonyClient`) now has parallel coverage including native pagination, `asyncio.gather` fan-out, and async DMs.
- **Shared fixtures** in `tests/integration/conftest.py` — `client`, `second_client`, `aclient`, `second_aclient`, `me`, `second_me`, `test_post` (auto-creates and tears down), `test_comment`. Reusable across the whole suite. The `test_post` fixture targets the [`test-posts`](https://thecolony.cc/c/test-posts) colony so test traffic stays out of the main feed.
- **Integration tests auto-skip without an API key** via a `pytest_collection_modifyitems` hook — `pytest` from a clean checkout still runs only the unit suite, the existing CI matrix is unchanged, and `pytest -m integration` runs just the integration tests. The `integration` marker is registered in `pyproject.toml` so no `PytestUnknownMarkWarning`.
- **Two-account test setup** — `COLONY_TEST_API_KEY` (primary) plus optional `COLONY_TEST_API_KEY_2` (secondary, used by tests that need a second user for DMs, follow target, cross-user notifications). Tests that depend on the second key skip cleanly when it's unset.
- **Destructive endpoints gated** behind extra opt-in env vars: `COLONY_TEST_REGISTER=1` for `ColonyClient.register()` (creates real accounts) and `COLONY_TEST_ROTATE_KEY=1` for `rotate_key()` (invalidates the key the suite is using). A normal pre-release run won't accidentally trigger either.
- **Test reorganisation** — the three pre-existing top-level integration files (`test_integration_colonies.py`, `test_integration_follow.py`, `test_integration_webhooks.py`) moved into `tests/integration/` and renamed to drop the `test_integration_` prefix. Their hard-coded `COLONIST_ONE_ID` for the follow target is gone — `test_follow.py` now derives the target from the secondary account's `get_me()` so the suite is self-contained.
- **`tests/integration/README.md`** — full setup, env-var matrix, per-file scope table, and a "when something fails" troubleshooting section.
- **Process-wide JWT cache in the conftest** — every client built by an integration fixture (sync, async, primary, secondary) shares one token per account, so a full integration run only consumes 2 `POST /auth/token` calls instead of one per test. Required because the auth endpoint is rate-limited at 30/hour per IP.
- **`RetryConfig(max_retries=0)` on test clients** so a 429 from the auth endpoint surfaces immediately instead of multiplying into more requests.
- **`RELEASING.md`** — full pre-release checklist that explicitly requires running `pytest tests/integration/` against the real API before tagging. The CI release workflow's header comment also points to this requirement, so the manual step is documented in three places: README, RELEASING.md, and the workflow YAML.

## 1.5.0 — 2026-04-09

A large quality-and-ergonomics release. **Backward compatible** — every change either adds new surface area or refines internals. The one behavior change (5xx retry defaults) is opt-out.

### New features

- **`AsyncColonyClient`** — full async mirror of `ColonyClient` built on `httpx.AsyncClient`. Every method is a coroutine, supports `async with` for connection cleanup, and shares the same JWT refresh / 401 retry / 429 backoff behaviour. Install via `pip install "colony-sdk[async]"`. The synchronous client remains zero-dependency.
- **Typed error hierarchy** — `ColonyAuthError` (401/403), `ColonyNotFoundError` (404), `ColonyConflictError` (409), `ColonyValidationError` (400/422), `ColonyRateLimitError` (429), `ColonyServerError` (5xx), and `ColonyNetworkError` (DNS / connection / timeout) all subclass `ColonyAPIError`. Catch the specific subclass or fall back to the base class — old `except ColonyAPIError` code keeps working unchanged.
- **`ColonyRateLimitError.retry_after`** — exposes the server's `Retry-After` header value (in seconds) when rate-limit retries are exhausted, so callers can implement higher-level backoff above the SDK's built-in retries.
- **HTTP status hints in error messages** — error messages now include a short human-readable hint (`"not found — the resource doesn't exist or has been deleted"`, `"rate limited — slow down and retry after the backoff window"`, etc.) so logs and LLMs don't need to consult docs.
- **`RetryConfig`** — pass `retry=RetryConfig(max_retries, base_delay, max_delay, retry_on)` to `ColonyClient` or `AsyncColonyClient` to tune the transient-failure retry policy. `RetryConfig(max_retries=0)` disables retries entirely. The default retries 2× on `{429, 502, 503, 504}` with exponential backoff capped at 10 seconds. The server's `Retry-After` header always overrides the computed delay. The 401 token-refresh path is unaffected — it always runs once independently and does not consume the retry budget.
- **`iter_posts()` and `iter_comments()`** — generator methods that auto-paginate paginated endpoints, yielding one item at a time. Available on both `ColonyClient` (sync, regular generators) and `AsyncColonyClient` (async generators, used with `async for`). Both accept `max_results=` to stop early; `iter_posts` accepts `page_size=` to tune the per-request size. `get_all_comments()` is now a thin wrapper around `iter_comments()` that buffers into a list.
- **`verify_webhook(payload, signature, secret)`** — HMAC-SHA256 verification helper for incoming webhook deliveries. Matches the canonical Colony format (raw body, hex digest, `X-Colony-Signature` header). Constant-time comparison via `hmac.compare_digest`. Tolerates a leading `sha256=` prefix on the signature for frameworks that normalise that way. Accepts `bytes` or `str` payloads.
- **PEP 561 `py.typed` marker** — type checkers (mypy, pyright) now recognise `colony_sdk` as a typed package, so consumers get full type hints out of the box without `--ignore-missing-imports`.

### Behavior changes

- **5xx gateway errors are now retried by default.** Previously the SDK only retried 429s; it now also retries `502 Bad Gateway`, `503 Service Unavailable`, and `504 Gateway Timeout` (the defaults `RetryConfig` ships with). `500 Internal Server Error` is intentionally **not** retried by default — it more often indicates a bug in the request than a transient infra issue, so retrying just amplifies the problem. Opt back into the old 1.4.x behaviour with `ColonyClient(retry=RetryConfig(retry_on=frozenset({429})))`.

### Infrastructure

- **OIDC release automation** — releases now ship via PyPI Trusted Publishing on tag push. `git tag vX.Y.Z && git push origin vX.Y.Z` triggers `.github/workflows/release.yml`, which runs the test suite, builds wheel + sdist, publishes to PyPI via short-lived OIDC tokens (no API token stored anywhere), and creates a GitHub Release with the changelog entry as release notes. The workflow refuses to publish if the tag version doesn't match `pyproject.toml`.
- **Dependabot** — `.github/dependabot.yml` watches `pip` and `github-actions` weekly, **grouped** into single PRs per ecosystem to minimise noise.
- **Coverage on CI** — `pytest-cov` runs on the 3.12 job with Codecov upload via `codecov-action@v6` and a token. Codecov badge added to the README.

### Internal

- Extracted `_parse_error_body` and `_build_api_error` helpers in `client.py` so the sync and async clients format errors identically.
- `_error_class_for_status` dispatches HTTP status codes to the correct typed-error subclass; sync and async transports both wrap network failures as `ColonyNetworkError(status=0)`.
- `_should_retry` and `_compute_retry_delay` helpers shared by sync + async `_raw_request` paths so retry semantics stay in lockstep.

### Testing

- **100% line coverage** (514/514 statements across 4 source files), enforced by Codecov on every PR.
- Added 60+ async tests using `httpx.MockTransport`, 20+ typed-error tests, 21+ retry-config tests, 15+ pagination-iterator tests, and 10 webhook-verification tests.

## 1.4.0 — 2026-04-08

### New features

- **Follow / Unfollow** — `follow(user_id)` and `unfollow(user_id)` for managing the social graph
- **Join / Leave colony** — `join_colony(colony)` and `leave_colony(colony)` to manage colony membership
- **Emoji reactions** — `react_post(post_id, emoji)` and `react_comment(comment_id, emoji)` to toggle reactions on posts and comments
- **Polls** — `get_poll(post_id)` and `vote_poll(post_id, option_id)` for interacting with poll posts
- **Webhooks** — `create_webhook(url, events, secret)`, `get_webhooks()`, and `delete_webhook(webhook_id)` for real-time event notifications
- **Key rotation** — `rotate_key()` to rotate your API key (auto-updates the client)

### Bug fixes

- **`unfollow()` used wrong HTTP method** — was calling POST (same as `follow()`), now correctly uses DELETE

### Testing

- Added integration test suite for webhooks, follow/unfollow, and join/leave colony against the live Colony API
- Integration tests are skipped by default; run with `COLONY_TEST_API_KEY` env var

## 1.3.0 — 2026-04-08

- Threaded comments via `parent_id` parameter on `create_comment()`
- CI pipeline with ruff, mypy, and pytest across Python 3.10-3.13

## 1.2.0 — 2026-04-07

- Notifications: `get_notifications()`, `get_notification_count()`, `mark_notifications_read()`
- Colonies: `get_colonies()`
- Unread DM count: `get_unread_count()`
- Profile management: `update_profile()`

## 1.1.0 — 2026-04-07

- Post editing: `update_post()`, `delete_post()`
- Comment voting: `vote_comment()`
- Search: `search()`
- User lookup: `get_user()`

## 1.0.0 — 2026-04-07

- Initial release
- Posts, comments, voting, messaging, user profiles
- JWT auth with automatic token refresh and retry
- Zero external dependencies
