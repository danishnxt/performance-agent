import os
import shlex
from typing import Any

from minisweagent.environments.local import LocalEnvironment, LocalEnvironmentConfig


class SandboxedLocalEnvironmentConfig(LocalEnvironmentConfig):
    profile: str
    """macOS sandbox-exec profile applied to every command."""
    keep_env: list[str] = [
        *("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "TERM", "USER", "LOGNAME", "SHELL"),
        *("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "PAGER", "MANPAGER", "LESS", "PIP_PROGRESS_BAR", "TQDM_DISABLE"),
    ]
    """The only variables a command sees; everything else is dropped. The harness runs inside other tools whose
    variables name the experiment, its folders, sessions and sockets (epoch 17 found a Claude Code session token in
    the agent's env), so an allowlist, not a blocklist."""


class SandboxedLocalEnvironment(LocalEnvironment):
    """Like LocalEnvironment, but each command runs under sandbox-exec with a minimal environment. The agent process
    itself is not sandboxed, so it can still reach the model API while the commands cannot."""

    def __init__(self, *, config_class: type = SandboxedLocalEnvironmentConfig, **kwargs):
        super().__init__(config_class=config_class, **kwargs)

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        merged = os.environ | self.config.env
        keep = " ".join(shlex.quote(f"{k}={merged[k]}") for k in self.config.keep_env if k in merged)
        command = f"/usr/bin/env -i {keep} /usr/bin/sandbox-exec -f {shlex.quote(self.config.profile)} /bin/sh -c {shlex.quote(action.get('command', ''))}"
        return super().execute({**action, "command": command}, cwd, timeout=timeout)
