"""Shell command helpers for tmux-launched agent processes."""

import shlex


def wrap_with_shell_exports(command: str, env: dict[str, str] | None) -> str:
    if not env:
        return command
    exports = " ".join(f"export {key}={shlex.quote(value)};" for key, value in env.items())
    return f"{exports} {command}"
