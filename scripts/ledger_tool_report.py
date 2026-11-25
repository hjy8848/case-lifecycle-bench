"""List every tool of the 20 templates with its Case Ledger class and object id fields.

    python scripts/ledger_tool_report.py > docs/LEDGER_TOOL_CLASSIFICATION.md

The class comes only from case_ledger.tools.tool_kind (one default rule); a human reviews the result.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from case_lifecycle_bench.case_ledger.tools import KIND_OVERRIDES, SUBJECT_KEYS, tool_kind  # noqa: E402
from case_lifecycle_bench.runnable_templates import ALL  # noqa: E402


def main():
    per_tool: dict = {}
    for name, workflow in ALL.items():
        for tool in workflow.tools:
            function = tool["function"]
            fields = [k for k in function["parameters"].get("properties", {}) if k in SUBJECT_KEYS]
            others = [k for k in function["parameters"].get("properties", {}) if k.endswith("_id") and k not in SUBJECT_KEYS]
            row = per_tool.setdefault(function["name"], {"fields": set(), "others": set(), "templates": set()})
            row["fields"].update(fields)
            row["others"].update(others)
            row["templates"].add(name)
    print("# Case Ledger tool classification\n")
    print(f"{len(per_tool)} distinct tools over {len(ALL)} templates. Default rule only; overrides in use: {dict(KIND_OVERRIDES) or 'none'}.\n")
    print("| tool | kind | object id fields | other *_id fields (not used for matching) | templates |")
    print("| --- | --- | --- | --- | --- |")
    for tool, row in sorted(per_tool.items(), key=lambda item: (tool_kind(item[0]), item[0])):
        print(f"| {tool} | {tool_kind(tool)} | {', '.join(sorted(row['fields'])) or '-'} | {', '.join(sorted(row['others'])) or '-'} | {len(row['templates'])} |")
    print("\n## Write tools without an object id field\n")
    for tool, row in sorted(per_tool.items()):
        if tool_kind(tool) == "write" and not row["fields"]:
            print(f"- {tool}")
    print("\n## Per template\n")
    for name, workflow in ALL.items():
        kinds: dict = {}
        for tool in workflow.tools:
            kinds.setdefault(tool_kind(tool["function"]["name"]), []).append(tool["function"]["name"])
        print(f"- **{name}**: " + "; ".join(f"{kind}: {', '.join(sorted(names))}" for kind, names in sorted(kinds.items())))


if __name__ == "__main__":
    main()
