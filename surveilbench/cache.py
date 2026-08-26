"""Where a run's per-scenario records live, and how the resume cache is keyed.

A record's path is derived from **what was run** — the configuration, the axis
and the scenario id — never from *where the scenario happened to sit in the
filtered list*. That is the whole point of this module::

    <out>/<run_key>/config.json
    <out>/<run_key>/summary.json
    <out>/<run_key>/by_band.json
    <out>/<run_key>/records/<axis>__<scenario_id>.json
    <out>/<run_key>/transcripts/<axis>__<scenario_id>.json

``run_key`` is ``<model>__<system_prompt>__<user_prompt>[__fu-<followup>]__<hash8>``:
readable enough to recognise in a directory listing, with a hash covering every
field that changes what the model is asked (see :func:`config_fingerprint`).
The follow-up segment appears only when a follow-up question is configured.

Two consequences, both deliberate:

* Any slice of the dataset — ``--axis``, ``--severity-band``, ``--limit``,
  ``--scenario``, in any order — reuses the same cached record for the same
  scenario. Position no longer participates in the key.
* Different configurations can share one ``--out`` without silently reading
  each other's records, because they resolve to different ``run_key`` dirs.

The axis is part of the record name because ``scenario_001`` exists in all
three axes; keying on the scenario id alone would collide across them. The
benign control split repeats those same axis/id pairs with rewritten documents,
so it is separated one level up instead — ``<out>/benign/<run_key>/``
(:func:`run_dir`).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .env import SCENARIO_TOOL_NAMES
from .types import RunConfig, Scenario

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(value: str, limit: int = 40) -> str:
    """Filesystem-safe fragment of ``value``, truncated to ``limit``."""
    cleaned = _UNSAFE.sub("-", str(value or "").strip()).strip("-.")
    return (cleaned or "none")[:limit]


def _prompt_identity(spec: str) -> tuple[str, str]:
    """``(slug, fingerprint_term)`` for a prompt spec.

    A keyword (``optimized``, ``plain``, …) identifies itself. A ``.txt`` path
    is identified by its stem *and* a hash of its contents, so editing a custom
    prompt file invalidates the cache instead of silently reusing rollouts from
    the previous wording. A path that no longer exists falls back to the spec
    string.
    """
    spec = str(spec or "")
    path = Path(spec)
    if path.suffix.lower() == ".txt" and path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        return _slug(path.stem, 24), f"file:{path.name}:{digest}"
    return _slug(spec, 24), f"spec:{spec}"


def config_fingerprint(config: RunConfig) -> dict[str, Any]:
    """The fields of ``config`` that change what the model is asked.

    Excluded on purpose: ``api_key`` (a secret, and not part of the run's
    identity), the retry policy (``max_attempts`` / delays — it only affects how
    hard we try, not the result), and ``dry_run`` (which already gets its own
    output subtree).

    ``scenario_tools`` is not a ``RunConfig`` field but a property of the code:
    it is here so that editing the always-on toolkit in :mod:`surveilbench.env`
    changes the ``run_key``, rather than letting a new run resume from records
    produced under a different set of tools.

    ``followup_prompt`` is included **only when one is set**, so adding the
    follow-up turn did not renumber the hash of every single-turn run that came
    before it. A follow-up run and a single-turn run of the same configuration
    still land in different directories, which is the property that matters.
    ``benign`` is conditional for the same reason and only ever appears as
    ``True``; the benign split also gets its own subtree (see :func:`run_dir`),
    so this is belt-and-braces rather than the sole guard.
    """
    _, system_term = _prompt_identity(config.system_prompt)
    _, user_term = _prompt_identity(config.user_prompt)
    specs = config.followup_specs()
    followup: dict[str, Any] = {}
    if len(specs) == 1:
        # Kept as a bare string, not a 1-element list, so every run directory
        # written before multi-followup existed still hashes to the same key.
        followup["followup_prompt"] = _prompt_identity(specs[0])[1]
    elif specs:
        followup["followup_prompt"] = [_prompt_identity(s)[1] for s in specs]
    if config.benign:
        followup["benign"] = True
    return {
        **followup,
        "model_id": config.model_id,
        "system_prompt": system_term,
        "user_prompt": user_term,
        "temperature": config.temperature,
        "max_steps": config.max_steps,
        "include_distractors": config.include_distractors,
        "base_tools": config.base_tools,
        "scenario_tools": list(SCENARIO_TOOL_NAMES),
        "reasoning_effort": config.reasoning_effort,
        "extra_body": config.extra_body,
        "thinking": config.thinking,
        "model_kwargs": config.model_kwargs,
        "api_base": config.api_base,
    }


def run_key(config: RunConfig) -> str:
    """Directory name identifying this configuration's outputs."""
    fingerprint = config_fingerprint(config)
    digest = hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:8]
    system_slug, _ = _prompt_identity(config.system_prompt)
    user_slug, _ = _prompt_identity(config.user_prompt)
    # The follow-up segment is appended only when there is one, so single-turn
    # run directories keep the names they have always had.
    specs = config.followup_specs()
    followup_slug = (
        "__fu-" + "+".join(_prompt_identity(s)[0] for s in specs) if specs else ""
    )
    return f"{_slug(config.model_id)}__{system_slug}__{user_slug}{followup_slug}__{digest}"


def config_for_followup(config: RunConfig, spec: str | None) -> RunConfig:
    """``config`` narrowed to a single follow-up spec.

    Results of a multi-followup run are filed under the directory each strategy
    would have had on its own, so a later ``--followup <one-of-them>`` run finds
    them in its cache instead of paying for the task turn again.
    """
    return replace(config, followup_prompt=spec)


def run_dir(out_dir: str | Path, config: RunConfig) -> Path:
    """The directory this run writes to (``<out>/[dry_run/][benign/]<run_key>``).

    The benign control split gets its own subtree because it shares every
    ``axis``/``scenario_id`` with the main split while showing the agent a
    different packet: sharing a directory would let benign
    ``corporate/scenario_001`` resume from the record of main
    ``corporate/scenario_001``, and would leave the two splits overwriting one
    ``summary.json``. ``dry_run`` stays the outermost segment so a dry run can
    still never touch a real run's records.
    """
    base = Path(out_dir)
    if config.dry_run:
        base = base / "dry_run"
    if config.benign:
        base = base / "benign"
    return base / run_key(config)


def scenario_axis(scenario: Scenario) -> str:
    """The axis a scenario belongs to, from its metadata or its parent dir."""
    axis = str((scenario.metadata or {}).get("axis") or "").strip()
    if not axis and getattr(scenario, "root", None) is not None:
        axis = Path(scenario.root).parent.name
    return axis or "unknown"


def record_slug(axis: str, scenario_id: str) -> str:
    """Stable per-scenario file stem: ``<axis>__<scenario_id>``."""
    return f"{_slug(axis, 24)}__{_slug(scenario_id, 48)}"


def slug_for_scenario(scenario: Scenario) -> str:
    return record_slug(scenario_axis(scenario), scenario.scenario_id)


def write_run_config(directory: Path, config: RunConfig) -> None:
    """Drop a ``config.json`` beside the records so ``run_key`` stays legible."""
    payload = {k: v for k, v in asdict(config).items() if k != "api_key"}
    payload["run_key"] = run_key(config)
    payload["fingerprint"] = config_fingerprint(config)
    (directory / "config.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

