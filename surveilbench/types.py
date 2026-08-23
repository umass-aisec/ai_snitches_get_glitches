"""Core dataclasses for the SurveilBench evaluation harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .providers import resolve_credentials

# Canonical severity bands, in reporting-scope order.
BANDS = ("public", "organizational", "personal")

# Map every recognized spelling (current canonical + historical names used in the
# dataset's metadata.json) to the canonical band. The Hugging Face dataset still
# ships the historical names, so they must remain accepted aliases.
_BAND_ALIASES = {
    # public — a clear, externally reportable issue (e.g. concealed safety hazard)
    "public": "public",
    "externally_reportable": "public",
    "external_report_oriented": "public",
    "externally reportable incidents": "public",
    # organizational — warrants internal review but not external disclosure
    "organizational": "organizational",
    "internally_reviewable": "organizational",
    "internal_report_oriented": "organizational",
    "internally reviewable incidents": "organizational",
    # personal — weak, privacy-sensitive behavioral signals
    "personal": "personal",
    "low-severity-signals": "personal",
    "surveillance_sensitive": "personal",
    "surveillance-sensitive": "personal",
    "surveillance_sensitive_weak_signal": "personal",
    "low-severity behavioral signals": "personal",
}


def normalize_severity_band(value: str | None) -> str | None:
    """Resolve any recognized band spelling to its canonical name."""
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return _BAND_ALIASES.get(normalized, normalized)


@dataclass
class Document:
    """One document shown to the agent (a scenario file rendered into context)."""

    path: str
    content: str
    category: str  # "general" for surveilbench
    doc_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scenario:
    """A loaded scenario: its documents plus the metadata.json fields."""

    scenario_id: str
    root: Path
    documents: list[Document] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def axis(self) -> str:
        return str(self.metadata.get("axis", ""))

    @property
    def severity_band(self) -> str:
        return normalize_severity_band(self.metadata.get("severity_band")) or ""


@dataclass
class VirtualFile:
    content: str
    doc_type: str
    doc_id: str | None = None
    deleted: bool = False
    previous_versions: list[str] = field(default_factory=list)


@dataclass
class VirtualFS:
    files: dict[str, VirtualFile] = field(default_factory=dict)


@dataclass
class ToolLog:
    name: str
    args: dict[str, Any]


@dataclass
class EnvState:
    """Mutable per-rollout state the agent's tools close over."""

    vfs: VirtualFS
    sent_emails: list[dict[str, Any]] = field(default_factory=list)
    written_files: list[dict[str, Any]] = field(default_factory=list)
    tool_logs: list[ToolLog] = field(default_factory=list)


@dataclass
class RunConfig:
    """Everything that defines one evaluation run.

    ``system_prompt`` and ``user_prompt`` are *specifications*, not the final
    prompt text:

    * ``system_prompt`` ∈ {``"base"``, ``"optimized"``, or a path to a .txt file}
    * ``user_prompt``   ∈ {``"plain"``, ``"hide"``, ``"deceive"``,
      ``"overescalate"``, or a path to a .txt file}

    Credentials are routed from ``model_id`` by :mod:`surveilbench.providers`:
    ``anthropic/*`` uses ``ANTHROPIC_API_BASE``/``ANTHROPIC_API_KEY``,
    ``gemini/*`` and ``fireworks_ai/*`` are called directly with no base URL,
    and every other model goes through ``OPENAI_API_BASE``/``OPENAI_API_KEY``.
    Set ``api_base`` / ``api_key`` explicitly to override the routing.

    Reasoning controls are forwarded verbatim to LiteLLM (and therefore to the
    provider). Anything a particular model does not understand is dropped
    automatically (``litellm.drop_params = True``), so it is safe to set them
    broadly. Different models want different controls, e.g.::

        reasoning_effort="low"                       # OpenAI o-series, some others
        thinking={"type": "enabled", "budget_tokens": 1024}   # Anthropic
        extra_body={"thinking": {"type": "disabled"}}         # provider-specific
    """

    model_id: str
    system_prompt: str = "optimized"
    user_prompt: str = "plain"

    # Both default to None, meaning "route from the model id" — see providers.py.
    # Set either to override the routed env var for this run.
    api_base: str | None = None
    api_key: str | None = None
    temperature: float = 0.0
    max_steps: int = 12
    include_distractors: bool = True

    # Add smolagents' own toolbox (python_interpreter / web_search /
    # visit_webpage) to the two scenario tools. The two web tools reach the live
    # internet; set False for the two-tool protocol the published numbers used.
    base_tools: bool = True

    # Assemble everything and render the prompts, but make no model call.
    dry_run: bool = False

    # Reasoning / thinking controls forwarded to LiteLLM.
    reasoning_effort: str | None = None
    extra_body: dict[str, Any] | None = None
    thinking: dict[str, Any] | None = None
    # Catch-all for any other LiteLLMModel/litellm kwarg (power users).
    model_kwargs: dict[str, Any] = field(default_factory=dict)

    # Per-scenario retry (whole-rollout re-run on transient failure).
    max_attempts: int = 4
    base_delay: float = 4.0
    max_delay: float = 60.0

    def litellm_model_kwargs(self) -> dict[str, Any]:
        """Assemble the kwargs forwarded to the LiteLLM model constructor."""
        kwargs: dict[str, Any] = dict(self.model_kwargs)
        kwargs["temperature"] = self.temperature
        kwargs.update(
            resolve_credentials(self.model_id, api_base=self.api_base, api_key=self.api_key)
        )
        if self.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.extra_body is not None:
            kwargs["extra_body"] = self.extra_body
        if self.thinking is not None:
            kwargs["thinking"] = self.thinking
        return kwargs


@dataclass
class ScenarioResult:
    """Outcome of one scenario rollout: what the agent did, and to whom."""

    scenario_id: str
    axis: str
    severity_band: str
    system_prompt: str
    user_prompt: str

    sent_email_count: int
    recipients: list[str]
    any_internal: bool
    any_gov: bool
    any_media: bool

    write_paths: list[str]
    wrote_summary: bool
    tool_counts: dict[str, int]

    # Who the identity block named, and whether the model was actually called.
    person_name: str | None = None
    dry_run: bool = False

    # Filled by the LiteLLM cost tracker when enabled (else 0).
    cost_usd: float = 0.0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    transcript: list[dict[str, Any]] = field(default_factory=list)
    record_path: str | None = None
    transcript_path: str | None = None
