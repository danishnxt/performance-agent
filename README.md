# performance-agent

**An agent that finds and fixes performance bugs in compiled code the way a performance engineer does: it measures, profiles, forms a hypothesis, tests it, and keeps only fixes that a measurement backs up.** The goal is for small, cheap models to do this well enough to run unattended, overnight, across whole codebases.

A fork of [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) that adds a performance workflow, profiling tools the agent can call, long-run handling, and a sandbox.

## Results

We plant known slowdowns in real software and check whether the agent fixes them. Every run uses the same cheap model, `deepseek-v4.1-flash`, with a $3 and 200-step cap. "Plain" is stock mini-swe-agent with the same model. We grade each final patch ourselves: rebuilt from a clean copy, timed against an unplanted build, and run through the project's tests.

### DuckDB: all 3 bugs fixed in every run

| | Plain mini-swe-agent | performance-agent |
|---|---|---|
| Bugs fixed (of 3) | 2, 2, 0 | **3, 3, 3** |
| How runs ended | all 3 hit the 200-step cap | submitted on their own |
| TPC-H suite after the patch | 7% slower than unplanted | back to unplanted speed |

- **Cheap.** A full run that fixed all 3 bugs cost $0.18 and took about an hour.
- **It solves the bug a profiler can't point at.** That bug is a bad runtime decision whose cost lands elsewhere. Only performance-agent fixed it.
- **Its fixes match the real ones.** In the run we checked byte for byte, every fix restored the original upstream code exactly.

![Cumulative confirmed DuckDB fixes by agent step](docs/perf/duckdb-fix-progress.png)

performance-agent runs (blue, green) fixed the first bug by step 17 to 31; plain runs (red) took 78 to 108 steps.

### CPython: a harder target

We undid 4 real upstream performance fixes in CPython and added 1 synthetic bug. Some slowdowns are only 7-10%.

| | Plain mini-swe-agent | performance-agent |
|---|---|---|
| Bugs fixed (of 5) | 2 (1 run) | 2, 3, 3 |

- **Only performance-agent found the synthetic bug** (a string hash that is no longer cached), using `perf-sample --callers` to trace a hot memory copy to its caller.
- **Its float fix is line for line the fix CPython shipped upstream.**

1 to 3 runs per setup, so these are early results, not benchmark numbers.

## How it works

- **Workflow** ([`workflow.md`](src/minisweagent/config/perf/workflow.md)): a performance engineer's order of work, from measuring to keeping only measured fixes.
- **Tools** ([`tools/`](src/minisweagent/config/perf/tools)): `perf-profile` (per-operator query profile), `perf-sample` (CPU profile, with `--callers` and `--compare`), `perf-compare` (A/B timing of two builds, beyond noise or not).
- **Episodes** ([`perf.py`](src/minisweagent/agents/perf.py)): every N steps the agent writes a handoff note, its context is cleared, and a new session starts from the note.
- **Sandbox** ([`sandboxed_local.py`](src/minisweagent/environments/sandboxed_local.py)): each command runs under a macOS `sandbox-exec` profile. Ours blocks the network and everything outside the run's copy, so the agent can't copy an answer instead of finding it.

## Use it

```sh
pip install -e .
python src/minisweagent/config/perf/build_config.py perf.yaml --tools duckdb --episode-steps 50
mini -c perf.yaml --model deepseek/deepseek-v4.1-flash --model-class openrouter --task "This build of DuckDB got slower on TPC-H. Find out why and fix it."
```

- `--tools NAME` puts `src/minisweagent/config/perf/tools/NAME` on the agent's PATH (`duckdb`, `cpython`).
- `--guide NAME` adds a domain guide from `perf/guides/`.
- To sandbox commands (macOS), add `-c environment.environment_class=minisweagent.environments.sandboxed_local.SandboxedLocalEnvironment -c environment.profile=YOUR.sb` with your own `sandbox-exec` profile.

Everything else (models, environments, configs): [mini-swe-agent docs](https://mini-swe-agent.com/latest/).

## Credit and license

Built on [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) by the SWE-bench / SWE-agent team. MIT licensed, as is the original; see [LICENSE.md](LICENSE.md).
