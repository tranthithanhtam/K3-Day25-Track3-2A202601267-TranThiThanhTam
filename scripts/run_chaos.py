from __future__ import annotations

import argparse
import json
from pathlib import Path

from reliability_lab.chaos import load_queries, run_all_scenarios
from reliability_lab.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--out", default="reports/metrics.json")
    parser.add_argument("--csv", default="reports/metrics.csv")
    parser.add_argument(
        "--scenario-out",
        default=None,
        help="Where to write the per-scenario metrics "
        "(default: <out> with a _scenarios suffix).",
    )
    parser.add_argument(
        "--prom-out",
        default=None,
        help="Optional path for the Prometheus exposition-format export.",
    )
    parser.add_argument("--concurrency", type=int, default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    metrics, per_scenario = run_all_scenarios(
        config, load_queries(), concurrency=args.concurrency
    )

    metrics.write_json(args.out)
    print(f"wrote {args.out}")

    if args.csv:
        metrics.write_csv(args.csv)
        print(f"wrote {args.csv}")

    # Per-scenario numbers, so the report can show observed behaviour per scenario
    # instead of only a pass/fail flag.
    scenario_out = args.scenario_out
    if scenario_out is None:
        out_path = Path(args.out)
        scenario_out = str(out_path.with_name(f"{out_path.stem}_scenarios.json"))
    scenario_data = {
        name: {
            **result.to_report_dict(),
            "false_hit_examples": result.false_hit_examples,
            "status": metrics.scenarios.get(name, "unknown"),
        }
        for name, result in per_scenario.items()
    }
    Path(scenario_out).parent.mkdir(parents=True, exist_ok=True)
    Path(scenario_out).write_text(
        json.dumps(scenario_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"wrote {scenario_out}")

    if args.prom_out:
        Path(args.prom_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.prom_out).write_text(metrics.to_prometheus_format(), encoding="utf-8")
        print(f"wrote {args.prom_out}")


if __name__ == "__main__":
    main()
