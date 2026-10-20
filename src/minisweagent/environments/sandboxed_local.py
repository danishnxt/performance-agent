import shlex
from typing import Any

from minisweagent.environments.local import LocalEnvironment, LocalEnvironmentConfig


class SandboxedLocalEnvironmentConfig(LocalEnvironmentConfig):
    profile: str
    """macOS sandbox-exec profile applied to every command."""
    unset_env: list[str] = [
        *("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LOCAL_API_KEY"),
        *("PYTHONPATH", "MINI_REPO", "WORKSPACE", "PERF", "TARGET", "MSWEA_CONFIGURED", "MSWEA_GLOBAL_CONFIG_DIR"),
    ]
    """Variables removed from each command's environment: the model's API keys, and the harness's own settings, which
    name the experiment and its folders (PYTHONPATH would also change what the target's Python imports)."""


class SandboxedLocalEnvironment(LocalEnvironment):
    """Like LocalEnvironment, but each command runs under sandbox-exec. The agent process itself is not sandboxed,
    so it can still reach the model API while the commands cannot."""

    def __init__(self, *, config_class: type = SandboxedLocalEnvironmentConfig, **kwargs):
        super().__init__(config_class=config_class, **kwargs)

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        unset = " ".join(f"-u {name}" for name in self.config.unset_env)
        command = f"env {unset} /usr/bin/sandbox-exec -f {shlex.quote(self.config.profile)} /bin/sh -c {shlex.quote(action.get('command', ''))}"
        return super().execute({**action, "command": command}, cwd, timeout=timeout)
