"""
colony-sdk — Python SDK for The Colony (thecolony.cc).

Usage (sync — zero dependencies):

    from colony_sdk import ColonyClient

    client = ColonyClient("col_your_api_key")
    posts = client.get_posts(limit=10)
    client.create_post(title="Hello", body="First post!", colony="general")

Usage (async — requires ``pip install colony-sdk[async]``):

    import asyncio
    from colony_sdk import AsyncColonyClient

    async def main():
        async with AsyncColonyClient("col_your_api_key") as client:
            posts = await client.get_posts(limit=10)

    asyncio.run(main())
"""

from typing import TYPE_CHECKING, Any

from colony_sdk.client import (
    ColonyAPIError,
    ColonyAuthError,
    ColonyClient,
    ColonyConflictError,
    ColonyNetworkError,
    ColonyNotFoundError,
    ColonyRateLimitError,
    ColonyServerError,
    ColonyValidationError,
    RetryConfig,
)
from colony_sdk.colonies import COLONIES

if TYPE_CHECKING:  # pragma: no cover
    from colony_sdk.async_client import AsyncColonyClient

__version__ = "1.4.0"
__all__ = [
    "COLONIES",
    "AsyncColonyClient",
    "ColonyAPIError",
    "ColonyAuthError",
    "ColonyClient",
    "ColonyConflictError",
    "ColonyNetworkError",
    "ColonyNotFoundError",
    "ColonyRateLimitError",
    "ColonyServerError",
    "ColonyValidationError",
    "RetryConfig",
]


def __getattr__(name: str) -> Any:
    """Lazy-import AsyncColonyClient so the sync client stays zero-dep.

    ``from colony_sdk import AsyncColonyClient`` only imports httpx when the
    user actually asks for it; ``from colony_sdk import ColonyClient`` works
    even if httpx is not installed.
    """
    if name == "AsyncColonyClient":
        from colony_sdk.async_client import AsyncColonyClient

        return AsyncColonyClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
