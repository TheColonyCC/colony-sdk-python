"""Verifying a Colony notarisation record.

The fixture in ``tests/fixtures/live_notarisation_record.json`` is not
hand-written. It is the first real notarisation on the live platform,
fetched from the public endpoint on 2026-09-03, together with the actual
title and body of the post it describes. That provenance is the whole
value of it: these tests prove the SDK's idea of "the canonical bytes"
is byte-identical to the SERVER's, which is a claim no amount of
self-consistent local fixtures can establish. If the two ever drift, a
verifier following our own documentation would compute a mismatch and
correctly conclude our records are bad — so it is worth pinning against
something we did not author.

The record happens to be a Bitcoin price prediction published in January
and notarised in September, which is also the textbook illustration of
what the record does NOT establish: the September submission is
witnessed, the January date is The Colony's word.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from colony_sdk import NotarisationVerification, verify_notarisation
from colony_sdk.notarisation import ASSERTED_BY_THE_PLATFORM, canonical_bytes

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "live_notarisation_record.json").read_text())
LIVE_RECORD: dict = _FIXTURE["record"]
LIVE_TITLE: str = _FIXTURE["title"]
LIVE_BODY: str = _FIXTURE["body"]


@pytest.fixture()
def record() -> dict:
    return copy.deepcopy(LIVE_RECORD)


class TestItAgreesWithTheServer:
    """The claim that cannot be established by a local fixture alone."""

    def test_the_live_record_verifies(self, record: dict) -> None:
        result = verify_notarisation(record)
        assert result.digest_ok, result.reasons
        assert result.ok
        assert bool(result) is True

    def test_our_canonical_bytes_hash_to_the_servers_payload_hash(
        self,
        record: dict,
    ) -> None:
        # Stated separately from the test above because THIS is the
        # cross-implementation claim: our JCS encoder and theirs produce
        # the same bytes for the same document.
        digest = hashlib.sha256(canonical_bytes(record["canonical"])).hexdigest()
        assert digest == record["payload_hash"]

    def test_the_real_body_and_title_match_the_document(
        self,
        record: dict,
    ) -> None:
        result = verify_notarisation(
            record,
            body=LIVE_BODY,
            title=LIVE_TITLE,
        )
        assert result.content_ok is True
        assert result.ok, result.reasons

    def test_canonical_bytes_are_compact_and_sorted(self, record: dict) -> None:
        raw = canonical_bytes(record["canonical"]).decode()
        assert ", " not in raw and '": ' not in raw, "not compact"
        keys = [k for k in record["canonical"]]
        assert list(json.loads(raw).keys()) == sorted(keys), "not key-sorted"


class TestItRefusesWhatItShould:
    def test_a_tampered_document_fails(self, record: dict) -> None:
        record["canonical"]["author_id"] = "00000000-0000-0000-0000-000000000000"
        result = verify_notarisation(record)
        assert not result
        assert result.digest_ok is False
        assert any("payload_hash does not match" in r for r in result.reasons)

    def test_a_tampered_hash_fails(self, record: dict) -> None:
        record["payload_hash"] = "0" * 64
        assert not verify_notarisation(record)

    def test_a_different_body_fails(self, record: dict) -> None:
        result = verify_notarisation(record, body="not what was published")
        assert not result
        assert result.digest_ok is True, "the record itself is still intact"
        assert result.content_ok is False
        assert any("body_sha256" in r for r in result.reasons)

    def test_a_different_title_fails(self, record: dict) -> None:
        result = verify_notarisation(record, title="A different headline")
        assert not result
        assert result.content_ok is False

    def test_whitespace_changes_to_the_body_are_caught(
        self,
        record: dict,
    ) -> None:
        # The hash is over the RAW stored markdown — no normalisation, no
        # trailing-newline rule. A body that has been through a renderer
        # or a .strip() will not match, and that is the correct answer
        # rather than a false alarm worth papering over.
        result = verify_notarisation(record, body=LIVE_BODY + "\n")
        assert result.content_ok is False


class TestItDoesNotOverstateWhatItChecked:
    def test_no_content_supplied_reports_none_not_false(
        self,
        record: dict,
    ) -> None:
        # None and False must not be conflated: one means "not checked",
        # the other means "checked and wrong". A caller branching on
        # falsiness would treat an unchecked record as a failed one.
        result = verify_notarisation(record)
        assert result.content_ok is None
        assert result.ok is True

    def test_it_says_so_when_only_internal_consistency_was_checked(
        self,
        record: dict,
    ) -> None:
        result = verify_notarisation(record)
        assert any("NOT that it describes the content" in n for n in result.notes)

    def test_the_platforms_proof_state_is_reported_not_believed(
        self,
        record: dict,
    ) -> None:
        """The live record says 'anchored'. That is OUR claim about our own
        proof, and must never be an input to the verifier's verdict."""
        assert record["proof_state"] == "anchored"
        record["canonical"]["author_id"] = "11111111-1111-1111-1111-111111111111"
        result = verify_notarisation(record)
        assert result.proof_state == "anchored"
        assert not result.ok, (
            "a record whose digest does not match must fail even while the platform reports it anchored"
        )

    def test_it_says_the_inclusion_proof_was_not_fetched(
        self,
        record: dict,
    ) -> None:
        result = verify_notarisation(record)
        assert any("NOT fetched" in n for n in result.notes)
        assert any("Touchstone" in n for n in result.notes)

    def test_it_names_the_fields_nobody_witnessed(self, record: dict) -> None:
        result = verify_notarisation(record)
        joined = " ".join(result.notes)
        for field in ASSERTED_BY_THE_PLATFORM:
            assert field in joined
        assert "created_at" in joined, (
            "the date is the field most likely to be over-read, so it must be named explicitly"
        )

    def test_a_record_with_no_proof_url_says_so(self, record: dict) -> None:
        record.pop("proof_url", None)
        result = verify_notarisation(record)
        assert any("no proof_url" in n for n in result.notes)


class TestMalformedInputIsNotAFailedVerification:
    def test_a_record_without_canonical_raises(self, record: dict) -> None:
        record.pop("canonical")
        with pytest.raises(ValueError, match="canonical"):
            verify_notarisation(record)

    def test_a_record_without_payload_hash_raises(self, record: dict) -> None:
        record.pop("payload_hash")
        with pytest.raises(ValueError, match="payload_hash"):
            verify_notarisation(record)

    def test_it_does_not_quietly_return_not_ok(self, record: dict) -> None:
        # Returning ok=False for a malformed record would blur "this proof
        # is bad" into "you passed me the wrong thing" — the first is a
        # finding about The Colony, the second is a bug in your code.
        record["canonical"] = "not a mapping"
        with pytest.raises(ValueError):
            verify_notarisation(record)


class TestTheResultType:
    def test_it_is_frozen(self, record: dict) -> None:
        result = verify_notarisation(record)
        assert isinstance(result, NotarisationVerification)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.ok = False  # type: ignore[misc]

    def test_it_is_truthy_only_when_ok(self, record: dict) -> None:
        assert bool(verify_notarisation(record)) is True
        record["payload_hash"] = "0" * 64
        assert bool(verify_notarisation(record)) is False


# ---------------------------------------------------------------------------
# The client methods — wire shape, validation, and both surfaces.
# ---------------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402

import httpx  # noqa: E402
from test_api_methods import _authed_client, _mock_response  # noqa: E402
from test_async_client import _json_response, _make_client  # noqa: E402

from colony_sdk.testing import MockColonyClient  # noqa: E402

POST_ID = "fbd86d55-79f2-4370-911e-9078d1c8161e"
COMMENT_ID = "3a836286-10fe-43c8-9302-f3562e6fc043"

#: Ids that look right and are not. ``_require_uuid`` catches exactly one
#: failure — a UUID truncated for display (``post["id"][:8]`` pasted into a
#: log, then pasted back) — because that is the one shape a server 404
#: cannot distinguish from "deleted". Arbitrary strings like "p1" pass
#: THROUGH by design, so a caller using placeholder ids against a mocked
#: transport is not broken by the check. Asserting the narrow contract it
#: actually has, not the broad one the name suggests.
TRUNCATED_IDS = ["fbd86d55", "fbd86d55-79f2", "fbd86d55-79f2-4370-911e"]


class TestTheWireShape:
    """Pins method + path. The half that was wrong in the platform's own
    published catalogue for the equivalent post call."""

    @patch("colony_sdk.client.urlopen")
    def test_notarise_post_posts_to_the_right_path(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _mock_response(LIVE_RECORD)
        client = _authed_client()
        client.notarise_post(POST_ID)
        req = mock_urlopen.call_args[0][0]
        assert req.get_method() == "POST"
        assert req.full_url.endswith(f"/posts/{POST_ID}/notarise")

    @patch("colony_sdk.client.urlopen")
    def test_notarise_comment_posts_to_the_right_path(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _mock_response(LIVE_RECORD)
        client = _authed_client()
        client.notarise_comment(COMMENT_ID)
        req = mock_urlopen.call_args[0][0]
        assert req.get_method() == "POST"
        assert req.full_url.endswith(f"/comments/{COMMENT_ID}/notarise")

    @patch("colony_sdk.client.urlopen")
    def test_reads_use_get_and_the_notarisation_path(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _mock_response(LIVE_RECORD)
        client = _authed_client()
        client.get_post_notarisation(POST_ID)
        req = mock_urlopen.call_args[0][0]
        assert req.get_method() == "GET"
        assert req.full_url.endswith(f"/posts/{POST_ID}/notarisation")

        client.get_comment_notarisation(COMMENT_ID)
        req = mock_urlopen.call_args[0][0]
        assert req.get_method() == "GET"
        assert req.full_url.endswith(f"/comments/{COMMENT_ID}/notarisation")

    @patch("colony_sdk.client.urlopen")
    def test_the_read_is_distinguishable_from_the_write(
        self,
        mock_urlopen,
    ) -> None:
        # ``/notarise`` and ``/notarisation`` differ by three characters and
        # one of them is irreversible. Worth an explicit assertion that the
        # read never POSTs.
        mock_urlopen.return_value = _mock_response(LIVE_RECORD)
        client = _authed_client()
        client.get_post_notarisation(POST_ID)
        req = mock_urlopen.call_args[0][0]
        assert req.get_method() == "GET"
        assert "/notarise" not in req.full_url.rsplit("/", 1)[-1]


class TestIdValidation:
    @pytest.mark.parametrize("bad", TRUNCATED_IDS)
    def test_a_truncated_post_id_never_reaches_the_network(self, bad) -> None:
        client = _authed_client()
        with patch("colony_sdk.client.urlopen") as mock_urlopen:
            with pytest.raises(ValueError, match="truncated"):
                client.notarise_post(bad)
            assert mock_urlopen.call_count == 0, (
                "an IRREVERSIBLE call must not reach the network on an id the SDK can already tell is malformed"
            )

    @pytest.mark.parametrize("bad", TRUNCATED_IDS)
    def test_a_truncated_comment_id_is_refused(self, bad) -> None:
        with pytest.raises(ValueError, match="truncated"):
            _authed_client().notarise_comment(bad)

    @pytest.mark.parametrize("bad", TRUNCATED_IDS)
    def test_the_reads_validate_too(self, bad) -> None:
        client = _authed_client()
        with pytest.raises(ValueError, match="truncated"):
            client.get_post_notarisation(bad)
        with pytest.raises(ValueError, match="truncated"):
            client.get_comment_notarisation(bad)

    @patch("colony_sdk.client.urlopen")
    def test_an_opaque_id_still_reaches_the_server(self, mock_urlopen) -> None:
        """The check is narrow ON PURPOSE and this pins that.

        Widening it to "anything that is not a UUID" would break every
        caller using placeholder ids against a mocked transport, which is
        why ``_require_uuid``'s docstring rules it out. A local check can
        say an id is malformed; only the server can say it is unreal.
        """
        mock_urlopen.return_value = _mock_response(LIVE_RECORD)
        _authed_client().get_post_notarisation("my-fixture-post")
        assert mock_urlopen.call_count == 1


class TestAsyncParity:
    @pytest.mark.asyncio
    async def test_async_notarise_hits_the_same_path(self) -> None:
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            return _json_response(LIVE_RECORD)

        client = _make_client(handler)
        await client.notarise_post(POST_ID)
        assert seen == [("POST", f"/api/v1/posts/{POST_ID}/notarise")]

    @pytest.mark.asyncio
    async def test_async_read_hits_the_same_path(self) -> None:
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            return _json_response(LIVE_RECORD)

        client = _make_client(handler)
        await client.get_comment_notarisation(COMMENT_ID)
        assert seen == [("GET", f"/api/v1/comments/{COMMENT_ID}/notarisation")]

    def test_async_verify_is_not_a_coroutine(self) -> None:
        """It does no I/O, so it is a plain def on BOTH clients.

        Asserted rather than left to convention: making it awaitable to
        match its neighbours would misrepresent a local hash comparison as
        a network call, and changing it later would break every caller who
        wrote it without ``await``.
        """
        import inspect

        from colony_sdk.async_client import AsyncColonyClient

        assert not inspect.iscoroutinefunction(AsyncColonyClient.verify_notarisation)
        client = _make_client(lambda r: _json_response({}))
        assert client.verify_notarisation(LIVE_RECORD).digest_ok


class TestTheMock:
    def test_the_reads_and_writes_are_canned(self) -> None:
        mock = MockColonyClient(responses={"notarise_post": LIVE_RECORD})
        assert mock.notarise_post(POST_ID)["payload_hash"] == (LIVE_RECORD["payload_hash"])

    def test_verify_is_real_in_the_mock_too(self) -> None:
        """NOT canned, deliberately.

        A stubbed verifier that always agrees turns "does my code reject a
        tampered record" into a test of nothing. The real client does no
        I/O here, so there is nothing to fake.
        """
        mock = MockColonyClient()
        assert mock.verify_notarisation(LIVE_RECORD).ok

        tampered = copy.deepcopy(LIVE_RECORD)
        tampered["payload_hash"] = "0" * 64
        assert not mock.verify_notarisation(tampered).ok
