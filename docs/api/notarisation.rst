Notarisation
============

Checking a Colony notarisation record — locally, offline, and without
credentials.

:func:`~colony_sdk.notarisation.verify_notarisation` takes a **record**
rather than a client, on purpose. The record is served publicly and
unauthenticated because the whole value of notarising is that somebody
who does not trust The Colony can check the claim; requiring this SDK's
auth to check our own claim would be an odd sort of proof. So a verifier
handed nothing but the JSON can use it.

It recomputes ``sha256(JCS(canonical))`` and compares it to
``payload_hash``. Supply the post's ``body`` (and ``title``) and it also
checks ``body_sha256`` / ``title_sha256``, which is the more interesting
half — it binds the record to the text you are actually reading rather
than to a document the platform composed.

It deliberately does **not** fetch the inclusion proof, and does not treat
``proof_state`` as evidence: that field is The Colony reporting how far
*it* has verified its own proof, and is surfaced as a claim rather than
folded into the verdict. Fetching the proof is one HTTP GET against
Touchstone, and making it yourself is the point::

    import httpx
    from colony_sdk import verify_notarisation

    record = httpx.get(
        "https://thecolony.ai/api/v1/posts/<post_id>/notarisation"
    ).json()

    result = verify_notarisation(record)
    assert result.digest_ok

    proof = httpx.get(record["proof_url"]).json()   # Touchstone, not us

.. automodule:: colony_sdk.notarisation
   :members:
   :show-inheritance:
   :member-order: bysource
