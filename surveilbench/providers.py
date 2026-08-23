"""Provider routing: which API base URL and key a given model id is called with.

`.env` carries four credentials; the model id's litellm provider prefix decides
which of them a run uses:

===================  ====================  ======================
model id prefix      api_base              api_key
===================  ====================  ======================
``anthropic/``       ``ANTHROPIC_API_BASE``  ``ANTHROPIC_API_KEY``
``gemini/``          *(none — direct)*       ``GEMINI_API_KEY``
``fireworks_ai/``    *(none — direct)*       ``FIREWORKS_AI_API_KEY``
*everything else*    ``OPENAI_API_BASE``     ``OPENAI_API_KEY``
===================  ====================  ======================

The last row is the deliberate part: every other model — including non-OpenAI
ones such as ``deepseek/``, ``xai/`` or ``moonshot/`` — is reached through the
OpenAI-compatible gateway at ``OPENAI_API_BASE`` and authenticates with
``OPENAI_API_KEY``, *not* with a provider-native key. Gemini and Fireworks are
the two providers called directly, so they get no base URL override and litellm
resolves their endpoint itself.

Credentials are read from the environment at call time, so this works whether
``.env`` was loaded by `cli.py` or by a Python-API caller.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderRoute:
    """The pair of env vars a provider's base URL and key are read from."""

    api_base_env: str | None
    api_key_env: str

    def api_base(self) -> str | None:
        return _env(self.api_base_env) if self.api_base_env else None

    def api_key(self) -> str | None:
        return _env(self.api_key_env)


# Providers called directly, on their own endpoint.
ANTHROPIC_ROUTE = ProviderRoute("ANTHROPIC_API_BASE", "ANTHROPIC_API_KEY")
GEMINI_ROUTE = ProviderRoute(None, "GEMINI_API_KEY")
FIREWORKS_ROUTE = ProviderRoute(None, "FIREWORKS_AI_API_KEY")

# Everything else goes through the OpenAI-compatible gateway.
DEFAULT_ROUTE = ProviderRoute("OPENAI_API_BASE", "OPENAI_API_KEY")

_ROUTES: dict[str, ProviderRoute] = {
    "anthropic": ANTHROPIC_ROUTE,
    "gemini": GEMINI_ROUTE,
    "fireworks_ai": FIREWORKS_ROUTE,
    "fireworks": FIREWORKS_ROUTE,
}


def _env(name: str | None) -> str | None:
    """Read an env var, treating blank / whitespace-only as unset."""
    if not name:
        return None
    value = (os.getenv(name) or "").strip()
    return value or None


def provider_prefix(model_id: str) -> str:
    """The litellm provider prefix of a model id (``""`` if it carries none)."""
    model_id = (model_id or "").strip()
    return model_id.split("/", 1)[0].lower() if "/" in model_id else ""


def route_for(model_id: str) -> ProviderRoute:
    """The :class:`ProviderRoute` a model id resolves to."""
    return _ROUTES.get(provider_prefix(model_id), DEFAULT_ROUTE)


def resolve_credentials(
    model_id: str,
    api_base: str | None = None,
    api_key: str | None = None,
) -> dict[str, str]:
    """Assemble the ``api_base`` / ``api_key`` kwargs for a model id.

    Explicit ``api_base`` / ``api_key`` arguments win over the routed env vars.
    Keys whose value resolves to nothing are omitted entirely, so litellm falls
    back to its own credential discovery rather than receiving ``None``.
    """
    route = route_for(model_id)
    resolved = {
        "api_base": api_base if api_base is not None else route.api_base(),
        "api_key": api_key if api_key is not None else route.api_key(),
    }
    return {k: v for k, v in resolved.items() if v}
