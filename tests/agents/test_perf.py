import subprocess
from pathlib import Path

import pytest
import yaml

from minisweagent.agents.perf import REBUILD, PerfAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicModel, make_output


class OfflinePerfAgent(PerfAgent):
    """Answers fresh looks locally, and at once, so the answer is handed back in the step that asked."""

    def _ask(self, prompt: str) -> tuple[str, float]:
        return f"reviewed {len(prompt)} chars", 0.5

    def _start(self, record: dict) -> None:
        super()._start(record)
        self._pending[-1][1].join()


COMPARE = "#!/bin/sh\nprintf 'benchmark A ms B ms change verdict\\n'\ncat rows.txt\n"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.cpp").write_text("".join(f"int a{i};\n" for i in range(1, 11)))
    (tmp_path / "src" / "b.cpp").write_text("int b;\n")
    (tmp_path / "BASELINE.csv").write_text("query,ms\nq19,25.0\nq06,12.0\nq01,50.0\n")
    (tmp_path / "perf-compare").write_text(COMPARE)
    (tmp_path / "perf-compare").chmod(0o755)
    for cmd in (["init", "-q"], ["add", "src"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(tmp_path), *cmd], check=True)
    return tmp_path


def run_agent(repo: Path, commands: list[str], **config) -> PerfAgent:
    agent_config = yaml.safe_load(Path("src/minisweagent/config/default.yaml").read_text())["agent"]
    steps = [make_output(f"step {i}", [{"command": c}]) for i, c in enumerate(commands)]
    steps.append(make_output("done", [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}]))
    agent = OfflinePerfAgent(
        DeterministicModel(outputs=steps),
        LocalEnvironment(cwd=str(repo)),
        **(agent_config | {"mode": "yolo", "confirm_exit": False} | config),
    )
    agent.run("find the regression")
    return agent


def fresh_records(agent: PerfAgent) -> list[dict]:
    return [m["extra"]["fresh_look"] for m in agent.messages if "fresh_look" in m.get("extra", {})]


def compare(*rows: str) -> str:
    """A perf-compare call printing these `name A B change verdict` rows."""
    return "printf '" + "\\n".join(rows).replace("%", "%%") + "\\n' > rows.txt && ./perf-compare"


def test_fresh_look_on_a_file_read_twice_without_changing_it(repo):
    agent = run_agent(repo, ["sed -n '3,5p' src/a.cpp", "cat src/b.cpp", "grep -n a3 src/a.cpp"])
    (record,) = fresh_records(agent)
    assert (record["path"], record["trigger"], record["location"], record["step"]) == (
        "src/a.cpp",
        "visits",
        "lines 3-5",
        3,
    )
    assert "     3\tint a3;" in record["prompt"] and "of a program" in record["prompt"]
    assert "Symptom" not in record["prompt"] and "Profile" not in record["prompt"]
    handback = next(m for m in agent.messages if "fresh_look" in m.get("extra", {}))["content"]
    assert "read `src/a.cpp` in 2 steps" in handback and "reviewed" in handback
    assert agent.cost == 4 * 1.0 + 0.5
    info = agent.serialize()["info"]
    assert info["fresh_looks"][0]["answer"].startswith("reviewed") and "prompt" not in info["fresh_looks"][0]
    assert info["files"]["src/a.cpp"]["visits"] == 2 and info["files"]["src/b.cpp"]["visits"] == 1


def test_a_changed_file_is_not_circling_but_a_failed_try_is(repo):
    agent = run_agent(
        repo,
        [
            "cat src/a.cpp",
            "echo 'int x;' >> src/a.cpp && grep -c int src/a.cpp",  # second visit, but the file is changed now
            compare("q19 60.0 59.0 -1.7% no change beyond noise", "q01 50.0 50.0 +0.0% no change beyond noise"),
        ],
        fresh_look_project="DuckDB, an analytical SQL database engine",
        fresh_look_baseline="BASELINE.csv",
    )
    (record,) = fresh_records(agent)
    assert (record["path"], record["trigger"], record["location"], record["step"]) == (
        "src/a.cpp",
        "failed",
        "lines 10-10",
        3,
    )
    assert record["symptom"] == "Q19 2.4x slower than baseline; the other 1 measured benchmarks are within 25% of it"
    assert "of DuckDB, an analytical SQL database engine" in record["prompt"] and record["symptom"] in record["prompt"]
    assert agent.files["src/a.cpp"]["edits"] == 1


def test_a_try_fails_only_if_nothing_got_faster_than_last_measured(repo):
    first = compare("q19 64.0 30.0 -53.1% faster")  # compared against the original build: faster, not a failure
    agent = run_agent(
        repo,
        [
            "echo 'int x;' >> src/a.cpp && " + first,
            "echo 'int y;' >> src/b.cpp && " + compare("q19 64.0 20.0 -68.8% faster"),  # faster again: not a failure
            "echo 'int z;' >> src/b.cpp && " + compare("q19 64.0 20.5 -68.0% faster"),  # same as last time: a failure
        ],
    )
    assert [(r["path"], r["trigger"], r["step"]) for r in fresh_records(agent)] == [("src/b.cpp", "failed", 3)]


def test_symptom_known_from_the_harness_measurement_before_any_perf_compare(repo, tmp_path_factory):
    measured = tmp_path_factory.mktemp("golden") / "duckdb.measured.csv"
    measured.write_text("name,ms\nq19,65.0\nq06,18.0\nq01,52.0\n")
    agent = run_agent(
        repo,
        ["cat src/a.cpp", "cat src/a.cpp", "cat src/b.cpp", compare("q19 65.0 25.0 -61.5% faster"), "cat src/b.cpp"],
        fresh_look_baseline="BASELINE.csv",
        fresh_look_measured=str(measured),
    )
    assert [r["symptom"] for r in fresh_records(agent)] == [
        "Q19 2.6x, Q06 1.5x slower than baseline; the other 1 measured benchmarks are within 25% of it",
        "Q06 1.5x slower than baseline; the other 2 measured benchmarks are within 25% of it",  # the agent fixed Q19
    ]


def test_one_fresh_look_per_file(repo):
    agent = run_agent(repo, ["cat src/a.cpp", "cat src/a.cpp", "cat src/a.cpp src/b.cpp", "cat src/b.cpp"])
    assert [(r["path"], r["step"]) for r in fresh_records(agent)] == [("src/a.cpp", 2), ("src/b.cpp", 4)]


def test_episodes_clear_the_context_and_carry_the_handoff_note(repo, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(repo))
    note = "cat > \"$TMPDIR/HANDOFF.md\" <<'X'\nsuspect: src/a.cpp\nX"
    agent = run_agent(repo, ["echo one", "echo two", note, "echo three"], episode_steps=2, fresh_look_visits=0)
    contents = [str(m.get("content", "")) for m in agent.messages]
    assert len(agent.episodes) == 1 and agent.handoffs == [{"step": 3, "note": "suspect: src/a.cpp"}]
    assert "suspect: src/a.cpp" in contents[2] and "step 0" not in contents and "step 3" in contents
    saved = [str(m.get("content", "")) for m in agent.serialize()["messages"]]
    assert len(saved) == len(agent.episodes[0]) + len(agent.messages) and "step 0" in saved

def test_fresh_look_off(repo):
    agent = run_agent(repo, ["cat src/a.cpp", "cat src/a.cpp"], fresh_look_visits=0)
    assert fresh_records(agent) == [] and agent.fresh_looks == [] and agent.files == {}


@pytest.mark.parametrize(
    ("command", "is_rebuild"),
    [
        ("make -C build/reldebug -j shell benchmark_runner", True),
        ("cd build && make -j", True),
        ("cd /repo/build && make -j15 2>&1 | tail", True),
        ("make -C build -j", True),
        ("cd /repo && time make -C build/reldebug shell", True),
        ("cmake --build build/release", True),
        ("cd /repo && timeout 280 make -C build/reldebug -j shell", True),
        ("cp a.bak src/a.cpp\nmake -C build/reldebug shell", True),
        ("make -j", False),
        ("grep -n make src/build.c", False),
    ],
)
def test_rebuild_commands(command, is_rebuild):
    assert bool(REBUILD.search(command)) is is_rebuild
