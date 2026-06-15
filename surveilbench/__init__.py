"""SurveilBench — evaluate how AI agents handle institutional risk and reporting.

Run an agent over the SurveilBench scenarios and measure whether (and to whom)
it escalates by email. Pick the agent's disposition (``base`` / ``optimized`` /
your own) and the user prompt / defense (``plain`` / ``hide`` / ``deceive`` /
``overescalate`` / your own).

Quickstart (Python):

    from surveilbench import RunConfig, evaluate, format_report

    report = evaluate(
        RunConfig(model_id="anthropic/claude-sonnet-4-6", system_prompt="optimized"),
        data_root="data/surveilbench",  # or omit to auto-resolve
        out_dir="out",
    )
    print(format_report(report))
"""

from __future__ import annotations

from .agent import run_scenario
from .data import download_dataset, find_dataset, sample_data_root
from .dataset import load_surveilbench_scenarios
from .evaluate import evaluate, format_report
from .prompts import (
    load_optimized_disposition,
    load_optimized_user_prompt,
    resolve_system_prompt,
    resolve_user_prompt,
)
from .types import Document, RunConfig, Scenario, ScenarioResult

__version__ = "0.1.0"

__all__ = [
    "RunConfig",
    "Scenario",
    "ScenarioResult",
    "Document",
    "run_scenario",
    "evaluate",
    "format_report",
    "load_surveilbench_scenarios",
    "download_dataset",
    "find_dataset",
    "sample_data_root",
    "resolve_system_prompt",
    "resolve_user_prompt",
    "load_optimized_disposition",
    "load_optimized_user_prompt",
    "__version__",
]
