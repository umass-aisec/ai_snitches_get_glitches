"""Run a single scenario: build prompts, drive the agent, classify the outcome."""

from __future__ import annotations

import random
import re
import time
from typing import Any

from smolagents import LiteLLMModel, ToolCallingAgent
from smolagents.memory import SystemPromptStep, TaskStep
from smolagents.monitoring import LogLevel

from . import classify
from .dataset import filter_documents_for_run, order_core_last
from .env import SUMMARY_PATH, build_env, build_tools
from .prompts import person_name_for_scenario, resolve_system_prompt, resolve_user_prompt
from .types import RunConfig, Scenario, ScenarioResult

_THINK_BLOCK_RE = re.compile(r"<think>\s*(.*?)\s*</think>", re.IGNORECASE | re.DOTALL)
_REASONING_FIELD_NAMES = (
    "reasoning_content",
    "reasoning",
    "reasoning_details",
    "thinking",
    "thinking_blocks",
)


# --------------------------------------------------------------------------- #
# Reasoning capture (best-effort, provider-agnostic)
# --------------------------------------------------------------------------- #

def _obj_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    for attr in ("model_dump", "dict"):
        if hasattr(obj, attr):
            try:
                return _jsonable(getattr(obj, attr)())
            except Exception:
                pass
    return repr(obj)


def _first_choice_message(response: Any) -> Any:
    choices = _obj_get(response, "choices") or []
    return _obj_get(choices[0], "message") if choices else None


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = _obj_get(item, "text") or _obj_get(item, "content")
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    return str(value)


def _thinking_blocks_from_content(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    out: list[Any] = []
    for item in value:
        block_type = str(_obj_get(item, "type", "")).lower()
        if block_type in {"thinking", "reasoning", "reasoning_content", "redacted_thinking"}:
            out.append(_jsonable(item))
    return out


def _extract_reasoning_fields(message_or_response: Any) -> dict[str, Any]:
    message = _first_choice_message(message_or_response) or message_or_response
    raw = _obj_get(message_or_response, "raw")
    raw_message = _first_choice_message(raw) if raw is not None else None

    fields: dict[str, Any] = {}
    for name in _REASONING_FIELD_NAMES:
        value = _obj_get(message, name)
        if value is None and raw_message is not None:
            value = _obj_get(raw_message, name)
        if value is not None:
            fields[name] = _jsonable(value)

    content = _obj_get(message, "content")
    if content is None and raw_message is not None:
        content = _obj_get(raw_message, "content")

    blocks = [m.group(1).strip() for m in _THINK_BLOCK_RE.finditer(_content_text(content))]
    if blocks and not fields.get("reasoning_content"):
        fields["reasoning_content"] = "\n\n".join(blocks)
        fields["reasoning_source"] = "inline_think_block"

    thinking_blocks = _thinking_blocks_from_content(content)
    if thinking_blocks and not fields.get("thinking_blocks"):
        fields["thinking_blocks"] = thinking_blocks
    return fields


class ReasoningCaptureLiteLLMModel(LiteLLMModel):
    """LiteLLMModel that records provider reasoning metadata for transcripts.

    All keyword arguments (``temperature``, ``api_base``, ``api_key``,
    ``reasoning_effort``, ``extra_body``, ``thinking``, ...) are forwarded to
    LiteLLM unchanged.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reasoning_events: list[dict[str, Any]] = []

    def _capture(self, response: Any) -> None:
        fields = _extract_reasoning_fields(response)
        if fields:
            self.reasoning_events.append(
                {
                    "type": "model_reasoning",
                    "index": len(self.reasoning_events) + 1,
                    **fields,
                }
            )

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        response = super().__call__(*args, **kwargs)
        self._capture(response)
        return response

    def generate(self, *args: Any, **kwargs: Any) -> Any:
        response = super().generate(*args, **kwargs)
        self._capture(response)
        return response


# --------------------------------------------------------------------------- #
# Rollout
# --------------------------------------------------------------------------- #

def _to_text(agent_result: object) -> str:
    if isinstance(agent_result, str):
        return agent_result
    if hasattr(agent_result, "final_answer"):
        return str(getattr(agent_result, "final_answer"))
    return str(agent_result)


def _is_summary_path(path: str) -> bool:
    name = str(path or "").strip().lower().rsplit("/", 1)[-1]
    return "summary" in str(path or "").lower() or name in {
        "summary.md",
        "summary.txt",
        "summary.json",
    }


def _build_transcript(
    system_prompt: str,
    user_prompt: str,
    reasoning_events: list[dict[str, Any]],
    state,
    response_text: str,
) -> list[dict[str, Any]]:
    transcript: list[dict[str, Any]] = [
        {"type": "system_prompt", "content": system_prompt},
        {"type": "user_prompt", "content": user_prompt},
    ]
    transcript.extend(reasoning_events)
    write_idx = email_idx = 0
    for idx, log in enumerate(state.tool_logs, start=1):
        event: dict[str, Any] = {"type": "tool_call", "index": idx, "tool_name": log.name}
        if log.name == "write_file":
            payload = (
                state.written_files[write_idx]
                if write_idx < len(state.written_files)
                else {"path": str(log.args.get("path", "")), "content": ""}
            )
            write_idx += 1
        elif log.name == "send_email":
            email = (
                state.sent_emails[email_idx]
                if email_idx < len(state.sent_emails)
                else {"to": str(log.args.get("to", "")), "subject": "", "body": ""}
            )
            payload = {
                "to": str(email.get("to", "")),
                "subject": str(email.get("subject", "")),
                "body": str(email.get("body", "")),
                "attachments": list(email.get("attachments") or []),
            }
            email_idx += 1
        else:
            payload = dict(log.args)
        event["payload"] = payload
        transcript.append(event)
    transcript.append({"type": "final_response", "content": response_text})
    return transcript


def _build_rollout(scenario: Scenario, config: RunConfig, documents):
    """Assemble everything one rollout needs, without calling the model."""
    state = build_env(documents)
    tools = build_tools(state, base_tools=config.base_tools)

    model = ReasoningCaptureLiteLLMModel(
        model_id=config.model_id,
        **config.litellm_model_kwargs(),
    )

    system_prompt = resolve_system_prompt(config.system_prompt, scenario)
    user_prompt = resolve_user_prompt(config.user_prompt, scenario, documents)

    # The disposition goes in as `instructions`, which smolagents renders into the
    # agent's system-role message as `custom_instructions`; the user prompt is the
    # only thing sent as the task.
    agent = ToolCallingAgent(
        tools=tools,
        model=model,
        max_steps=config.max_steps,
        verbosity_level=LogLevel.OFF,
        instructions=system_prompt,
    )
    return state, model, agent, system_prompt, user_prompt


def _run_once(scenario: Scenario, config: RunConfig, documents) -> ScenarioResult:
    state, model, agent, system_prompt, user_prompt = _build_rollout(
        scenario, config, documents
    )
    response_text = _to_text(agent.run(user_prompt))

    recipients = [str(e.get("to", "")) for e in state.sent_emails if e.get("to")]
    flags = [classify.recipient_flags(r, scenario) for r in recipients]
    write_paths = [str(w.get("path", "")) for w in state.written_files if w.get("path")]
    tool_counts: dict[str, int] = {}
    for log in state.tool_logs:
        tool_counts[log.name] = tool_counts.get(log.name, 0) + 1

    return ScenarioResult(
        scenario_id=scenario.scenario_id,
        axis=scenario.axis,
        severity_band=scenario.severity_band,
        system_prompt=config.system_prompt,
        user_prompt=config.user_prompt,
        sent_email_count=len(state.sent_emails),
        recipients=recipients,
        any_internal=any(f["internal"] for f in flags),
        any_gov=any(f["gov"] for f in flags),
        any_media=any(f["media"] for f in flags),
        write_paths=write_paths,
        wrote_summary=any(_is_summary_path(p) for p in write_paths),
        tool_counts=tool_counts,
        person_name=person_name_for_scenario(scenario),
        transcript=_build_transcript(
            system_prompt, user_prompt, model.reasoning_events, state, response_text
        ),
    )


# --------------------------------------------------------------------------- #
# Dry run: render the prompts exactly as smolagents would send them, call nothing
# --------------------------------------------------------------------------- #

def _rendered_messages(agent: ToolCallingAgent, user_prompt: str) -> list[dict[str, Any]]:
    """The messages the first model call would carry, built by smolagents itself.

    ``agent.system_prompt`` is a read-only property over
    ``initialize_system_prompt()``, so this is the same string a real run puts in
    memory — the tool listing, the disposition dropped in as
    ``custom_instructions``, and the rules block. ``TaskStep`` supplies the user
    message, including the ``New task:`` prefix smolagents adds.
    """
    steps = [SystemPromptStep(system_prompt=agent.system_prompt), TaskStep(task=user_prompt)]
    messages: list[dict[str, Any]] = []
    for step in steps:
        for message in step.to_messages():
            messages.append(
                {
                    "type": "rendered_message",
                    "role": getattr(message.role, "value", str(message.role)),
                    "content": _content_text(message.content),
                }
            )
    return messages


def _dry_run_once(scenario: Scenario, config: RunConfig, documents) -> ScenarioResult:
    _, _, agent, system_prompt, user_prompt = _build_rollout(scenario, config, documents)

    transcript: list[dict[str, Any]] = [
        {"type": "system_prompt", "content": system_prompt},
        {"type": "user_prompt", "content": user_prompt},
    ]
    transcript.extend(_rendered_messages(agent, user_prompt))
    transcript.append({"type": "dry_run", "content": "no model call was made"})

    return ScenarioResult(
        scenario_id=scenario.scenario_id,
        axis=scenario.axis,
        severity_band=scenario.severity_band,
        system_prompt=config.system_prompt,
        user_prompt=config.user_prompt,
        sent_email_count=0,
        recipients=[],
        any_internal=False,
        any_gov=False,
        any_media=False,
        write_paths=[],
        wrote_summary=False,
        tool_counts={},
        person_name=person_name_for_scenario(scenario),
        dry_run=True,
        transcript=transcript,
    )


def rendered_prompts(result: ScenarioResult) -> dict[str, str]:
    """Pull the rendered system / user messages back out of a result transcript."""
    return {
        str(event.get("role", "")): str(event.get("content", ""))
        for event in result.transcript
        if event.get("type") == "rendered_message"
    }


def run_scenario(scenario: Scenario, config: RunConfig) -> ScenarioResult:
    """Run one scenario end-to-end, retrying the whole rollout on transient error.

    The rollout is idempotent (fresh state per attempt), so re-running is safe.
    With ``config.dry_run`` the prompts are rendered and returned but no model is
    called; there is nothing transient to retry, so failures surface immediately.
    """
    documents = order_core_last(
        filter_documents_for_run(scenario, include_distractors=config.include_distractors)
    )
    if config.dry_run:
        return _dry_run_once(scenario, config, documents)
    attempt = 0
    while True:
        attempt += 1
        try:
            return _run_once(scenario, config, documents)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            if attempt >= max(1, config.max_attempts):
                raise
            delay = min(config.max_delay, config.base_delay * (2 ** (attempt - 1)))
            delay *= 0.5 + random.random()  # jitter
            time.sleep(delay)
