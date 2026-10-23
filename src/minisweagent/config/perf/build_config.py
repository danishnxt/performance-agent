#!/usr/bin/env python3
"""Build a performance-agent config: mini.yaml with the SWE workflow replaced by perf/workflow.md.

Usage: build_config.py OUT.yaml [--guide NAME] [--tools NAME]
  --guide: file in perf/guides/, without .md
  --tools: folder in perf/tools/; put on the agent's PATH and described from its TOOLS.md
  Adds guides/platform/<os>.md for the OS it runs on.
  --tools-dest: copy the tools there and put that copy on PATH, so PATH does not reveal this checkout
  --workdir, --scratch: tell the agent where the repository is and where to put scratch files
"""

import argparse
import os
import platform
import shutil
from pathlib import Path

import yaml

PERF_DIR = Path(__file__).parent
CONFIG_DIR = PERF_DIR.parent

SYSTEM = "You are a performance engineer who can interact with a computer. You find and fix the causes of slow software, and you back every claim with a measurement."

SUBMIT = """## Finishing

When you are finished, leave the repository containing only your measured fixes, give your summary, then issue this command on its own: `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`.
Do not combine it with any other command. <important>After this command, you cannot continue working on this task.</important>

"""


# What the fresh-look reviewer is told about each target, and its baseline timings (`name,ms`) in the repository.
FRESH_LOOK = {
    "duckdb": ("DuckDB, an analytical SQL database engine", "benchmark/BASELINE_SF10.csv"),
    "cpython": ("CPython, the reference Python interpreter", "benchmarks/BASELINE.csv"),
}


def raw(text: str) -> str:
    """Keep Markdown out of Jinja's way."""
    return "{% raw %}" + text.strip() + "{% endraw %}\n\n"


def build(
    guide: str | None,
    tools: str | None = None,
    tools_dest: Path | None = None,
    workdir: Path | None = None,
    scratch: Path | None = None,
    fresh_look: bool = False,
) -> dict:
    config = yaml.safe_load((CONFIG_DIR / "mini.yaml").read_text())
    template = config["agent"]["instance_template"]
    start = template.index("## Recommended Workflow")
    end = template.index("## Command Execution Rules")
    middle = raw((PERF_DIR / "workflow.md").read_text())
    if fresh_look:
        middle += "## Fresh looks\n\n" + raw((PERF_DIR / "fresh_look.md").read_text())
    if workdir:
        env = f"The repository is your current working directory, `{workdir}`. Every command starts there; do not search the machine for it."
        if scratch:
            env += f" Put scratch files in `{scratch}` (also `$TMPDIR`); `/tmp` is not available. There is no network access."
        middle = "## Environment\n\n" + raw(env) + middle
    os_guide = PERF_DIR / "guides" / "platform" / f"{platform.system().lower()}.md"
    if os_guide.is_file():
        middle += "## Platform\n\n" + raw(os_guide.read_text())
    if guide:
        middle += "## Domain guide\n\n" + raw((PERF_DIR / "guides" / f"{guide}.md").read_text())
    if tools:
        tools_dir = (PERF_DIR / "tools" / tools).resolve()
        middle += "## Tools\n\n" + raw((tools_dir / "TOOLS.md").read_text())
        if tools_dest:
            shutil.copytree(tools_dir, tools_dest, ignore=shutil.ignore_patterns("TOOLS.md"), dirs_exist_ok=True)
            tools_dir = tools_dest.resolve()
        config["environment"]["env"]["PATH"] = f"{tools_dir}:{os.environ['PATH']}"
        if tools in FRESH_LOOK:
            config["agent"]["fresh_look_project"], config["agent"]["fresh_look_baseline"] = FRESH_LOOK[tools]
    config["agent"]["instance_template"] = template[:start] + middle + SUBMIT + template[end:]
    config["agent"]["system_template"] = SYSTEM
    config["agent"]["agent_class"] = "minisweagent.agents.perf.PerfAgent"
    return config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--guide")
    parser.add_argument("--tools")
    parser.add_argument("--tools-dest", type=Path)
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--fresh-look", action="store_true")
    args = parser.parse_args()
    args.out.write_text(yaml.safe_dump(build(args.guide, args.tools, args.tools_dest, args.workdir, args.scratch, args.fresh_look), sort_keys=False, allow_unicode=True, width=10_000))


if __name__ == "__main__":
    main()
