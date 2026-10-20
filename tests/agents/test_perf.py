import subprocess
from pathlib import Path

import pytest
import yaml

from minisweagent.agents.perf import REBUILD, PerfAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicModel, make_output


class OfflinePerfAgent(PerfAgent):
    """Answers fresh looks locally instead of calling the API."""

    def _ask(self, prompt: str) -> tuple[str, float]:
        return f"reviewed {len(prompt)} chars", 0.5


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.cpp").write_text("".join(f"int a{i};\n" for i in range(1, 11)))
    (tmp_path / "src" / "b.cpp").write_text("int b;\n")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(tmp_path), *cmd], check=True)
    return tmp_path


def run_agent(repo: Path, commands: list[str], **config) -> PerfAgent:
    agent_config = yaml.safe_load(Path("src/minisweagent/config/default.yaml").read_text())["agent"]
    steps = [make_output(f"step {i}", [{"command": c}]) for i, c in enumerate(commands)]
    steps.append(make_output("done", [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}]))
    agent = OfflinePerfAgent(
        DeterministicModel(outputs=steps),
        LocalEnvironment(cwd=str(repo)),
        **(agent_config | {"mode": "yolo", "confirm_exit": False, "fresh_look_rebuilds": 2} | config),
    )
    agent.run("find the regression")
    return agent


def fresh_messages(agent: PerfAgent) -> list[dict]:
    return [m for m in agent.messages if "fresh_look" in m.get("extra", {})]


def test_fresh_look_after_rebuilds_of_changed_code(repo):
    agent = run_agent(
        repo,
        [
            "sed -n '3,5p' src/a.cpp",
            "grep -n a3 src/a.cpp && echo 'int x;' >> src/b.cpp && make -C build/reldebug shell",
            "make -C build/reldebug shell",  # nothing changed since the last rebuild: not a try
            "grep -n a4 src/a.cpp && echo 'int y;' >> src/b.cpp && make -C build/reldebug shell",
        ],
    )
    (message,) = fresh_messages(agent)
    record = message["extra"]["fresh_look"]
    assert (record["path"], record["location"], record["rebuilds"], record["step"]) == ("src/a.cpp", "lines 3-5", 2, 4)
    assert "     3\tint a3;" in record["prompt"]
    assert "Profile" not in record["prompt"]
    assert "Fresh look" in message["content"] and "reviewed" in message["content"]
    assert agent.cost == 5 * 1.0 + 0.5
    assert agent.serialize()["info"]["fresh_looks"][0]["path"] == "src/a.cpp"


def test_fresh_look_moves_to_next_file_and_uses_changed_lines(repo):
    edit_a = "echo 'int x{}' >> src/a.cpp && make -C build/reldebug shell"
    agent = run_agent(
        repo,
        [
            edit_a.format(1),
            edit_a.format(2),  # first fresh look: a.cpp, at the edit in the original numbering (appended after 10)
            "cat src/a.cpp src/b.cpp && " + edit_a.format(3),
            edit_a.format(4),  # second: a.cpp was already sent, so b.cpp
        ],
    )
    assert [(m["extra"]["fresh_look"]["path"], m["extra"]["fresh_look"]["location"]) for m in fresh_messages(agent)] == [
        ("src/a.cpp", "lines 10-10"),
        ("src/b.cpp", "the whole file"),
    ]


def test_fresh_look_off(repo):
    agent = run_agent(repo, ["echo 1 >> src/a.cpp && make -C build/x", "echo 2 >> src/a.cpp && make -C build/x"], fresh_look_rebuilds=0)
    assert fresh_messages(agent) == [] and agent.fresh_looks == []


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
