"""A tool that re-records the head GIF of the README (images/readme-demo.gif).

The GIF shows "`pip install doeff` -> look at the program file and the handler file -> run the same `digest` with 4 handlers, and
how a handler treats k (the continuation) changes the meaning and answer of an effect" -- canned, which answers a value and resumes k;
trace, which sees every effect and passes it on; budget(1), which lets one through then drops k and stops midway; and live_world, which answers with real HTTP
(the same 4 as `python -m doeff demo`). The script lives in one place, SCRIPT below, and the files recorded are digest.py and
handlers.py next to it (the files shown in the GIF themselves). Both are written the same way as the README examples (one blank line between definitions, aligned comments) and each fits on one screen.
handlers.py also imports `handler` and `run` used in the REPL -- ruff formatting is not applied (outside the scope of `make lint-ruff`).
Whether they run is checked by tests/test_readme_demo.py:

    uv run --no-project python tools/readme_demo/record.py                       # rewrite images/readme-demo.gif
    uv run --no-project python tools/readme_demo/record.py --font-dir <dir>      # when the machine has no monospace glyphs
    uv run --no-project python tools/readme_demo/record.py --cast demo.cast      # also keep the intermediate asciicast

How it records:

1. Copy digest.py and handlers.py into a temporary dir outside the repo, and make a new venv with pip using `uv venv --seed`.
2. In that dir, start bash inside a pseudo-terminal (pty) and type the script's commands one character at a time. The commands really run -- `pip install doeff` installs from
   PyPI, and `live_world` really fetches https://example.com. What appears on screen is the output of bash and Python itself.
3. Write the output timing as asciicast v2. The pauses between keystrokes and the script's "wait" are fixed lengths, and waits between command output (such as pip fetching) are
   shortened (treated the same as asciinema's idle time limit).
4. Make a GIF with agg (https://github.com/asciinema/agg). agg and uv are looked up in PATH.

The environment variables of this machine are not passed to bash and Python (HOME is a temporary dir, pip does not read config files). Paths and names
of this machine do not appear on screen. The size target of the GIF is up to 1 MB -- if exceeded, it is reported with exit code 1 (the GIF is still written).

Exit code: 0 if written and within the target, 1 if the target is exceeded, 2 if the tool cannot be used (no uv or agg, a command does not finish in time).
Uses only the standard library (runs without syncing the workspace).
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import pty
import random
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
from dataclasses import dataclass
from fcntl import ioctl
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# Files shown in the GIF -- the program digest.py, and handlers.py which imports it.
DEMO_FILES = (HERE / "digest.py", HERE / "handlers.py")
DEFAULT_OUT = ROOT / "images" / "readme-demo.gif"
SIZE_BUDGET_BYTES = 1_000_000

# Terminal size -- tall enough that the `cat handlers.py` command line, the file (29 lines), and the next prompt fit on one screen.
COLUMNS = 76
ROWS = 32
SHELL_PROMPT = r"\$ $"
PYTHON_PROMPT = r">>> $"


@dataclass(frozen=True)
class Type:
    """Type a command one character at a time, press Enter, and record the output until the prompt returns.

    gap_cap is the upper limit of waits between output (seconds) -- shortens long waits such as pip fetching.
    """

    text: str
    prompt: str
    gap_cap: float = 0.3


@dataclass(frozen=True)
class Hold:
    """Wait without typing (seconds) -- while the viewer reads the screen."""

    seconds: float


SCRIPT: tuple[Type | Hold, ...] = (
    Hold(0.5),
    Type("pip install doeff", SHELL_PROMPT, gap_cap=0.005),
    Hold(1.0),
    Type("cat digest.py", SHELL_PROMPT),
    Hold(2.0),
    Type("cat handlers.py", SHELL_PROMPT),
    Hold(3.0),
    Type("python -i handlers.py", PYTHON_PROMPT),
    Hold(0.5),
    Type("run(handler(canned)(digest(url)))", PYTHON_PROMPT),
    Hold(1.2),
    Type("run(handler(canned)(handler(trace)(digest(url))))", PYTHON_PROMPT),
    Hold(1.5),
    Type("run(handler(canned)(budget(1)(digest(url))))", PYTHON_PROMPT),
    Hold(1.5),
    Type("run(handler(canned)(handler(live_world)(digest(url))))", PYTHON_PROMPT),
)
# How long to show the last screen (seconds) -- agg's --last-frame-duration.
LAST_FRAME_SECONDS = 4.0
# The gap between typing one character (seconds) and its jitter. The jitter's random numbers use a fixed seed so re-recording gives the same typing.
CHAR_SECONDS = 0.035
CHAR_JITTER = 0.4
SEED = 7
COMMAND_TIMEOUT_SECONDS = 300

ANSI = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>()][0-9A-Za-z]?"
)


class ToolError(Exception):
    """The tool cannot be used (exit code 2)."""


def plain(text: str) -> str:
    """Text with terminal control sequences and CR removed. Used to read whether the prompt has returned."""
    return ANSI.sub("", text).replace("\r", "")


class Recorder:
    """Type into bash inside the pty and record the output with timestamps from a virtual clock."""

    def __init__(self, fd: int) -> None:
        """State at the start of recording -- the virtual clock is at 0 seconds and no output has been recorded."""
        self.fd = fd
        self.clock = 0.0
        self.events: tuple[tuple[float, str], ...] = ()
        self.since_enter = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.random = random.Random(SEED)

    def _read(self, timeout: float) -> str | None:
        """Turn output arriving from the pty into text. None if nothing arrives for timeout seconds; empty text if the terminal closed."""
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return None
        try:
            data = os.read(self.fd, 65536)
        except OSError:  # a pty whose child closed returns EIO
            return ""
        return self.decoder.decode(data)

    def _record(self, text: str) -> None:
        """Keep the output in the GIF's event list at the current time, and also add it to what is used to read whether the prompt has returned."""
        if text:
            self.events = (*self.events, (round(self.clock, 3), text))  # noqa: DOEFF002 — the clock and input field of the single recorder that holds recording state
            self.since_enter += text  # noqa: DOEFF002 — the clock and input field of the single recorder that holds recording state

    def drain(self, quiet: float) -> None:
        """Read until nothing arrives for quiet seconds, and record at the current time (the echo of typed characters, etc.)."""
        while (chunk := self._read(quiet)) is not None:
            if chunk == "":
                return
            self._record(chunk)

    def wait_for(self, prompt: str, gap_cap: float) -> None:
        """Read until the prompt returns at the end of the output. Waits between output are shortened to at most gap_cap seconds and timestamped."""
        last = time.monotonic()
        deadline = last + COMMAND_TIMEOUT_SECONDS
        while not re.search(prompt, plain(self.since_enter)):
            chunk = self._read(0.05)
            now = time.monotonic()
            if chunk == "":
                raise ToolError(f"the terminal closed. Last output:\n{plain(self.since_enter)[-2000:]}")
            if chunk is not None:
                self.clock += min(now - last, gap_cap)  # noqa: DOEFF002 — the clock and input field of the single recorder that holds recording state
                last = now
                self._record(chunk)
            if now > deadline:
                raise ToolError(
                    f"the prompt did not return after waiting {COMMAND_TIMEOUT_SECONDS} seconds. Last output:\n"
                    f"{plain(self.since_enter)[-2000:]}"
                )
        self.drain(0.15)

    def type(self, step: Type) -> None:
        """Type a command at human speed, run it, and record until the answer has finished appearing."""
        for char in step.text:
            os.write(self.fd, char.encode())
            self.clock += CHAR_SECONDS * (1 + CHAR_JITTER * (self.random.random() - 0.5))
            self.drain(0.02)
        self.clock += CHAR_SECONDS * 3
        self.since_enter = ""
        os.write(self.fd, b"\r")
        self.wait_for(step.prompt, step.gap_cap)

    def hold(self, step: Hold) -> None:
        """Advance only the virtual clock without typing, so the viewer can read the current screen."""
        self.drain(0.05)
        self.clock += step.seconds


def make_world(work: Path, python: str) -> Path:
    """Copy the files shown in the GIF and make a new venv with pip. Return the venv dir."""
    uv = shutil.which("uv")
    if uv is None:
        raise ToolError("uv is not on PATH (https://docs.astral.sh/uv/)")
    for demo_file in DEMO_FILES:
        shutil.copy(demo_file, work / demo_file.name)
    venv = work / ".venv"
    made = subprocess.run(
        [uv, "venv", "--quiet", "--seed", "--python", python, str(venv)],
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
    )
    if made.returncode != 0:
        raise ToolError(f"uv venv failed:\n{made.stderr}")
    return venv


def shell_env(work: Path, venv: Path) -> dict[str, str]:
    """The environment of the recorded bash. Environment variables of this machine are not passed."""
    return {
        "PATH": f"{venv / 'bin'}:/usr/bin:/bin",
        "VIRTUAL_ENV": str(venv),
        "HOME": str(work),
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "PS1": r"\[\e[1;32m\]$\[\e[0m\] ",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_PROGRESS_BAR": "off",
        "PIP_NO_CACHE_DIR": "1",
        "PIP_NO_INPUT": "1",
    }


def record(work: Path, venv: Path) -> tuple[tuple[float, str], ...]:
    """Perform the script inside the pty and return the list of output timestamps."""
    bash = shutil.which("bash", path="/usr/bin:/bin")
    if bash is None:
        raise ToolError("bash is in neither /usr/bin nor /bin")
    pid, fd = pty.fork()
    if pid == 0:  # child: start bash in a terminal of the chosen size
        os.chdir(work)
        os.execve(bash, ["bash", "--norc", "--noprofile", "-i"], shell_env(work, venv))
    try:
        ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
        os.kill(pid, signal.SIGWINCH)
        recorder = Recorder(fd)
        recorder.wait_for(SHELL_PROMPT, gap_cap=0.0)
        for step in SCRIPT:
            if isinstance(step, Type):
                recorder.type(step)
            else:
                recorder.hold(step)
        return recorder.events
    finally:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        os.close(fd)


def write_cast(events: tuple[tuple[float, str], ...], path: Path) -> None:
    """Turn the recorded output into an asciicast v2 that agg can read (the environment records only the terminal type)."""
    header = {
        "version": 2,
        "width": COLUMNS,
        "height": ROWS,
        "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"},
    }
    lines = [json.dumps(header)] + [json.dumps([at, "o", text]) for at, text in events]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render(cast: Path, out: Path, agg: str, font_dirs: list[str]) -> None:
    """Turn the asciicast into the GIF placed in the README. Colors, glyph size, and frame count are values that fit the 1 MB target."""
    command = (
        agg,
        "--quiet",
        "--theme",
        "github-dark",
        "--font-size",
        "16",
        "--fps-cap",
        "10",
        # Timing was already shortened at recording time, so agg does not shorten it (keeps the script's Hold)
        "--idle-time-limit",
        "60",
        "--last-frame-duration",
        str(LAST_FRAME_SECONDS),
        *(arg for font_dir in font_dirs for arg in ("--font-dir", font_dir)),
        str(cast),
        str(out),
    )
    made = subprocess.run(command, capture_output=True, text=True, check=False)
    if made.returncode != 0:
        raise ToolError(f"agg failed:\n{made.stderr}")


def main(argv: list[str]) -> int:
    """Record and write the GIF, and report its size and length. The exit code is as in the module docstring."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="GIF to write (default images/readme-demo.gif)"
    )
    parser.add_argument("--cast", type=Path, help="where to keep the intermediate asciicast (default: not kept)")
    parser.add_argument(
        "--python",
        default="3.14",
        help="Python version of the recorded venv (default 3.14 -- the REPL prompt gets color)",
    )
    parser.add_argument("--agg", default="agg", help="path of agg (default: from PATH)")
    parser.add_argument(
        "--font-dir", action="append", default=[], help="a glyph dir to add to agg (can be given repeatedly)"
    )
    args = parser.parse_args(argv)
    agg = shutil.which(args.agg)
    try:
        if agg is None:
            raise ToolError(
                f"agg not found: {args.agg} (https://github.com/asciinema/agg/releases)"
            )
        with tempfile.TemporaryDirectory(prefix="doeff-demo-") as tmp:
            work = Path(tmp)
            venv = make_world(work, args.python)
            events = record(work, venv)
            cast = args.cast or work / "demo.cast"
            write_cast(events, cast)
            render(cast, args.out, agg, args.font_dir)
    except ToolError as error:
        print(f"cannot record: {error}", file=sys.stderr)
        return 2
    size = args.out.stat().st_size
    length = events[-1][0] + LAST_FRAME_SECONDS
    print(f"{args.out}: {size / 1_000_000:.2f} MB, about {length:.1f} seconds")
    if size > SIZE_BUDGET_BYTES:
        print(f"exceeded the target of {SIZE_BUDGET_BYTES / 1_000_000:.1f} MB", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
