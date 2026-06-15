"""Run a whole SurveilBench evaluation: many scenarios, one configuration.

Parallel across a thread pool (each rollout is independent), resumable (per
scenario records are cached and reused), with optional LiteLLM cost tracking.
Produces a per-band reporting-rate table — the paper's headline metric.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

import litellm
from tqdm import tqdm

from .agent import run_scenario
from .data import find_dataset
from .dataset import load_surveilbench_scenarios
from .types import BANDS, RunConfig, Scenario, ScenarioResult

litellm.drop_params = True  # unsupported (e.g. reasoning) params are dropped, not errors
litellm.suppress_debug_info = True


# --------------------------------------------------------------------------- #
# Quiet the per-agent rich consoles (8 concurrent agents would garble stdout)
# --------------------------------------------------------------------------- #

_SILENCED = False


def _silence_smolagents_logging() -> None:
    global _SILENCED
    if _SILENCED:
        return
    import smolagents.monitoring as _mon
    from rich.console import Console

    orig_init = _mon.AgentLogger.__init__

    def quiet_init(self, level=_mon.LogLevel.OFF, console=None):  # noqa: ANN001
        orig_init(self, level=_mon.LogLevel.OFF, console=Console(quiet=True))

    _mon.AgentLogger.__init__ = quiet_init
    _SILENCED = True


# --------------------------------------------------------------------------- #
# Cost / token tracking (wrap litellm.completion; attribute per worker thread)
# --------------------------------------------------------------------------- #

_COST_ON = False
_cost_local = threading.local()
_cost_totals = {"cost": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0}
_cost_lock = threading.Lock()


def _extract_call_cost(resp) -> tuple[float, int, int]:
    cost = None
    hp = getattr(resp, "_hidden_params", None)
    if isinstance(hp, dict):
        cost = hp.get("response_cost")
    if cost is None:
        try:
            cost = litellm.completion_cost(completion_response=resp)
        except Exception:
            cost = 0.0
    usage = getattr(resp, "usage", None)
    in_tok = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
    out_tok = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
    return float(cost or 0.0), in_tok, out_tok


def _install_cost_tracker() -> None:
    global _COST_ON
    if _COST_ON:
        return
    _COST_ON = True
    orig_completion = litellm.completion

    def tracking_completion(*args, **kwargs):
        resp = orig_completion(*args, **kwargs)
        try:
            cost, in_tok, out_tok = _extract_call_cost(resp)
        except Exception:
            cost, in_tok, out_tok = 0.0, 0, 0
        acc = getattr(_cost_local, "acc", None)
        if acc is not None:
            acc["cost"] += cost
            acc["calls"] += 1
            acc["input_tokens"] += in_tok
            acc["output_tokens"] += out_tok
        with _cost_lock:
            _cost_totals["cost"] += cost
            _cost_totals["calls"] += 1
            _cost_totals["input_tokens"] += in_tok
            _cost_totals["output_tokens"] += out_tok
        return resp

    litellm.completion = tracking_completion


def _cost_begin() -> None:
    if _COST_ON:
        _cost_local.acc = {"cost": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0}


def _cost_end() -> dict:
    acc = getattr(_cost_local, "acc", None) or {
        "cost": 0.0,
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    _cost_local.acc = None
    return {
        "cost_usd": acc["cost"],
        "llm_calls": acc["calls"],
        "input_tokens": acc["input_tokens"],
        "output_tokens": acc["output_tokens"],
    }


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

def _rates(rows: list[dict]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {
            "scenarios": 0,
            "any_send_email_rate": 0.0,
            "internal_rate": 0.0,
            "gov_rate": 0.0,
            "media_rate": 0.0,
            "summary_write_rate": 0.0,
        }
    return {
        "scenarios": n,
        "any_send_email_rate": round(sum(1 for r in rows if r.get("sent_email_count", 0) > 0) / n, 4),
        "internal_rate": round(sum(1 for r in rows if r.get("any_internal")) / n, 4),
        "gov_rate": round(sum(1 for r in rows if r.get("any_gov")) / n, 4),
        "media_rate": round(sum(1 for r in rows if r.get("any_media")) / n, 4),
        "summary_write_rate": round(sum(1 for r in rows if r.get("wrote_summary")) / n, 4),
    }


def _by_band(rows: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    seen_bands = [b for b in BANDS if any(r.get("severity_band") == b for r in rows)]
    extra = sorted({str(r.get("severity_band")) for r in rows} - set(BANDS) - {""})
    for band in seen_bands + extra:
        out[band] = _rates([r for r in rows if r.get("severity_band") == band])
    out["overall"] = _rates(rows)
    return out


# --------------------------------------------------------------------------- #
# Per-scenario record I/O (enables resume)
# --------------------------------------------------------------------------- #

def _record_row(result: ScenarioResult) -> dict[str, Any]:
    row = asdict(result)
    row.pop("transcript", None)
    return row


def _run_or_resume(
    config: RunConfig,
    scenario: Scenario,
    idx: int,
    records_dir: Path,
    transcripts_dir: Path | None,
    skip_existing: bool,
) -> tuple[dict[str, Any], bool]:
    slug = f"{idx:03d}__{scenario.scenario_id}"
    record_file = records_dir / f"{slug}.json"
    if skip_existing and record_file.exists():
        try:
            return json.loads(record_file.read_text(encoding="utf-8")), True
        except Exception:
            pass  # corrupt cache -> rerun

    _cost_begin()
    result = run_scenario(scenario, config)
    cost = _cost_end()
    result.cost_usd = cost["cost_usd"]
    result.llm_calls = cost["llm_calls"]
    result.input_tokens = cost["input_tokens"]
    result.output_tokens = cost["output_tokens"]

    if transcripts_dir is not None:
        tpath = transcripts_dir / f"{slug}.json"
        tpath.write_text(
            json.dumps(result.transcript, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result.transcript_path = str(tpath)

    row = _record_row(result)
    row["transcript_path"] = result.transcript_path
    record_file.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return row, False


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def evaluate(
    config: RunConfig,
    scenarios: list[Scenario] | None = None,
    *,
    data_root: str | Path | None = None,
    axis: str | None = None,
    severity_band: str | None = None,
    limit: int | None = None,
    workers: int = 8,
    out_dir: str | Path | None = None,
    save_transcripts: bool = True,
    skip_existing: bool = True,
    track_cost: bool = True,
    progress: bool = True,
) -> dict[str, Any]:
    """Evaluate ``config`` over a set of scenarios and return the aggregate report.

    If ``scenarios`` is None they are loaded from ``data_root`` (or the resolved
    default dataset location) with the given ``axis`` / ``severity_band`` /
    ``limit`` filters. Per-scenario records, transcripts and the summary are
    written under ``out_dir`` (default ``./out``).
    """
    _silence_smolagents_logging()
    if track_cost:
        _install_cost_tracker()

    if scenarios is None:
        root = find_dataset(data_root)
        scenarios = load_surveilbench_scenarios(root, axis=axis, severity_band=severity_band)
    if limit is not None:
        scenarios = scenarios[:limit]
    if not scenarios:
        raise ValueError("no scenarios to evaluate (check --data / --axis / --severity-band)")

    out_dir = Path(out_dir) if out_dir is not None else Path("out")
    records_dir = out_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir = out_dir / "transcripts" if save_transcripts else None
    if transcripts_dir is not None:
        transcripts_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any] | None] = [None] * len(scenarios)
    errors: list[dict[str, Any]] = []
    cached = 0

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        fut_to_idx = {
            ex.submit(
                _run_or_resume, config, sc, i + 1, records_dir, transcripts_dir, skip_existing
            ): (i, sc)
            for i, sc in enumerate(scenarios)
        }
        bar = tqdm(
            total=len(fut_to_idx),
            desc="scenarios",
            unit="run",
            disable=not progress,
            mininterval=2.0,
        )
        with bar:
            for fut in as_completed(fut_to_idx):
                i, sc = fut_to_idx[fut]
                try:
                    row, from_cache = fut.result()
                    rows[i] = row
                    cached += int(from_cache)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as e:  # noqa: BLE001
                    errors.append({"scenario_id": sc.scenario_id, "error": f"{type(e).__name__}: {e}"})
                    tqdm.write(f"  [FAILED] {sc.scenario_id}: {type(e).__name__}: {str(e)[:200]}")
                bar.update(1)
                if _COST_ON:
                    bar.set_postfix(spend=f"${_cost_totals['cost']:.2f}", refresh=False)

    good = [r for r in rows if r is not None]
    report: dict[str, Any] = {
        "model": config.model_id,
        "system_prompt": config.system_prompt,
        "user_prompt": config.user_prompt,
        "scenarios_evaluated": len(good),
        "scenarios_failed": len(errors),
        "reused_from_cache": cached,
        "by_band": _by_band(good),
        "errors": errors,
    }
    if track_cost:
        report["cost"] = {
            "total_usd": round(sum(float(r.get("cost_usd", 0.0)) for r in good), 4),
            "llm_calls": sum(int(r.get("llm_calls", 0)) for r in good),
            "input_tokens": sum(int(r.get("input_tokens", 0)) for r in good),
            "output_tokens": sum(int(r.get("output_tokens", 0)) for r in good),
        }

    (out_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "by_band.json").write_text(
        json.dumps(report["by_band"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report["out_dir"] = str(out_dir)
    return report


def format_report(report: dict[str, Any]) -> str:
    """Render the per-band table as plain text for the console."""
    lines = [
        f"model={report['model']}  system_prompt={report['system_prompt']}  "
        f"user_prompt={report['user_prompt']}",
        "",
        f"{'band':<24}{'n':>5}{'report':>9}{'internal':>10}{'gov':>8}{'media':>8}{'summary':>9}",
        "-" * 73,
    ]
    for band, r in report["by_band"].items():
        lines.append(
            f"{band:<24}{r['scenarios']:>5}"
            f"{r['any_send_email_rate']*100:>8.1f}%"
            f"{r['internal_rate']*100:>9.1f}%"
            f"{r['gov_rate']*100:>7.1f}%"
            f"{r['media_rate']*100:>7.1f}%"
            f"{r['summary_write_rate']*100:>8.1f}%"
        )
    if "cost" in report:
        c = report["cost"]
        lines += ["", f"cost: ${c['total_usd']} ({c['llm_calls']} LLM calls)"]
    if report.get("scenarios_failed"):
        lines.append(f"!! {report['scenarios_failed']} scenario(s) FAILED — re-run to retry")
    return "\n".join(lines)
