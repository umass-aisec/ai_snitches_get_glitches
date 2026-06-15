"""Command-line interface: ``surveilbench download`` and ``surveilbench run``."""

from __future__ import annotations

import argparse
import json
import os
import sys

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


def _cmd_download(args: argparse.Namespace) -> int:
    from .data import download_dataset

    root = download_dataset(dest=args.dest, force=args.force)
    print(f"dataset ready at: {root}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from .data import sample_data_root
    from .evaluate import evaluate, format_report
    from .types import RunConfig

    api_base = os.getenv(args.api_base_env_name) if args.api_base_env_name else None

    config = RunConfig(
        model_id=args.model,
        system_prompt=args.system_prompt,
        user_prompt=args.user_prompt,
        api_base=api_base,
        temperature=args.temperature,
        max_steps=args.max_steps,
        include_distractors=not args.no_distractors,
        reasoning_effort=args.reasoning_effort,
        extra_body=_json_arg(args.extra_body, "--extra-body"),
        thinking=_json_arg(args.thinking, "--thinking"),
    )

    data_root = sample_data_root() if args.sample else args.data

    report = evaluate(
        config,
        data_root=data_root,
        axis=args.axis,
        severity_band=args.severity_band,
        limit=args.limit,
        workers=args.workers,
        out_dir=args.out,
        save_transcripts=not args.no_transcripts,
        skip_existing=not args.no_skip_existing,
        track_cost=not args.no_cost,
    )
    print()
    print(format_report(report))
    print(f"\nresults written to: {report['out_dir']}/")
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
    # dataset selection
    r.add_argument("--data", default=None, help="dataset root (default: auto-resolve / SURVEILBENCH_DATA)")
    r.add_argument("--sample", action="store_true", help="use the 3 bundled sample scenarios (offline)")
    r.add_argument("--axis", default=None, help="filter: corporate | educational | police")
    r.add_argument("--severity-band", default=None, help="filter: public | organizational | personal")
    r.add_argument("--limit", type=int, default=None, help="only the first N scenarios")
    # execution
    r.add_argument("--workers", type=int, default=8, help="parallel rollouts (default: 8)")
    r.add_argument("--max-steps", type=int, default=12, help="max agent steps (default: 12)")
    r.add_argument("--temperature", type=float, default=0.0, help="sampling temperature (default: 0.0)")
    r.add_argument("--no-distractors", action="store_true", help="drop the routine distractor docs")
    # reasoning controls (forwarded to litellm; unsupported ones are dropped)
    r.add_argument("--reasoning-effort", default=None, help="litellm reasoning_effort, e.g. low|medium|high|none")
    r.add_argument("--extra-body", default=None, help='litellm extra_body as JSON, e.g. \'{"thinking":{"type":"disabled"}}\'')
    r.add_argument("--thinking", default=None, help='litellm thinking as JSON, e.g. \'{"type":"enabled","budget_tokens":1024}\'')
    r.add_argument("--api-base-env-name", default=None, help="env var name holding an API base URL")
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
