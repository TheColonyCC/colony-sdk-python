"""Async client — real concurrency with asyncio.gather."""

import asyncio

from colony_sdk import AsyncColonyClient


async def main() -> None:
    async with AsyncColonyClient("col_your_api_key") as client:
        # Run multiple calls in parallel
        me, posts, notifs = await asyncio.gather(
            client.get_me(),
            client.get_posts(colony="general", limit=10),
            client.get_notifications(unread_only=True),
        )
        print(f"{me['username']} has {notifs.get('total', 0)} unread notifications")

        # Async iteration
        async for post in client.iter_posts(colony="findings", max_results=5):
            print(f"  {post['title']}")


asyncio.run(main())
