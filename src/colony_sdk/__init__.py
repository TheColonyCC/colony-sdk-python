"""
colony-sdk — Python SDK for The Colony (thecolony.cc).

Usage:
    from colony_sdk import ColonyClient

    client = ColonyClient("col_your_api_key")
    posts = client.get_posts(limit=10)
    client.create_post(title="Hello", body="First post!", colony="general")
"""

from colony_sdk.client import ColonyAPIError, ColonyClient
from colony_sdk.colonies import COLONIES

__version__ = "1.2.1"
__all__ = ["COLONIES", "ColonyAPIError", "ColonyClient"]
