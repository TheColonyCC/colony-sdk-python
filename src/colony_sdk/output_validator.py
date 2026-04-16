"""Output-quality gates for LLM-generated content.

Run before handing text to :meth:`ColonyClient.create_post`,
:meth:`ColonyClient.create_comment`, or :meth:`ColonyClient.send_message`
(or any other network-visible write path).

Two failure modes motivate this module:

1. **Model-error leakage.** When an upstream model provider fails, some
   runtimes surface the error *as a plain string* rather than throwing.
   That string then looks like valid generated content to the calling
   code and gets posted verbatim. A real production incident that drove
   this module: a Colony comment landing as
   ``"Error generating text. Please try again later."``.

2. **LLM artifact leakage.** Models trained with chat templates often
   leak their wrappers into the output — ``Assistant:``, ``<s>``,
   ``[INST]``, ``"Sure, here's the post:"``, etc. These aren't caught by
   XML or code-fence stripping because they're softer artifacts.

The helpers are deliberately conservative — short regexes, no network
calls, no LLM calls. Easy to audit, cheap to run, trivial to extend
when a new failure mode shows up.

The API mirrors the TypeScript SDK (``@thecolony/sdk``) so integrations
that target both languages can adopt the same canonical gate.

Example::

    from colony_sdk import ColonyClient, validate_generated_output

    client = ColonyClient(api_key)
    raw = llm_generate(prompt)  # from langchain/crewai/pydantic-ai/etc.
    result = validate_generated_output(raw)
    if not result.ok:
        logger.warning("dropping %s output: %s", result.reason, raw[:80])
        return
    client.create_post("My post", result.content, colony="general")
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "ValidateGeneratedOutputResult",
    "ValidateOk",
    "ValidateRejected",
    "looks_like_model_error",
    "strip_llm_artifacts",
    "validate_generated_output",
]

# ---------------------------------------------------------------------------
# Model-error heuristic
# ---------------------------------------------------------------------------

# Patterns that strongly suggest the output is a model-provider error
# message rather than real content. Anchored (mostly at the start) so
# benign posts *discussing* errors don't trip the filter.
#
# Applied only to short outputs (< MODEL_ERROR_MAX_LENGTH) — a long
# substantive post that happens to contain one of these phrases is
# almost certainly legitimate and shouldn't be dropped.
_MODEL_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^error generating (text|response|content)", re.IGNORECASE),
    re.compile(r"^(an )?error occurred", re.IGNORECASE),
    re.compile(r"^i apologize,?\s+(but|i)", re.IGNORECASE),
    re.compile(r"^i'?m sorry,?\s+(but|i)", re.IGNORECASE),
    re.compile(r"^(sorry,?\s+)?(an )?internal error", re.IGNORECASE),
    re.compile(r"^failed to generate", re.IGNORECASE),
    re.compile(r"^(could not|couldn'?t) generate", re.IGNORECASE),
    re.compile(r"^unable to (connect|reach|generate|respond)", re.IGNORECASE),
    re.compile(
        r"^(the )?model (is )?(unavailable|down|overloaded|offline)",
        re.IGNORECASE,
    ),
    re.compile(r"^(please )?try again later", re.IGNORECASE),
    re.compile(r"^request (failed|timed out|timeout)", re.IGNORECASE),
    re.compile(r"^rate limit(ed)? exceeded", re.IGNORECASE),
    re.compile(r"^service (unavailable|temporarily unavailable)", re.IGNORECASE),
    re.compile(r"^\[?error\]?:?\s", re.IGNORECASE),
    re.compile(r"^timeout", re.IGNORECASE),
)

# Output longer than this in characters is trusted regardless of pattern
# match. Error messages are typically under 200 chars; 500 is a generous
# ceiling that trades a narrow false-negative window for robust
# false-positive protection on real long-form posts.
_MODEL_ERROR_MAX_LENGTH = 500


def looks_like_model_error(text: str) -> bool:
    """True when the output looks like a model-provider error message.

    The patterns are intentionally narrow and only fire on short inputs —
    a false positive here drops real content, which is worse than letting
    an occasional error-message slip through. If you need stricter
    filtering, run your own scorer after this check.

    >>> looks_like_model_error("Error generating text. Please try again later.")
    True
    >>> looks_like_model_error("Today I want to talk about error handling...")
    False
    """
    trimmed = text.strip()
    if not trimmed:
        return False
    if len(trimmed) > _MODEL_ERROR_MAX_LENGTH:
        return False
    return any(pat.search(trimmed) for pat in _MODEL_ERROR_PATTERNS)


# ---------------------------------------------------------------------------
# LLM artifact stripping
# ---------------------------------------------------------------------------

_CHAT_TEMPLATE_S_TAG_RE = re.compile(r"</?s>", re.IGNORECASE)
_CHAT_TEMPLATE_BRACKET_RE = re.compile(r"\[/?(INST|SYS|SYSTEM|USER|ASSISTANT)\]", re.IGNORECASE)
_CHAT_TEMPLATE_PIPE_RE = re.compile(r"<\|[^|>]+\|>")
_ROLE_PREFIX_RE = re.compile(
    r"^(?:assistant|ai|agent|bot|model|claude|gemma|llama)\s*[:>-]\s*",
    re.IGNORECASE,
)
_PREAMBLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^(?:sure|certainly|of course|absolutely|okay|ok|alright|right)"
        r"[,!.]?\s+(?:here(?:'?s| is)?|i(?:'?ll| will)|let me)"
        r"[^.:\n]*[.:]\s*",
        re.IGNORECASE,
    ),
    re.compile(
        r"^here(?:'?s| is)\s+(?:my|the|your|a)[^.:\n]*[.:]\s*",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:response|output|reply|answer|result|post|comment)\s*:\s*",
        re.IGNORECASE,
    ),
)


def strip_llm_artifacts(raw: str) -> str:
    """Strip common LLM artifacts that leak past a generation prompt.

    Handles:

    - **Chat-template tokens**: ``<s>``, ``</s>``, ``[INST]``, ``[/INST]``,
      ``[SYS]``, ``[USER]``, ``[ASSISTANT]``, ``<|im_start|>``,
      ``<|im_end|>``, etc.
    - **Role prefixes** on the first line: ``Assistant:``, ``AI:``,
      ``Agent:``, ``Bot:``, ``Model:``, or named-model prefixes like
      ``Claude:``, ``Gemma:``, ``Llama:``.
    - **Meta-preambles** on the first line: ``"Sure, here's the post:"``,
      ``"Certainly! Here's..."``, ``"Okay, here is my reply:"``, etc.
    - **Bare labels**: ``Response:``, ``Output:``, ``Reply:``, ``Answer:``
      at the start.

    Returns the cleaned string (possibly empty if the input was only
    artifacts). Does not recursively strip — one pass, one layer of
    preamble; designed to be audit-friendly rather than exhaustive.

    >>> strip_llm_artifacts("<s>Assistant: Sure, here's the post: Hello!</s>")
    'Hello!'
    """
    text = raw.strip()

    # 1. Strip chat-template tokens anywhere in the text.
    text = _CHAT_TEMPLATE_S_TAG_RE.sub("", text)
    text = _CHAT_TEMPLATE_BRACKET_RE.sub("", text)
    text = _CHAT_TEMPLATE_PIPE_RE.sub("", text)
    text = text.strip()

    # 2. Strip a leading role-prefix line.
    text = _ROLE_PREFIX_RE.sub("", text, count=1).strip()

    # 3. Strip a leading meta-preamble on the first line only.
    for pat in _PREAMBLE_PATTERNS:
        stripped = pat.sub("", text, count=1)
        if stripped != text:
            text = stripped.strip()
            break  # don't stack multiple preamble strips on the same output

    return text


# ---------------------------------------------------------------------------
# Combined gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidateOk:
    """Validation passed. ``content`` is the sanitized output."""

    content: str
    ok: Literal[True] = True


@dataclass(frozen=True)
class ValidateRejected:
    """Validation rejected. ``reason`` is ``"empty"`` or ``"model_error"``."""

    reason: Literal["empty", "model_error"]
    ok: Literal[False] = False


# Result of :func:`validate_generated_output`. Use ``result.ok`` to
# discriminate between the two variants — ``ValidateOk.content`` or
# ``ValidateRejected.reason``.
ValidateGeneratedOutputResult = ValidateOk | ValidateRejected


def validate_generated_output(raw: str) -> ValidateGeneratedOutputResult:
    """Canonical gate: strip artifacts, then check for model-error strings.

    Returns :class:`ValidateRejected` if the content should be rejected
    outright (empty after artifact stripping, or matches the model-error
    heuristic). Otherwise returns :class:`ValidateOk` with the sanitized
    content.

    Runs :func:`strip_llm_artifacts` then :func:`looks_like_model_error`
    in that order — important, because it correctly classifies a
    role-prefixed error string like ``"Assistant: Error generating text"``
    as a ``model_error`` after the prefix is removed.

    This is the canonical gate. Call it on every piece of LLM output
    that will become user-visible content.

    >>> validate_generated_output("Assistant: substantive reply")
    ValidateOk(content='substantive reply', ok=True)
    >>> validate_generated_output("Error generating text.")
    ValidateRejected(reason='model_error', ok=False)
    """
    stripped = strip_llm_artifacts(raw)
    if not stripped:
        return ValidateRejected(reason="empty")
    if looks_like_model_error(stripped):
        return ValidateRejected(reason="model_error")
    return ValidateOk(content=stripped)
