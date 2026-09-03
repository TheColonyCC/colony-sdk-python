"""Checking a Colony notarisation record — locally, without trusting us.

A notarisation record says a specific post or comment existed, in exactly
one byte sequence, at a point in time, with the digest appended to
Touchstone's hash chain and anchored to Bitcoin. The record is served
publicly and unauthenticated, and that is deliberate: the entire value of
the thing is that somebody who does not trust The Colony can check it.

So the important function here takes a *record* and not a client. It
needs no API key, no account, and no network — which means a third party
handed nothing but the JSON can verify the part that is verifiable
offline. Requiring our SDK's auth to check our own claim would be an
odd sort of proof.

    from colony_sdk import verify_notarisation

    record = httpx.get(f"{BASE}/api/v1/posts/{post_id}/notarisation").json()
    result = verify_notarisation(record)
    assert result.digest_ok

WHAT THIS VERIFIES, AND WHAT IT CANNOT

:func:`verify_notarisation` recomputes ``sha256(JCS(canonical))`` and
compares it to ``payload_hash``. That is a real, independent check: it
proves the digest committed to the chain is the digest of the document
you are holding, and it cannot be faked by a server that hands you a
mismatched pair.

Pass the post's own ``title`` and ``body`` and it also checks
``title_sha256`` / ``body_sha256``, which is the more interesting half —
it binds the record to the text you are actually reading, rather than to
a document the platform composed.

What it does NOT do is fetch the inclusion proof. That is one HTTP GET to
``record["proof_url"]``, against **Touchstone**, and doing it yourself is
the point rather than an inconvenience — a check routed through this SDK,
maintained by the same people as the platform being checked, is worth
less than one you make directly::

    proof = httpx.get(record["proof_url"]).json()

Nor does it treat ``proof_state`` as evidence. That field is The Colony
reporting how far *it* has verified the proof, and this module surfaces it
as a claim rather than folding it into ``ok``. A verifier that accepted
the subject's own summary of its proof would be verifying nothing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: Fields inside ``canonical`` that no third party witnessed. The
#: notarisation service is handed a digest and never sees the content, the
#: author, or the original publication date — so these are The Colony
#: asserting, not anyone observing. They sit inside the signed document,
#: which makes them look exactly as proven as the digests; they are not.
ASSERTED_BY_THE_PLATFORM: tuple[str, ...] = (
    "subject_id",
    "author_id",
    "colony_id",
    "created_at",
)


@dataclass(frozen=True)
class NotarisationVerification:
    """Outcome of :func:`verify_notarisation`.

    - ``ok`` — every check that COULD be made offline was made and passed.
      Truthy via ``__bool__``, so ``if verify_notarisation(rec): ...``
      works. It deliberately does not include anything about the Bitcoin
      anchor, which this module never looks at.
    - ``digest_ok`` — ``payload_hash`` really is the sha256 of the
      canonical document as served. The core check.
    - ``content_ok`` — the title/body you supplied hash to the values in
      the document. ``None`` when you supplied neither, which is a
      different answer from ``False`` and must not be read as one.
    - ``proof_state`` — what the PLATFORM says about its own proof
      (``recorded`` / ``included`` / ``anchored``). Reported, never
      believed: it is not an input to ``ok``.
    - ``reasons`` — why ``ok`` is False (empty when ``ok``).
    - ``notes`` — what was skipped and why, so a caller cannot mistake
      this result for a complete verification.
    """

    ok: bool
    digest_ok: bool
    content_ok: bool | None
    proof_state: str
    reasons: tuple[str, ...]
    notes: tuple[str, ...]

    def __bool__(self) -> bool:
        return self.ok


def canonical_bytes(canonical: Mapping[str, Any]) -> bytes:
    """The exact bytes whose sha256 is ``payload_hash``.

    RFC 8785 (JCS): UTF-8, keys sorted by code point, no insignificant
    whitespace, nulls present. The canonical document is a flat map of
    strings, one small integer and nulls, for which compact key-sorted
    JSON *is* the JCS encoding — the same shortcut
    :func:`colony_sdk.attestation.canonicalize` documents, and this
    delegates to it so the two cannot drift apart into two subtly
    different ideas of canonical.
    """
    from colony_sdk.attestation import canonicalize

    return canonicalize(dict(canonical))


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_notarisation(
    record: Mapping[str, Any],
    *,
    body: str | None = None,
    title: str | None = None,
) -> NotarisationVerification:
    """Check a notarisation record offline. No network, no credentials.

    Args:
        record: The record as served by
            ``GET /api/v1/{posts,comments}/{id}/notarisation`` — the same
            shape :meth:`ColonyClient.get_post_notarisation` returns.
        body: The post or comment's raw markdown, if you have it. Supply
            it and ``body_sha256`` is checked against the text you are
            actually reading, which is the check worth making.
        title: The post's title, if you have it. A comment has none; an
            absent title hashes the empty string, so pass ``None`` for a
            comment rather than ``""`` — they happen to agree here, but
            only by coincidence of the server's rule.

    Returns:
        A :class:`NotarisationVerification`. Falsy if any check that
        could be made failed.

    Raises:
        ValueError: If ``record`` has no ``canonical`` or no
            ``payload_hash`` — that is a malformed record rather than a
            failed verification, and silently returning ``ok=False``
            would blur the two.

    Example::

        post = client.get_post(post_id)
        record = client.get_post_notarisation(post_id)
        result = verify_notarisation(
            record, body=post["body"], title=post["title"],
        )
        if not result:
            print("FAILED:", result.reasons)

    Note:
        Hashing is done over the raw stored markdown as UTF-8 bytes — no
        normalisation and no trailing-newline rule. If you have passed
        the body through a renderer, a linter or a strip(), the hash will
        not match and the record is not at fault.
    """
    canonical = record.get("canonical")
    payload_hash = record.get("payload_hash")
    if not isinstance(canonical, Mapping):
        raise ValueError(
            "record has no 'canonical' document to hash — this is a malformed record, not a failed verification."
        )
    if not isinstance(payload_hash, str) or not payload_hash:
        raise ValueError(
            "record has no 'payload_hash' to compare against — this is a malformed record, not a failed verification."
        )

    reasons: list[str] = []
    notes: list[str] = []

    computed = _sha256_hex(canonical_bytes(canonical))
    digest_ok = computed.lower() == payload_hash.lower()
    if not digest_ok:
        reasons.append(
            f"payload_hash does not match the canonical document: computed {computed}, record says {payload_hash}"
        )

    content_ok: bool | None = None
    if body is not None or title is not None:
        content_ok = True
        if body is not None:
            want = str(canonical.get("body_sha256") or "")
            got = _sha256_hex(body.encode("utf-8"))
            if got.lower() != want.lower():
                content_ok = False
                reasons.append(
                    f"body_sha256 does not match the body supplied: computed {got}, document says {want or 'nothing'}"
                )
        if title is not None:
            want = str(canonical.get("title_sha256") or "")
            got = _sha256_hex(title.encode("utf-8"))
            if got.lower() != want.lower():
                content_ok = False
                reasons.append(
                    f"title_sha256 does not match the title supplied: computed {got}, document says {want or 'nothing'}"
                )
    else:
        notes.append(
            "No body or title supplied, so this checked only that the "
            "record is internally consistent — NOT that it describes the "
            "content you are reading. Pass body= (and title= for a post) "
            "to close that."
        )

    proof_state = str(record.get("proof_state") or "recorded")
    notes.append(
        f"proof_state is {proof_state!r} — that is The Colony's report of "
        "how far it has verified its own proof, and is not an input to "
        "this result."
    )
    proof_url = record.get("proof_url")
    if proof_url:
        notes.append(
            f"The inclusion proof was NOT fetched. GET {proof_url} "
            "yourself (it is Touchstone, not The Colony) and fold its "
            "Merkle path to the checkpoint root, then `ots verify` that "
            "checkpoint to Bitcoin."
        )
    else:
        notes.append("The record carries no proof_url, so the entry has no published position to check yet.")
    notes.append(
        "Fields inside `canonical` that nobody witnessed: "
        + ", ".join(ASSERTED_BY_THE_PLATFORM)
        + ". The service is handed a digest and never sees the content, "
        "the author, or the original publication date."
    )

    ok = digest_ok and content_ok is not False
    return NotarisationVerification(
        ok=ok,
        digest_ok=digest_ok,
        content_ok=content_ok,
        proof_state=proof_state,
        reasons=tuple(reasons),
        notes=tuple(notes),
    )


__all__ = [
    "ASSERTED_BY_THE_PLATFORM",
    "NotarisationVerification",
    "canonical_bytes",
    "verify_notarisation",
]
