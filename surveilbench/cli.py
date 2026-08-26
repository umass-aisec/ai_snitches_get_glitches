"""Command-line interface: ``surveilbench download`` and ``surveilbench run``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# Load API keys from .env before anything touches litellm/providers.
load_dotenv(find_dotenv(usecwd=True))


def _json_arg(value: str | None, flag: str) -> dict | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as e:
        raise SystemExit(f"{flag}: invalid JSON ({e})")
    if not isinstance(parsed, dict):
        raise SystemExit(f"{flag}: expected a JSON object, got {type(parsed).__name__}")
    return parsed


def _followup_arg(value: str | None) -> str | list[str] | None:
    """``--followup a,b`` -> a list; a single spec stays a plain string.

    Comma-separated like ``--scenario``. Several strategies share one task turn
    per scenario, so listing them here is much cheaper than separate runs — and
    compares them on the same rollout.
    """
    if not value:
        return None
    if "," not in value:
        return value
    specs = [part.strip() for part in value.split(",") if part.strip()]
    return specs or None


def _print_dry_run_prompts(report: dict) -> None:
    """Echo the first dry-run scenario's rendered messages to the console."""
    transcripts = Path(report["out_dir"]) / "transcripts"
    files = sorted(transcripts.glob("*.json")) if transcripts.is_dir() else []
    if not files:
        print("\n(no transcripts written — drop --no-transcripts to see the prompts)")
        return
    events = json.loads(files[0].read_text(encoding="utf-8"))
    print(f"\n===== rendered prompts — {files[0].stem} =====")
    for event in events:
        if event.get("type") == "rendered_message":
            print(f"\n----- {event.get('role', '?')} message -----")
            print(event.get("content", ""))
        elif event.get("type") == "followup_prompt":
            print("\n----- follow-up (sent after the task turn) -----")
            print(event.get("content", ""))
    if len(files) > 1:
        print(f"\n({len(files) - 1} more under {transcripts}/)")


def _print_annotation_stats(root, stats: dict) -> None:
    print(
        f"annotated {stats['scenarios']} scenarios at {root}: "
        f"{stats['chat_log']} from the AI chat log, "
        f"{stats['email_thread']} from the email thread"
    )
    if stats["unresolved"]:
        print(f"  no person found in {len(stats['unresolved'])}: {', '.join(stats['unresolved'])}")


def _cmd_download(args: argparse.Namespace) -> int:
    from .annotate import annotate_dataset
    from .data import download_dataset

    root = download_dataset(dest=args.dest, force=args.force)
    # A fresh download has no person_name field; add it before anyone runs.
    _print_annotation_stats(root, annotate_dataset(root))
    print(f"dataset ready at: {root}")
    return 0


def _cmd_annotate(args: argparse.Namespace) -> int:
    from .annotate import annotate_dataset
    from .data import find_dataset

    root = find_dataset(args.data)
    _print_annotation_stats(root, annotate_dataset(root, overwrite=not args.keep_existing))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from .data import sample_data_root
    from .evaluate import evaluate, format_report
    from .types import RunConfig

    # Left as None, api_base/api_key are routed from the model id (providers.py).
    api_base = args.api_base or (
        os.getenv(args.api_base_env_name) if args.api_base_env_name else None
    )

    config = RunConfig(
        model_id=args.model,
        system_prompt=args.system_prompt,
        user_prompt=args.user_prompt,
        followup_prompt=_followup_arg(args.followup),
        api_base=api_base,
        temperature=args.temperature,
        max_steps=args.max_steps,
        include_distractors=not args.no_distractors,
        base_tools=args.base_tools,
        benign=args.benign,
        dry_run=args.dry_run,
        reasoning_effort=args.reasoning_effort,
        extra_body=_json_arg(args.extra_body, "--extra-body"),
        thinking=_json_arg(args.thinking, "--thinking"),
    )

    data_root = sample_data_root() if args.sample else args.data

    try:
        report = evaluate(
            config,
            data_root=data_root,
            axis=args.axis,
            severity_band=args.severity_band,
            scenario_ids=args.scenario,
            limit=args.limit,
            workers=args.workers,
            out_dir=args.out,
            save_transcripts=not args.no_transcripts,
            skip_existing=not args.no_skip_existing,
            track_cost=not args.no_cost,
        )
    except (ValueError, FileNotFoundError) as e:  # bad selection / missing data — usage errors
        raise SystemExit(str(e))
    print()
    print(format_report(report))
    # One directory per follow-up strategy, so list them all rather than the first.
    out_dirs = [run["out_dir"] for run in report.get("followup_runs") or [report]]
    print("\nresults written to:")
    for path in out_dirs:
        print(f"  {path}/")
    if args.dry_run:
        _print_dry_run_prompts(report)
    return 2 if report.get("scenarios_failed") else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="surveilbench",
        description="Evaluate AI-agent reporting behavior on the SurveilBench benchmark.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("download", help="download the SurveilBench dataset from Hugging Face")
    d.add_argument("--dest", default=None, help="destination directory (default: ./data)")
    d.add_argument("--force", action="store_true", help="re-download even if present")
    d.set_defaults(func=_cmd_download)

    a = sub.add_parser(
        "annotate",
        help="write each scenario's person_name into its metadata.json (run by `download`)",
    )
    a.add_argument("--data", default=None, help="dataset root (default: auto-resolve)")
    a.add_argument(
        "--keep-existing",
        action="store_true",
        help="leave scenarios that already have a person_name untouched",
    )
    a.set_defaults(func=_cmd_annotate)

    r = sub.add_parser("run", help="run an evaluation")
    r.add_argument("--model", required=True, help="litellm model id, e.g. anthropic/claude-sonnet-4-6")
    r.add_argument(
        "--system-prompt",
        default="optimized",
        help="agent disposition: 'base', 'optimized', or a path to a .txt file (default: optimized)",
    )
    r.add_argument(
        "--user-prompt",
        default="plain",
        help="user request / defense: 'plain', 'hide', 'deceive', 'overescalate', "
        "or a path to a .txt file (default: plain)",
    )
    r.add_argument(
        "--followup",
        default=None,
        help="ask a second question in the same conversation after the task: "
        "'honesty' (did you contact anyone?), 'honesty_prefill', or a path to a "
        ".txt file. Comma-separate several to ask each of them about the same "
        "task turn, paying for that turn once (default: no follow-up turn)",
    )
    # dataset selection
    r.add_argument("--data", default=None, help="dataset root (default: auto-resolve / SURVEILBENCH_DATA)")
    r.add_argument("--sample", action="store_true", help="use the 3 bundled sample scenarios (offline)")
    r.add_argument(
        "--benign",
        action="store_true",
        help="evaluate the benign control split (<data>/benign/, 27 scenarios with "
        "the wrongdoing written out) instead of the three main axes — every email "
        "sent there is a false positive. Results go to <out>/benign/<run_key>/",
    )
    r.add_argument("--axis", default=None, help="filter: corporate | educational | police")
    r.add_argument("--severity-band", default=None, help="filter: public | organizational | personal")
    r.add_argument(
        "--scenario",
        default=None,
        help="filter: comma-separated scenario selectors, e.g. 'scenario_042', "
        "'corporate/scenario_042' or '42' (ids repeat across axes — qualify to "
        "pick exactly one)",
    )
    r.add_argument("--limit", type=int, default=None, help="only the first N scenarios")
    # execution
    r.add_argument("--workers", type=int, default=8, help="parallel rollouts (default: 8)")
    r.add_argument("--max-steps", type=int, default=12, help="max agent steps (default: 12)")
    r.add_argument("--temperature", type=float, default=0.0, help="sampling temperature (default: 0.0)")
    r.add_argument("--no-distractors", action="store_true", help="drop the routine distractor docs")
    r.add_argument(
        "--base-tools",
        action="store_true",
        help="include smolagents' python_interpreter / web_search / visit_webpage",
    )
    r.add_argument(
        "--dry-run",
        action="store_true",
        help="render the prompts and write them under <out>/dry_run/<run_key>/, without calling the model",
    )
    # reasoning controls (forwarded to litellm; unsupported ones are dropped)
    r.add_argument("--reasoning-effort", default=None, help="litellm reasoning_effort, e.g. low|medium|high|none")
    r.add_argument("--extra-body", default=None, help='litellm extra_body as JSON, e.g. \'{"thinking":{"type":"disabled"}}\'')
    r.add_argument("--thinking", default=None, help='litellm thinking as JSON, e.g. \'{"type":"enabled","budget_tokens":1024}\'')
    # credentials (by default routed from --model; see surveilbench/providers.py)
    r.add_argument("--api-base", default=None, help="override the routed API base URL")
    r.add_argument(
        "--api-base-env-name",
        default=None,
        help="override the routed API base URL with the value of this env var",
    )
    # output / caching
    r.add_argument("--out", default="out", help="output directory (default: ./out)")
    r.add_argument("--no-transcripts", action="store_true", help="do not save raw transcripts")
    r.add_argument("--no-skip-existing", action="store_true", help="do not reuse cached per-scenario records")
    r.add_argument("--no-cost", action="store_true", help="disable LiteLLM cost/token tracking")
    r.set_defaults(func=_cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
