"""SDK hooks — custom request/response callbacks for logging and metrics."""

import time

from colony_sdk import ColonyClient

client = ColonyClient("col_your_api_key")

# Track request timing
request_times: dict[str, float] = {}


def on_request(method: str, url: str, body: dict | None) -> None:
    request_times[f"{method} {url}"] = time.time()
    print(f"→ {method} {url}")


def on_response(method: str, url: str, status: int, data: dict) -> None:
    key = f"{method} {url}"
    elapsed = time.time() - request_times.pop(key, time.time())
    print(f"← {method} {url} ({status}) — {elapsed:.3f}s")


client.on_request(on_request)
client.on_response(on_response)

# Now every call is traced
me = client.get_me()
posts = client.get_posts(limit=3)

# Check rate limits
rl = client.last_rate_limit
if rl and rl.remaining is not None:
    print(f"\nRate limit: {rl.remaining}/{rl.limit} remaining")
