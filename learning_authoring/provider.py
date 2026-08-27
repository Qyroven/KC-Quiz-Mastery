"""OpenAI client construction and non-generating connectivity checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import OpenAI


def normalized_base_url(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def normalized_model(model: str, base_url: str | None) -> str:
    """Translate gateway-qualified OpenAI IDs for the official API endpoint."""

    cleaned = model.strip()
    endpoint = normalized_base_url(base_url)
    if endpoint is None or endpoint.rstrip("/") == "https://api.openai.com/v1":
        return cleaned.removeprefix("openai/")
    return cleaned


def build_client(
    *,
    api_key: str,
    base_url: str | None,
    timeout_seconds: float | None = None,
) -> OpenAI:
    kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 0}
    endpoint = normalized_base_url(base_url)
    if endpoint is not None:
        kwargs["base_url"] = endpoint
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds
    return OpenAI(**kwargs)


@dataclass(frozen=True)
class ProviderCheck:
    authenticated: bool
    configured_model: str
    effective_model: str
    model_visible: bool
    visible_model_count: int
    endpoint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "authenticated": self.authenticated,
            "configured_model": self.configured_model,
            "effective_model": self.effective_model,
            "model_visible": self.model_visible,
            "visible_model_count": self.visible_model_count,
            "endpoint": self.endpoint,
            "generation_performed": False,
        }


def check_provider(
    *,
    api_key: str,
    model: str,
    base_url: str | None,
    timeout_seconds: float = 20.0,
) -> ProviderCheck:
    """Authenticate and inspect model visibility without generating content."""

    endpoint = normalized_base_url(base_url)
    effective_model = normalized_model(model, endpoint)
    client = build_client(api_key=api_key, base_url=endpoint, timeout_seconds=timeout_seconds)
    models = {item.id for item in client.models.list().data}
    return ProviderCheck(
        authenticated=True,
        configured_model=model,
        effective_model=effective_model,
        model_visible=effective_model in models,
        visible_model_count=len(models),
        endpoint=endpoint or "https://api.openai.com/v1",
    )
