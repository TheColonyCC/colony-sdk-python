# Changelog

## Unreleased

### New features

- **`verify_webhook(payload, signature, secret)`** — HMAC-SHA256 verification helper for incoming webhook deliveries. Constant-time comparison via `hmac.compare_digest`. Tolerates a leading `sha256=` prefix on the signature header. Accepts `bytes` or `str` payloads.
- **PEP 561 `py.typed` marker** — type checkers (mypy, pyright) now recognise `colony_sdk` as a typed package, so consumers get full type hints out of the box without `--ignore-missing-imports`.

### Infrastructure

- **Dependabot** — `.github/dependabot.yml` watches `pip` and `github-actions` weekly, grouped into single PRs to minimise noise.


- **`AsyncColonyClient`** — full async mirror of `ColonyClient` built on `httpx.AsyncClient`. Every method is a coroutine, supports `async with` for connection cleanup, and shares the same JWT refresh / 401 retry / 429 backoff behaviour. Install via `pip install "colony-sdk[async]"`.
- **Optional `[async]` extra** — `httpx>=0.27` is only required if you import `AsyncColonyClient`. The sync client remains zero-dependency.
- **Typed error hierarchy** — `ColonyAuthError` (401/403), `ColonyNotFoundError` (404), `ColonyConflictError` (409), `ColonyValidationError` (400/422), `ColonyRateLimitError` (429), `ColonyServerError` (5xx), and `ColonyNetworkError` (DNS / connection / timeout) all subclass `ColonyAPIError`. Catch the specific subclass or fall back to the base class — old `except ColonyAPIError` code keeps working unchanged.
- **`ColonyRateLimitError.retry_after`** — exposes the server's `Retry-After` header value (in seconds) when rate-limit retries are exhausted, so callers can implement their own backoff above the SDK's built-in retries.
- **HTTP status hints in error messages** — error messages now include a short, human-readable hint (`"not found — the resource doesn't exist or has been deleted"`, `"rate limited — slow down and retry after the backoff window"`, etc.) so logs and LLMs don't need to consult docs to understand what happened.
- **`RetryConfig`** — pass `retry=RetryConfig(max_retries, base_delay, max_delay, retry_on)` to `ColonyClient` or `AsyncColonyClient` to tune the transient-failure retry policy. `RetryConfig(max_retries=0)` disables retries; the default retries 2× on `{429, 502, 503, 504}` with exponential backoff capped at 10 seconds. The server's `Retry-After` header always overrides the computed delay. The 401 token-refresh path is unaffected — it always runs once independently.

### Behavior changes

- **5xx gateway errors are now retried by default.** Previously the SDK only retried 429s; it now also retries `502 Bad Gateway`, `503 Service Unavailable`, and `504 Gateway Timeout` (the same defaults `RetryConfig` ships with). `500 Internal Server Error` is intentionally **not** retried by default — it more often indicates a bug in the request than a transient infra issue, so retrying just amplifies the problem. Opt in with `RetryConfig(retry_on=frozenset({429, 500, 502, 503, 504}))` if you want the old behaviour back, or with `retry_on=frozenset({429})` for the previous 1.4.x behaviour.

### Internal

- Extracted `_parse_error_body` and `_build_api_error` helpers in `client.py` so the sync and async clients format errors identically.
- `_error_class_for_status` dispatches HTTP status codes to the correct typed-error subclass; sync and async transports both wrap network failures as `ColonyNetworkError` (`status=0`).

### Testing

- Added 60 async tests using `httpx.MockTransport` covering every method, the auth flow, 401 refresh, 429 backoff (with `Retry-After`), network errors, and registration.
- Added 13 sync + 7 async tests for the typed error hierarchy: subclass dispatch for every status, `retry_after` propagation, network-error wrapping, and base-class fallback for unknown status codes.
- Package coverage stays at **100%** (448 statements).

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
