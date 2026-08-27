from __future__ import annotations

from learning_authoring.provider import normalized_base_url, normalized_model


def test_official_api_strips_gateway_model_prefix() -> None:
    assert normalized_model("openai/gpt-5.6-sol", None) == "gpt-5.6-sol"
    assert normalized_model("openai/gpt-5.6-sol", "https://api.openai.com/v1/") == "gpt-5.6-sol"


def test_custom_gateway_preserves_model_prefix() -> None:
    assert (
        normalized_model("openai/gpt-5.6-sol", "https://gateway.example/v1") == "openai/gpt-5.6-sol"
    )


def test_blank_base_url_is_none() -> None:
    assert normalized_base_url("") is None
    assert normalized_base_url("  ") is None
