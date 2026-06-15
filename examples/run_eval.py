"""Minimal Python-API example.

Runs the optimized agent against the 3 bundled sample scenarios (no dataset
download needed) and prints the per-band reporting-rate table.

    python examples/run_eval.py
"""

from surveilbench import RunConfig, evaluate, format_report, sample_data_root

config = RunConfig(
    model_id="anthropic/claude-sonnet-4-6",  # any litellm model id
    system_prompt="optimized",               # "base" | "optimized" | path to .txt
    user_prompt="plain",                     # "plain" | "hide" | "deceive" | "overescalate" | path
)

report = evaluate(
    config,
    data_root=sample_data_root(),  # omit to use the downloaded full dataset
    out_dir="out/sample",
)

print(format_report(report))
