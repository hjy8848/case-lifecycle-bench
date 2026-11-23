"""Select three cases each for T1, T2 and T9 from a seeded Retail pilot."""

import argparse
import json
from pathlib import Path

from .pilot import run_pilot


_PANEL_TEMPLATES = {
    "t1": "refund_silent_failure",
    "t2": "refund_timeout_committed",
    "t9": "cross_channel_revisit",
}
_CASES_PER_TEMPLATE = 3


def select_panel(pilot_result: dict) -> list[dict[str, str]]:
    """Keep the first cases in the seeded draw and sort each group by order ID."""
    panel = []
    for short_template, template_id in _PANEL_TEMPLATES.items():
        selected = [
            row["case"]
            for row in pilot_result["results"]
            if row["case"]["template"] == template_id
        ][:_CASES_PER_TEMPLATE]
        if len(selected) != _CASES_PER_TEMPLATE:
            raise ValueError(
                f"{short_template}: fewer than {_CASES_PER_TEMPLATE} pilot cases"
            )
        for case in sorted(selected, key=lambda row: row["retail_source"]["order_id"]):
            panel.append(
                {
                    "template": short_template,
                    "order_id": case["retail_source"]["order_id"],
                    "case_id": case["case_id"],
                }
            )

    if len({row["order_id"] for row in panel}) != len(panel):
        raise ValueError("Panel orders must be unique")
    return panel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--seed", type=int, default=300, help="Regenerate the Retail pilot"
    )
    source.add_argument("--pilot", type=Path, help="Select from a saved pilot report")
    args = parser.parse_args()
    if args.pilot is not None:
        pilot_result = json.loads(args.pilot.read_text(encoding="utf-8"))
    else:
        pilot_result = run_pilot(args.seed)
    print(json.dumps({"cases": select_panel(pilot_result)}, indent=2))


if __name__ == "__main__":
    main()
