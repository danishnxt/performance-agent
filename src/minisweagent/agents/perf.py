"""Performance agent: the interactive agent plus a fresh look at a file the agent is circling, and fresh-context
episodes.

Episodes (`episode_steps`): every N steps the agent writes a handoff note, its conversation is cleared, and it carries
on from the task and that note alone; the repository, build and scratch files stay. Epoch 18 pilots found the first
~50 steps the sharpest: later the agent re-read its own early conclusions ("this is upstream code") as settled and
talked itself out of the planted causes. The saved trajectory keeps every episode's messages in order.

A fresh look sends one file to the same model in a separate request: no run history, no profiles and none of the
agent's theories, only the file, the lines the agent is on, the project and the measured symptom. The answer goes
back to the agent as a lead to test with the benchmark. It fires once per file, when the agent

- has named the file in `fresh_look_visits` steps without changing it (reading around it, not acting), or
- changed the file, and the next `perf-compare` showed no benchmark faster than when it was last measured (a failed try).

The request runs in the background, so the agent keeps working; the answer is handed back at the first step after
it arrives.

Epoch 11 (LOGS/cold_check_q19_matrix): with the Q19 profiles attached the model trusted them and named the planted
line 1/3 instead of 3/3, so the request carries no profiles. Epoch 17 (fresh-look rewording): asked "is the regression
caused at this location?" the model said yes about 76% of innocent files and confirmed the agent's wrong lead 6/6.
Told the project, the measured symptom and that most files are innocent, it said yes about 20% of innocent files,
rejected the wrong lead 6/6, and still named the Q19 bug 16/20.
"""

import csv
import json
import logging
import re
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path

import requests
from jinja2 import StrictUndefined, Template

from minisweagent.agents.interactive import InteractiveAgent, InteractiveAgentConfig

logger = logging.getLogger("perf_agent")

SOURCE_FILE = re.compile(r"(?<![\w.-])((?:[\w.-]+/)*[\w-]+\.(?:cpp|cc|cxx|c|hpp|hh|hxx|h|inl))(?![\w.])")
# `make -C build/reldebug ...` (DuckDB), and `cd build && make` or `make -C build` (CPython's out-of-tree build).
REBUILD = re.compile(
    r"(?:^|[;&|(\n]\s*)(?:(?:time|nice|timeout\s+\S+)\s+)*make\b[^;&|]*\bbuild\b|\bcd\s+\S*build\S*\s*&&\s*make\b|\bcmake\s+--build\b|\bninja\b"
)
VIEW_RANGE = re.compile(r"sed\s+-n\s+['\"]?(\d+),(\d+)p")
HEAD_RANGE = re.compile(r"\bhead\s+-(?:n\s*)?(\d+)\b")
DIFF_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? ", re.M)
DIFF_FILE = re.compile(r"^diff --git a/(\S+) b/", re.M)
# One perf-compare row: benchmark, A ms, B ms, change, verdict.
COMPARE_ROW = re.compile(r"^(\S+?)(?:\.benchmark)?\s+([\d.]+)\s+([\d.]+)\s+[+-][\d.]+%\s+(.+)$", re.M)
COMPARE_NOISE = 0.08  # perf-compare's per-query noise floor

QUESTION = """You are reviewing one file of {{ project or "a program" }} during a hunt for a performance regression.
{% if symptom %}
Symptom (measured against the baseline): {{ symptom }}
{% endif %}
The engineer has spent the most time in {{ path }}{{ excerpt }}, at {{ location }}.

Most files read during a hunt like this are innocent. "Nothing here explains it" is a common, useful answer.

1. Could code in this file cause this symptom? Answer yes or no first.
2. If yes: the lines, the mechanism, and the cheapest experiment that would confirm it.
3. If no: what kind of code elsewhere would produce this symptom, and what to search for to find it.

=== {{ path }} ===
{{ code }}
"""

HANDBACK = """Fresh look (automatic: you {{ reason }}). A separate reviewer that has not seen your work, your theories or \
this conversation was shown only `{{ path }}` at {{ location }}{% if symptom %} and the measured symptom{% endif %}, \
and asked whether this file could cause the regression. Its answer:

<fresh_look>
{{ answer }}
</fresh_look>

This is a lead, not a fact. If it names a cause, test it next: make the smallest change that would show whether it is \
right, rebuild, measure, and run the correctness checks. Keep it only if the benchmark confirms it. If it says the \
file is innocent, weigh that against your own evidence before spending more steps here."""

HANDOFF_REQUEST = """Context reset (automatic, every {{ n }} steps). Your conversation is about to be cleared. The \
repository, its build and your scratch files stay as they are. A fresh session continues from a note you write now and \
sees nothing else from this conversation.

The tree as you leave it is what the next session starts from. If a change you measured as a fix is not applied and \
built right now, make that your one command instead: apply it and rebuild (`... && make -C build -j`), and the request \
comes again next step. Otherwise, in this one step, write the note to `$TMPDIR/HANDOFF.md` with a single heredoc \
command, and do nothing else. In it:
1. Measurements: the slow workloads against the baseline, first and latest (numbers).
2. Fixes: for each, file, function, what changed, before and after numbers, and KEPT (applied and built now) or \
REVERTED. A fix that measured better than the baseline is KEPT, not "experimental".
3. Open suspects: file and function, the measurement or profile line that points there, and the next experiment.
4. Ruled out: each lead with the measurement that ruled it out. A lead you set aside by reasoning, or because the code \
looks like the upstream you remember, is not ruled out: list it under open suspects.
Keep it under 60 lines."""

RESUME = """You are continuing a performance investigation: session {{ k }}. Each session gets {{ n }} steps, then a \
fresh context. The repository already contains the earlier sessions' changes (`git diff` shows them) and the build \
reflects them. `$TMPDIR/before`, if it exists, is the tree from before any change: keep it as the build to compare \
every fix against (`perf-compare $TMPDIR/before/build/python.exe build/python.exe`), and never overwrite it.

The note the previous session left:

<handoff>
{{ note }}
</handoff>

Start with the first "next experiment" in its open suspects: make that change, rebuild and measure it now. Do not \
re-read the code or re-run the suite first; re-measure only what that experiment needs. Its "ruled out" items are \
settled only where it gives a measurement. Fixes marked KEPT stay: undo one only if a measurement in this session \
shows it does not help."""

REASONS = {
    "visits": "have read `{path}` in {n} steps without changing it",
    "failed": "changed `{path}` and the next perf-compare showed nothing getting faster",
}


def read_times(path: Path) -> dict[str, float]:
    """`name,ms` rows of a CSV, keyed by lower-case name; {} if the file is missing."""
    rows = csv.reader(path.read_text().splitlines()) if path.is_file() else []
    return {name.lower(): float(ms) for name, ms in rows if ms.replace(".", "", 1).isdigit()}


def render(template: str, **kwargs) -> str:
    return Template(template, undefined=StrictUndefined, keep_trailing_newline=True).render(**kwargs)


class PerfAgentConfig(InteractiveAgentConfig):
    fresh_look_visits: int = 2
    """Fresh look at a file named in this many steps while the agent has not changed it. 0 turns fresh looks off."""
    fresh_look_failed_tries: int = 1
    """Fresh look at a changed file after this many perf-compare runs with nothing faster. 0 turns this trigger off."""
    fresh_look_max: int = 12
    """At most this many fresh looks per run."""
    fresh_look_project: str = ""
    """What the program is, for the reviewer, e.g. "DuckDB, an analytical SQL database engine"."""
    fresh_look_baseline: str = ""
    """CSV in the repository with `name,ms` baseline timings; the symptom is the latest measured time over it."""
    fresh_look_measured: str = ""
    """CSV with `name,ms` timings of the planted build, measured by the harness before the run (scripts/measure_golden.sh),
    so the symptom is known before the agent's first perf-compare."""
    fresh_look_slow_ratio: float = 1.25
    """A benchmark counts as slow in the symptom at this many times its baseline time."""
    fresh_look_deadline_seconds: int = 600
    """Cap on the whole fresh-look request. OpenRouter keep-alive padding defeats a socket timeout; one hung 17 min."""
    fresh_look_max_lines: int = 2500
    """Larger files are sent as an excerpt around the location of interest."""
    episode_steps: int = 0
    """Clear the context every this many steps, carrying over only a handoff note. 0 turns episodes off."""


class PerfAgent(InteractiveAgent):
    def __init__(self, *args, config_class=PerfAgentConfig, **kwargs):
        super().__init__(*args, config_class=config_class, **kwargs)
        self.files: dict[str, Counter] = {}
        self.viewed: dict[str, tuple[int, int]] = {}
        self.measured = read_times(Path(self.config.fresh_look_measured)) if self.config.fresh_look_measured else {}
        self.fresh_looks: list[dict] = []
        self._diffs: dict[str, str] = {}
        self._tracked: set[str] | None = None
        self._baseline: dict[str, float] | None = None
        self._pending: list[tuple[dict, threading.Thread, dict]] = []
        self.episodes: list[list[dict]] = []
        self.handoffs: list[dict] = []
        self._next_reset = self.config.episode_steps
        self._handoff_asks = 0

    def query(self) -> dict:
        limit = self.config.step_limit
        if 0 < self._next_reset <= self.n_calls and not 0 < limit <= self.n_calls:
            self._handoff_asks += 1
            content = render(HANDOFF_REQUEST, n=self.config.episode_steps)
            self.add_messages(self.model.format_message(role="user", content=content, extra={"handoff_request": True}))
        return super().query()

    def execute_actions(self, message: dict) -> list[dict]:
        result = super().execute_actions(message)
        if self.config.fresh_look_visits > 0:
            commands = [action.get("command", "") for action in message.get("extra", {}).get("actions", [])]
            outputs = [m.get("extra", {}).get("raw_output", "") for m in result]
            self._track(commands, outputs)
            result += self._collect()
        # The agent may first re-apply a fix it measured (the request asks it to); reset once the note is written.
        commands = [action.get("command", "") for action in message.get("extra", {}).get("actions", [])]
        if self._handoff_asks and (any("HANDOFF.md" in c for c in commands) or self._handoff_asks >= 3):
            self._new_episode()
        return result

    def _new_episode(self) -> None:
        note = self.env.execute({"command": 'cat "$TMPDIR/HANDOFF.md"'}).get("output", "").strip()
        self.handoffs.append({"step": self.n_calls, "asks": self._handoff_asks, "note": note})
        self.episodes.append(self.messages)
        self.messages = self.messages[:2]
        content = render(RESUME, k=len(self.episodes) + 1, n=self.config.episode_steps, note=note or "(none was written)")
        self.add_messages(self.model.format_message(role="user", content=content, extra={"episode": len(self.episodes) + 1}))
        self._next_reset = self.n_calls + self.config.episode_steps
        self._handoff_asks = 0

    # --- what the agent is circling ---

    def _repo(self) -> Path:
        return Path(self.env.config.cwd or ".").resolve()

    def _git(self, *args: str) -> str:
        out = subprocess.run(["git", "-C", str(self._repo()), *args], capture_output=True, text=True, timeout=60)
        return out.stdout if out.returncode == 0 else ""

    def _resolve(self, ref: str) -> str | None:
        """Map a path as written in a command to a tracked file, or None."""
        if self._tracked is None:
            self._tracked = set(self._git("ls-files").splitlines())
        repo = str(self._repo()) + "/"
        ref = ref.removeprefix(repo).removeprefix("./")
        if ref in self._tracked:
            return ref
        matches = [f for f in self._tracked if f == ref or f.endswith("/" + ref)]
        return matches[0] if len(matches) == 1 else None

    def _file(self, path: str) -> Counter:
        return self.files.setdefault(path, Counter(first_step=self.n_calls))

    def _track(self, commands: list[str], outputs: list[str]) -> None:
        step_files = set()
        for command in commands:
            files = {f for ref in SOURCE_FILE.findall(command) if (f := self._resolve(ref))}
            step_files |= files
            if len(files) == 1 and (m := VIEW_RANGE.search(command) or HEAD_RANGE.search(command)):
                lo, hi = (int(m[1]), int(m[2])) if m.re is VIEW_RANGE else (1, int(m[1]))
                self.viewed[next(iter(files))] = (lo, hi)
        diff = self._git("diff", "HEAD")
        diffs = dict(zip(DIFF_FILE.findall(diff), re.split(r"^diff --git ", diff, flags=re.M)[1:]))
        for path in set(diffs) | set(self._diffs):
            if diffs.get(path) != self._diffs.get(path):
                self._file(path).update(edits=1, failed=-self._file(path)["failed"])
        self._diffs = diffs
        for path in step_files:
            self._file(path).update(visits=1)
        if any(REBUILD.search(c) for c in commands):
            for path in diffs:
                self._file(path).update(rebuilds=1)
        for command, output in zip(commands, outputs):
            if "perf-compare" in command and (rows := COMPARE_ROW.findall(output)):
                # Agents keep comparing against the original build, so after one fix every compare shows something
                # faster. A try failed if nothing got faster than the last time each query was measured.
                now = {Path(name).name.lower(): float(b) for name, _, b, _ in rows if "suite" not in name}
                before = {q: self.measured[q] for q in now if q in self.measured}
                if before:
                    improved = any(now[q] < (1 - COMPARE_NOISE) * ms for q, ms in before.items())
                else:
                    improved = any(v.strip().startswith("faster") for name, _, _, v in rows if "suite" not in name)
                self.measured |= now
                if not improved:
                    for path in diffs:
                        self._file(path).update(failed=1)
        if not self._pending and len(self.fresh_looks) < self.config.fresh_look_max:
            if trigger := self._trigger(step_files, diffs):
                self._fresh_look(*trigger)

    def _trigger(self, step_files: set[str], diffs: dict[str, str]) -> tuple[str, str] | None:
        sent = {r["path"] for r in self.fresh_looks}
        if self.config.fresh_look_failed_tries > 0:
            failed = [
                p for p in diffs if p not in sent and self.files[p]["failed"] >= self.config.fresh_look_failed_tries
            ]
            if failed:
                return max(failed, key=lambda p: self.files[p]["visits"]), "failed"
        circling = [
            p
            for p in step_files
            if p not in sent and p not in diffs and self.files[p]["visits"] >= self.config.fresh_look_visits
        ]
        if circling:
            return max(circling, key=lambda p: self.files[p]["visits"]), "visits"
        return None

    def _location(self, path: str) -> tuple[int, int] | None:
        """The lines the agent changed, else the lines it last looked at. Edits come first: a last look is often a
        glance (run 2 read lines 1-12 of the file it had been editing for 30 steps, to check its includes)."""
        hunks = [(int(s), int(n or 1)) for s, n in DIFF_HUNK.findall(self._git("diff", "-U0", "HEAD", "--", path))]
        if hunks:
            return min(s for s, _ in hunks), max(s + max(n, 1) - 1 for s, n in hunks)
        return self.viewed.get(path)

    def _symptom(self) -> str:
        """The latest measured time of each query over its baseline, from the agent's own perf-compare runs."""
        if self._baseline is None:
            self._baseline = (
                read_times(self._repo() / self.config.fresh_look_baseline) if self.config.fresh_look_baseline else {}
            )
        ratios = {q: ms / self._baseline[q] for q, ms in self.measured.items() if self._baseline.get(q)}
        slow = sorted(
            (q for q, r in ratios.items() if r >= self.config.fresh_look_slow_ratio), key=lambda q: -ratios[q]
        )
        if not slow:
            return ""
        text = ", ".join(f"{q.upper()} {ratios[q]:.1f}x" for q in slow) + " slower than baseline"
        if rest := len(ratios) - len(slow):
            text += (
                f"; the other {rest} measured benchmarks are within {self.config.fresh_look_slow_ratio - 1:.0%} of it"
            )
        return text

    # --- the fresh look ---

    def _fresh_look(self, path: str, why: str) -> None:
        lines = (self._git("show", f"HEAD:{path}") or "").splitlines()
        span = self._location(path)
        location = f"lines {span[0]}-{span[1]}" if span else "the whole file"
        lo, hi, excerpt = 1, len(lines), ""
        if len(lines) > self.config.fresh_look_max_lines:
            mid = (span[0] + span[1]) // 2 if span else 1
            half = self.config.fresh_look_max_lines // 2
            lo, hi = max(1, mid - half), min(len(lines), mid + half)
            excerpt = f" (excerpt: lines {lo}-{hi} of {len(lines)})"
        code = "\n".join(f"{i:6}\t{lines[i - 1]}" for i in range(lo, hi + 1))
        symptom = self._symptom()
        prompt = render(
            QUESTION,
            project=self.config.fresh_look_project,
            symptom=symptom,
            path=path,
            excerpt=excerpt,
            location=location,
            code=code,
        )
        record = {
            "step": self.n_calls,
            "trigger": why,
            "path": path,
            "location": location,
            "symptom": symptom,
            "file": dict(self.files[path]),
            "prompt": prompt,
        }
        self.fresh_looks.append(record)
        self._start(record)

    def _start(self, record: dict) -> None:
        """Ask in the background; `_collect` hands the answer back once it is in."""
        box: dict = {}

        def call():
            try:
                box["answer"], box["cost"] = self._ask(record["prompt"])
            except Exception as e:
                box["error"] = e

        worker = threading.Thread(target=call, daemon=True)
        record["_started"] = time.monotonic()
        worker.start()
        self._pending.append((record, worker, box))

    def _collect(self) -> list[dict]:
        messages, waiting = [], []
        for record, worker, box in self._pending:
            if worker.is_alive():
                if time.monotonic() - record["_started"] < self.config.fresh_look_deadline_seconds:
                    waiting.append((record, worker, box))
                else:
                    record["error"] = f"no answer within {self.config.fresh_look_deadline_seconds} s"
                continue
            if "error" in box:
                logger.warning("fresh look failed: %r", box["error"])
                record["error"] = repr(box["error"])
                continue
            self.cost += box["cost"]
            record |= {"answer": box["answer"], "cost": box["cost"], "answered_step": self.n_calls}
            reason = REASONS[record["trigger"]].format(path=record["path"], n=record["file"]["visits"])
            content = render(
                HANDBACK,
                reason=reason,
                path=record["path"],
                location=record["location"],
                symptom=record["symptom"],
                answer=box["answer"].strip(),
            )
            messages += self.add_messages(
                self.model.format_message(role="user", content=content, extra={"fresh_look": record})
            )
        self._pending = waiting
        return messages

    def _ask(self, prompt: str) -> tuple[str, float]:
        """One request to the run's model with no tools and no history. Raises on error."""
        url, key = getattr(self.model, "_api_url", None), getattr(self.model, "_api_key", None)
        if not url:
            raise RuntimeError(f"fresh look needs an OpenRouter model, not {type(self.model).__name__}")
        payload = {
            "model": self.model.config.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "usage": {"include": True},
            **self.model.config.model_kwargs,
        }
        started = time.monotonic()
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=60,
        )
        r.raise_for_status()
        response = r.json()
        logger.info("fresh look answered in %.0f s", time.monotonic() - started)
        return response["choices"][0]["message"].get("content") or "", response.get("usage", {}).get("cost", 0.0)

    def serialize(self, *extra_dicts) -> dict:
        looks = [{k: v for k, v in r.items() if k not in ("prompt", "_started")} for r in self.fresh_looks]
        digest = {"fresh_looks": looks, "files": {path: dict(c) for path, c in self.files.items()}, "handoffs": self.handoffs}
        data = super().serialize({"info": digest}, *extra_dicts)
        data["messages"] = [m for episode in self.episodes for m in episode] + self.messages
        return data
