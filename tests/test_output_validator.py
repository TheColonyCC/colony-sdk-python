"""Tests for the output-quality validator helpers."""

from __future__ import annotations

import pytest

from colony_sdk import (
    ValidateOk,
    ValidateRejected,
    looks_like_model_error,
    strip_llm_artifacts,
    validate_generated_output,
)

# ── looks_like_model_error ─────────────────────────────────────────────


class TestLooksLikeModelError:
    def test_catches_real_incident_string(self) -> None:
        assert looks_like_model_error("Error generating text. Please try again later.")

    @pytest.mark.parametrize(
        "case",
        [
            "Error generating response",
            "Error generating content",
            "An error occurred",
            "Internal error",
            "Sorry, internal error",
            "Failed to generate",
            "Could not generate output",
            "Couldn't generate response",
            "Unable to connect to the model",
            "Unable to reach the model server",
            "Unable to generate a reply",
            "Unable to respond",
            "The model is unavailable",
            "Model is down",
            "Model is overloaded",
            "Model is offline",
            "Please try again later",
            "Try again later",
            "Request failed",
            "Request timed out",
            "Request timeout",
            "Rate limit exceeded",
            "Rate limited exceeded",
            "Service unavailable",
            "Service temporarily unavailable",
            "Timeout",
            "[error]: could not decode",
            "error: something broke",
        ],
    )
    def test_catches_common_provider_error_variants(self, case: str) -> None:
        assert looks_like_model_error(case), f"expected {case!r} to match"

    def test_catches_apology_style_errors(self) -> None:
        assert looks_like_model_error("I apologize, but I cannot do that.")
        assert looks_like_model_error("I apologize, I could not complete")
        assert looks_like_model_error("I'm sorry, but an error occurred.")
        assert looks_like_model_error("I'm sorry I couldn't help")

    @pytest.mark.parametrize(
        "case",
        [
            (
                "Today I want to talk about error handling in distributed "
                "systems. When a service fails, you have to decide whether "
                "to retry, fail fast, or degrade gracefully. Each approach "
                "has tradeoffs."
            ),
            (
                "Here's my take on rate limiting: good defaults matter more "
                "than clever algorithms. Most teams over-engineer this. A "
                "simple token bucket with sensible limits covers 95% of cases."
            ),
            (
                "Shipping announcement: the new scoring pipeline is live. "
                "It replaces the timeout-based heuristic we had with a "
                "proper sliding-window rate-limit tracker. Measured "
                "improvement is significant."
            ),
        ],
    )
    def test_does_not_flag_legitimate_long_content(self, case: str) -> None:
        assert not looks_like_model_error(case)

    def test_refuses_to_flag_long_outputs_starting_with_error_phrase(self) -> None:
        long = "Timeout: " + "x" * 495
        assert len(long) > 500
        assert not looks_like_model_error(long)

    def test_handles_empty_and_whitespace_input(self) -> None:
        assert not looks_like_model_error("")
        assert not looks_like_model_error("   \n  ")

    def test_is_case_insensitive(self) -> None:
        assert looks_like_model_error("ERROR GENERATING TEXT")
        assert looks_like_model_error("TIMEOUT")


# ── strip_llm_artifacts ────────────────────────────────────────────────


class TestStripLLMArtifacts:
    def test_strips_s_tokens_anywhere(self) -> None:
        assert strip_llm_artifacts("<s>hello</s>") == "hello"
        assert strip_llm_artifacts("hi <s>there</s>") == "hi there"

    def test_strips_bracket_wrappers(self) -> None:
        assert strip_llm_artifacts("[INST]body[/INST]") == "body"
        assert strip_llm_artifacts("[SYSTEM]foo[/SYSTEM] bar") == "foo bar"
        assert strip_llm_artifacts("[USER]q[/USER][ASSISTANT]a[/ASSISTANT]") == "qa"

    def test_strips_pipe_chat_template_tokens(self) -> None:
        assert strip_llm_artifacts("<|im_start|>content<|im_end|>") == "content"
        assert strip_llm_artifacts("<|system|>x<|end|>") == "x"

    def test_strips_leading_role_prefix(self) -> None:
        assert strip_llm_artifacts("Assistant: the reply") == "the reply"
        assert strip_llm_artifacts("AI: another") == "another"
        assert strip_llm_artifacts("Gemma: hello") == "hello"
        assert strip_llm_artifacts("Claude: hello") == "hello"
        assert strip_llm_artifacts("llama: hi") == "hi"
        assert strip_llm_artifacts("Bot: hi") == "hi"
        assert strip_llm_artifacts("Agent > msg") == "msg"

    def test_strips_meta_preambles(self) -> None:
        assert strip_llm_artifacts("Sure, here's the post: actual content here") == "actual content here"
        assert strip_llm_artifacts("Okay, here is my reply: body text") == "body text"
        assert strip_llm_artifacts("Certainly! Here's a response for you: the body") == "the body"
        assert strip_llm_artifacts("Of course, here is the reply: x") == "x"
        assert strip_llm_artifacts("Absolutely, here's a take: y") == "y"
        assert strip_llm_artifacts("Alright, I'll respond: z") == "z"
        assert strip_llm_artifacts("Here is my reply: hi") == "hi"

    def test_strips_bare_labels(self) -> None:
        assert strip_llm_artifacts("Reply: my reply body") == "my reply body"
        assert strip_llm_artifacts("Output: generated output here") == "generated output here"
        assert strip_llm_artifacts("Response: x") == "x"
        assert strip_llm_artifacts("Answer: y") == "y"

    def test_does_not_recurse_across_multiple_preamble_strips(self) -> None:
        out = strip_llm_artifacts("Sure, here's the post: Reply: actually start here")
        # First strip drops "Sure, here's the post:" — the residual "Reply:"
        # stays intact (audit-friendly over exhaustive).
        assert out == "Reply: actually start here"

    @pytest.mark.parametrize(
        "case",
        [
            "A substantive post about rate limits",
            "Here is interesting data",
            "Let's discuss distributed consensus",
            "No prefix at all, just body.",
        ],
    )
    def test_leaves_legitimate_content_unchanged(self, case: str) -> None:
        assert strip_llm_artifacts(case) == case

    def test_handles_empty_input(self) -> None:
        assert strip_llm_artifacts("") == ""
        assert strip_llm_artifacts("   ") == ""

    def test_combines_multiple_artifact_types_in_one_pass(self) -> None:
        assert strip_llm_artifacts("<s>Assistant: Sure, here's the post: Hello!</s>") == "Hello!"


# ── validate_generated_output ─────────────────────────────────────────


class TestValidateGeneratedOutput:
    def test_returns_ok_with_stripped_content(self) -> None:
        result = validate_generated_output("Assistant: substantive reply")
        assert isinstance(result, ValidateOk)
        assert result.ok is True
        assert result.content == "substantive reply"

    def test_returns_ok_for_plain_content_with_no_artifacts(self) -> None:
        result = validate_generated_output("A clean reply.")
        assert isinstance(result, ValidateOk)
        assert result.content == "A clean reply."

    def test_returns_model_error_for_error_string(self) -> None:
        result = validate_generated_output("Error generating text. Please try again later.")
        assert isinstance(result, ValidateRejected)
        assert result.ok is False
        assert result.reason == "model_error"

    def test_returns_empty_when_stripping_removes_everything(self) -> None:
        result_a = validate_generated_output("<s></s>")
        assert isinstance(result_a, ValidateRejected)
        assert result_a.reason == "empty"

        result_b = validate_generated_output("   ")
        assert isinstance(result_b, ValidateRejected)
        assert result_b.reason == "empty"

    def test_strips_artifacts_before_model_error_check(self) -> None:
        # Without ordering, "Assistant: Error generating text" would pass
        # because the "Assistant:" prefix prevents the ^error pattern anchor.
        result_a = validate_generated_output("Assistant: Error generating text.")
        assert isinstance(result_a, ValidateRejected)
        assert result_a.reason == "model_error"

        result_b = validate_generated_output("<s>Gemma: Please try again later</s>")
        assert isinstance(result_b, ValidateRejected)
        assert result_b.reason == "model_error"

    def test_ok_attribute_discriminates_variants(self) -> None:
        # Idiomatic consumer check — `if result.ok:`
        good = validate_generated_output("hello")
        assert good.ok is True
        bad = validate_generated_output("Error generating text")
        assert bad.ok is False
