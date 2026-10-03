#!/usr/bin/env python3
"""Build a performance-agent config: mini.yaml with the SWE workflow replaced by perf/workflow.md.

Usage: build_config.py OUT.yaml [--guide NAME]   (NAME = file in perf/guides/, without .md)
"""

import argparse
from pathlib import Path

import yaml

PERF_DIR = Path(__file__).parent
CONFIG_DIR = PERF_DIR.parent

SYSTEM = "You are a performance engineer who can interact with a computer. You find and fix the causes of slow software, and you back every claim with a measurement."

SUBMIT = """## Finishing

When you are finished, leave the repository containing only your measured fixes, give your summary, then issue this command on its own: `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`.
Do not combine it with any other command. <important>After this command, you cannot continue working on this task.</important>

"""


def raw(text: str) -> str:
    """Keep Markdown out of Jinja's way."""
    return "{% raw %}" + text.strip() + "{% endraw %}\n\n"


def build(guide: str | None) -> dict:
    config = yaml.safe_load((CONFIG_DIR / "mini.yaml").read_text())
    template = config["agent"]["instance_template"]
    start = template.index("## Recommended Workflow")
    end = template.index("## Command Execution Rules")
    middle = raw((PERF_DIR / "workflow.md").read_text())
    if guide:
        middle += "## Domain guide\n\n" + raw((PERF_DIR / "guides" / f"{guide}.md").read_text())
    config["agent"]["instance_template"] = template[:start] + middle + SUBMIT + template[end:]
    config["agent"]["system_template"] = SYSTEM
    return config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--guide")
    args = parser.parse_args()
    args.out.write_text(yaml.safe_dump(build(args.guide), sort_keys=False, allow_unicode=True, width=10_000))


if __name__ == "__main__":
    main()
