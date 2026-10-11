"""Performance agent: the interactive agent plus a fresh look for when it circles on one problem.

Every `fresh_look_rebuilds` rebuilds of changed source, the file the agent has worked on most since the last fresh look
is sent to the same model in a separate request: no run history, no profiles, only that file, the lines the agent
looked at, and one question. The answer goes back to the agent as a lead to test with the benchmark.

Epoch 11 (LOGS/cold_check_q19_matrix): asked this way about the Q19 bug file, deepseek-v4.1-flash named the planted
line 3/3. With the Q19 profiles attached it dropped to 1/3: it trusted the profile, which shows where time goes, not
what caused it. So the request carries no profiles.
"""

import hashlib
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
REBUILD = re.compile(r"\bmake\b[^;&|]*\bbuild/|\bcmake\s+--build\b|\bninja\b")
VIEW_RANGE = re.compile(r"sed\s+-n\s+['\"]?(\d+),(\d+)p")
HEAD_RANGE = re.compile(r"\bhead\s+-(?:n\s*)?(\d+)\b")
DIFF_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? ", re.M)

QUESTION = """Some workloads of this program run much slower than their baseline: a performance regression. Below is \
the source file {{ path }} with line numbers{{ excerpt }}.

Location of interest: {{ location }}.

Is the regression caused by code at this location? If yes, give the line numbers and explain why. If not, say what \
the real cause is likely to be and where you would look next.

=== {{ path }} ===
{{ code }}
"""

HANDBACK = """Fresh look (automatic, after {{ rebuilds }} rebuilds of changed code). A separate reviewer that has not seen \
your work, your measurements or this conversation was shown only `{{ path }}` and asked whether the regression is caused \
at {{ location }}. Its answer:

<fresh_look>
{{ answer }}
</fresh_look>

This is a lead, not a fact. Test it next: make the smallest change that would show whether it is right, rebuild, \
measure, and run the correctness checks. Keep it only if the benchmark confirms it; otherwise revert and carry on."""


def render(template: str, **kwargs) -> str:
    return Template(template, undefined=StrictUndefined, keep_trailing_newline=True).render(**kwargs)


class PerfAgentConfig(InteractiveAgentConfig):
    fresh_look_rebuilds: int = 5
    """Ask for a fresh look after this many rebuilds of changed source since the last one. 0 turns it off."""
    fresh_look_deadline_seconds: int = 600
    """Cap on the whole fresh-look request. OpenRouter keep-alive padding defeats a socket timeout; one hung 17 min."""
    fresh_look_max_lines: int = 2500
    """Larger files are sent as an excerpt around the location of interest."""


class PerfAgent(InteractiveAgent):
    def __init__(self, *args, config_class=PerfAgentConfig, **kwargs):
        super().__init__(*args, config_class=config_class, **kwargs)
        self.rebuilds = 0
        self.touched: Counter[str] = Counter()
        self.viewed: dict[str, tuple[int, int]] = {}
        self.sent: set[str] = set()
        self.fresh_looks: list[dict] = []
        self._last_diff: str | None = None
        self._tracked: set[str] | None = None

    def execute_actions(self, message: dict) -> list[dict]:
        result = super().execute_actions(message)
        if self.config.fresh_look_rebuilds > 0:
            commands = [action.get("command", "") for action in message.get("extra", {}).get("actions", [])]
            self._track(commands)
            if self.rebuilds >= self.config.fresh_look_rebuilds:
                result += self._fresh_look()
        return result

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

    def _track(self, commands: list[str]) -> None:
        step_files = set()
        for command in commands:
            files = {f for ref in SOURCE_FILE.findall(command) if (f := self._resolve(ref))}
            step_files |= files
            if len(files) == 1 and (m := VIEW_RANGE.search(command) or HEAD_RANGE.search(command)):
                lo, hi = (int(m[1]), int(m[2])) if m.re is VIEW_RANGE else (1, int(m[1]))
                self.viewed[files.pop()] = (lo, hi)
            if REBUILD.search(command):
                diff = self._git("diff", "HEAD")
                if diff and diff != self._last_diff:
                    self.rebuilds += 1
                self._last_diff = diff
        self.touched.update(step_files)

    def _location(self, path: str) -> tuple[int, int] | None:
        """The lines the agent changed, else the lines it last looked at. Edits come first: a last look is often a
        glance (run 2 read lines 1-12 of the file it had been editing for 30 steps, to check its includes)."""
        hunks = [(int(s), int(n or 1)) for s, n in DIFF_HUNK.findall(self._git("diff", "-U0", "HEAD", "--", path))]
        if hunks:
            return min(s for s, _ in hunks), max(s + max(n, 1) - 1 for s, n in hunks)
        return self.viewed.get(path)

    # --- the fresh look ---

    def _fresh_look(self) -> list[dict]:
        rebuilds, self.rebuilds = self.rebuilds, 0
        candidates = [f for f, _ in self.touched.most_common() if f not in self.sent]
        self.touched.clear()
        if not candidates:
            return []
        path = candidates[0]
        self.sent.add(path)
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
        prompt = render(QUESTION, path=path, excerpt=excerpt, location=location, code=code)
        record = {"step": self.n_calls, "rebuilds": rebuilds, "path": path, "location": location, "prompt": prompt}
        self.fresh_looks.append(record)
        try:
            answer, cost = self._ask(prompt)
        except Exception as e:
            logger.warning("fresh look failed: %r", e)
            record["error"] = repr(e)
            return []
        self.cost += cost
        record |= {"answer": answer, "cost": cost}
        content = render(HANDBACK, rebuilds=rebuilds, path=path, location=location, answer=answer.strip())
        return self.add_messages(
            self.model.format_message(role="user", content=content, extra={"fresh_look": record})
        )

    def _ask(self, prompt: str) -> tuple[str, float]:
        """One request to the run's model with no tools and no history. Raises on error or past the deadline."""
        url, key = getattr(self.model, "_api_url", None), getattr(self.model, "_api_key", None)
        if not url:
            raise RuntimeError(f"fresh look needs an OpenRouter model, not {type(self.model).__name__}")
        payload = {
            "model": self.model.config.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "usage": {"include": True},
            **self.model.config.model_kwargs,
        }
        box: dict = {}

        def call():
            try:
                r = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    data=json.dumps(payload),
                    timeout=60,
                )
                r.raise_for_status()
                box["response"] = r.json()
            except Exception as e:
                box["error"] = e

        # A daemon thread, so a request past the deadline is abandoned rather than holding up the run.
        worker = threading.Thread(target=call, daemon=True)
        started = time.monotonic()
        worker.start()
        worker.join(self.config.fresh_look_deadline_seconds)
        if worker.is_alive():
            raise TimeoutError(f"no answer within {self.config.fresh_look_deadline_seconds} s")
        if "error" in box:
            raise box["error"]
        response = box["response"]
        answer = response["choices"][0]["message"].get("content") or ""
        logger.info("fresh look answered in %.0f s", time.monotonic() - started)
        return answer, response.get("usage", {}).get("cost", 0.0)

    def serialize(self, *extra_dicts) -> dict:
        digest = {"fresh_looks": [{k: v for k, v in r.items() if k != "prompt"} for r in self.fresh_looks]}
        return super().serialize({"info": digest}, *extra_dicts)
