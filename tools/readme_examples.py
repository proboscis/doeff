"""One place that reads the README examples (``` fences), runs them, and compares the printed output with the annotations (doeff-publish initiative).

Used in two places: the repo test tests/test_readme_examples.py (runs the root README in the development environment and reads that the fences of the READMEs of the 5 core packages follow the
conventions below), and the tool tools/verify_release_install.py for checks before and after release (runs the examples of the 5
READMEs in a fresh venv with the 5 core packages installed). Both read READMEs by the same conventions:

- The fence's language (the first word after ```) decides the treatment. python and hy are written to a file and run as a script with a fresh interpreter,
  and the sequence of annotations at the end of print lines (Python `print(x)  # value`, Hy `(print x)  ; value`) is compared with the sequence of lines on standard output. The exit code alone
  is not enough, so an example with no annotation is red because it has nothing checked. bash, text, and toml are display only. A language that is none of these is red
  (so that a fence rewritten as ```py or the like is not silently dropped from the check).
- Words after the language add treatments (GitHub and PyPI both read only the first word as the language and do not render later words):
  - `file=<name>`: write the fence to the file <name> in the example dir (a module or conftest.py that later fences import). If it has print
    lines with annotations, it is run as a script after being written.
  - `excerpt`: a part of a large file (uses names not written). Not run.
  - `run` (bash only): run the fence with `bash -e`. Checks that the text of the annotation at the end of a command line (`command  # text`) appears
    somewhere in the output (stdout and stderr) -- tools print times and versions too, so they cannot be compared line by line.
  - `continues` (python and hy only): a continuation of the example above. The code of the nearest fence above with the same language (a script with neither file= nor excerpt) plus
    what is added is run as one script, and annotations are compared continuing from the upper part (the same as a reader
    appending to the file of the example above and running it). If the upper fence is also a continuation, all of that chain.
- The fences of one README are handled in one dir from top to bottom (the same as a reader placing files in the order written and running them).

The interpreter is started with -E -s -B: it reads neither PYTHON* environment variables nor the user's site (relies on neither the repo tree paths nor pytest's conftest),
and writes no bytecode (if it did, the root conftest would go red saying startup bytecode was left under the checkout). Unlike -I, the
script's dir goes into sys.path, so a module written next to it with `file=` can be imported.

Uses only the standard library (tools/verify_release_install.py runs without syncing the workspace).
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Runner:
    """Which file a runnable-language fence is written to and how it is started."""

    # extension of the example file
    suffix: str
    # arguments placed after the interpreter
    arguments: tuple[str, ...]


# Languages that are run -> how they are run.
RUNNERS = {
    "python": Runner(".py", ("-E", "-s", "-B")),
    "hy": Runner(".hy", ("-E", "-s", "-B", "-m", "hy")),
}
# The shell language that is run when the `run` word is present.
SHELL = "bash"
# Languages that are not run (shell commands to copy and type, diagrams, config fragments).
SHOWN_ONLY = frozenset({SHELL, "text", "toml"})
# Words that may follow the language (besides `file=<name>`).
EXCERPT = "excerpt"
RUN = "run"
CONTINUES = "continues"

_FENCE = re.compile(r"^```(?P<info>[^\n`]*)\n(?P<code>.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
_SECTION = re.compile(r"^## (?P<title>.+?)[ \t]*$", re.MULTILINE)
# The annotation of the printed value at the end of a print line (fence language -> regex that picks the annotation).
_STATED_OUTPUT = {
    "python": re.compile(r"\bprint\(.*\)\s+#\s?(?P<output>.*?)\s*$"),
    "hy": re.compile(r"\(print\s.*\)\s+;\s?(?P<output>.*?)\s*$"),
    # From the first "space # space" onward in a command line (one not starting with #).
    SHELL: re.compile(r"^\s*[^#\s][^#]*?\s+#\s(?P<output>.*?)\s*$"),
}
_FILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


@dataclass(frozen=True)
class Example:
    """One README fence."""

    # Which README (used for the printed name -- e.g. "README.md", "the README of doeff-hy")
    source: str
    # The section the fence is in (the `## ` heading -- "" if before the first heading)
    section: str
    # The fence's language (the first word after ```)
    language: str
    # The fence's content (for a continues fence, what was appended after the code of the fence it continues -- the whole script to run)
    code: str
    # The line where the fence starts (counted from 1)
    line: int
    # Words after the language
    words: tuple[str, ...] = ()
    # The start line of the fence that a continues fence continues (None if not continues, or if there is nothing to continue)
    continues_from: int | None = None

    @property
    def name(self) -> str:
        """A name for a sentence saying something failed, so the reader can find the README fence."""
        return f"{self.source}:{self.line}({self.language} in {self.section})"

    @property
    def file(self) -> str | None:
        """The name of `file=<name>` (None if absent)."""
        return next((word[len("file=") :] for word in self.words if word.startswith("file=")), None)

    def stated_output(self) -> list[str]:
        """The sequence of annotations at line ends -- what the example says it prints."""
        pattern = _STATED_OUTPUT.get(self.language)
        if pattern is None:
            return []
        return [
            found["output"] for line in self.code.splitlines() if (found := pattern.search(line))
        ]

    def written_as(self) -> str | None:
        """The name of the file written to the example dir (None for a fence that is not written)."""
        if self.language in RUNNERS and EXCERPT not in self.words:
            return self.file or f"readme_example_{self.line}{RUNNERS[self.language].suffix}"
        if self.language == SHELL and RUN in self.words:
            return f"readme_example_{self.line}.sh"
        return None

    def runs(self) -> bool:
        """Whether this is a fence that is run and compared with its annotations."""
        if self.written_as() is None:
            return False
        return self.file is None or bool(self.stated_output())

    def problems(self) -> list[str]:
        """The places that do not follow the conventions (empty if it follows them)."""
        return list(self._problems())

    def _problems(self) -> Iterator[str]:
        """The content of problems -- for each convention, emit a sentence naming the convention if it is not followed."""
        if self.language not in RUNNERS and self.language not in SHOWN_ONLY:
            yield (
                f"the language of {self.name} is neither a run language ({sorted(RUNNERS)}) nor"
                f" a not-run language ({sorted(SHOWN_ONLY)}) -- fix the language, or if it is not run, add it to SHOWN_ONLY"
            )
        unknown = [
            word
            for word in self.words
            if word not in (EXCERPT, RUN, CONTINUES) and not word.startswith("file=")
        ]
        if unknown:
            yield (
                f"{self.name} has an unknown word {unknown} after the language"
                f" (allowed words: file=<name>, {EXCERPT}, {RUN}, {CONTINUES})"
            )
        if RUN in self.words and self.language != SHELL:
            yield f"{RUN} of {self.name} can be written only on a {SHELL} fence"
        if EXCERPT in self.words and (self.file is not None or RUN in self.words):
            yield f"{EXCERPT} of {self.name} is a fence that is not run, so it cannot be written with file= or {RUN}"
        if CONTINUES in self.words:
            if self.language not in RUNNERS or self.file is not None or EXCERPT in self.words:
                yield (
                    f"{CONTINUES} of {self.name} is written on a python or hy fence, and cannot be written with file= or"
                    f" {EXCERPT} (a fence that is appended to the script above and run)"
                )
            elif self.continues_from is None:
                yield (
                    f"{self.name}: above {CONTINUES} there is no {self.language} fence to continue into (a script with neither file= nor {EXCERPT})"
                )
        if self.file is not None and (
            self.language not in RUNNERS or not _FILE_NAME.fullmatch(self.file)
        ):
            yield f"file={self.file} of {self.name} must be on a python or hy fence, with a name that contains no dir"
        if self.runs() and not self.stated_output():
            yield (
                f"{self.name} has no annotation of the printed value -- nothing is checked, so"
                " write it in the form `print(x)  # value` (Hy `(print x)  ; value`, bash `command  # text in the output`)"
            )


def _info(fence: re.Match[str]) -> tuple[str, ...]:
    """The sequence of words after the ``` of a fence (one empty language if there is no language)."""
    return tuple(fence["info"].split()) or ("",)


def fences(text: str, source: str) -> list[Example]:
    """Read the fences of the README body from the top (a continues fence is read with the code of the fence it continues put at the head)."""
    sections = [(found.start(), found["title"]) for found in _SECTION.finditer(text)]
    read: list[Example] = []
    for fence in _FENCE.finditer(text):
        language, *words = _info(fence)
        above = _continued(read, language) if CONTINUES in words else None
        read.append(
            Example(
                source=source,
                section=next(
                    (title for start, title in reversed(sections) if start < fence.start()), ""
                ),
                language=language,
                code=fence["code"] if above is None else above.code + fence["code"],
                line=text.count("\n", 0, fence.start()) + 1,
                words=tuple(words),
                continues_from=None if above is None else above.line,
            )
        )
    return read


def _continued(above: Sequence[Example], language: str) -> Example | None:
    """What a continues fence continues -- the nearest fence above with the same language, if it is a script (neither file= nor excerpt)."""
    same = next((example for example in reversed(above) if example.language == language), None)
    if same is None or same.file is not None or EXCERPT in same.words:
        return None
    return same


def write(example: Example, directory: Path) -> Path:
    """Write the fence to a file in the example dir (not used for fences that are not written)."""
    name = example.written_as()
    if name is None:
        raise ValueError(f"{example.name} is a fence that is not written to a file")
    path = directory / name
    path.write_text(example.code, encoding="utf-8")
    return path


def command(example: Example, script: Path, python: Path) -> list[str]:
    """The command that runs a written fence."""
    if example.language == SHELL:
        return ["bash", "-e", script.name]
    return [str(python), *RUNNERS[example.language].arguments, script.name]


def run(
    example: Example,
    directory: Path,
    python: Path,
    timeout: float,
    *,
    environment: Mapping[str, str] | None = None,
    wrap: Callable[[list[str]], list[str]] = list,
) -> subprocess.CompletedProcess[str]:
    """Write the fence to a file in the directory and run it with cwd set to the directory.

    Put python's dir at the head of PATH (so `pytest` and `python` in a bash fence are those of that interpreter's environment).
    If environment is None, inherit this process's environment. wrap adds what goes before the command (such as the check's `env ...`).
    """
    script = write(example, directory)
    inherited = os.environ if environment is None else environment  # noqa: DOEFF004 — the one place where the tool passes the current environment to the child process
    path = os.pathsep.join(filter(None, (str(python.parent), inherited.get("PATH"))))
    return subprocess.run(
        wrap(command(example, script, python)),
        cwd=directory,
        env={**inherited, "PATH": path},
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )


def mismatch(example: Example, result: subprocess.CompletedProcess[str]) -> str | None:
    """A sentence saying the example failed or printed something different from its annotations (None if it matches)."""
    if result.returncode != 0:
        return (
            f"{example.name} failed with exit code {result.returncode} — update the example to the current usage\n"
            f"{result.stdout}{result.stderr}"
        )
    if example.language == SHELL:
        output = result.stdout + result.stderr
        missing = [stated for stated in example.stated_output() if stated not in output]
        if missing:
            return f"{example.name}: the output lacks the annotated text {missing} — output\n{output}"
        return None
    printed = result.stdout.splitlines()
    if printed != example.stated_output():
        return (
            f"{example.name}: the printed output differs from the annotation on the print line — annotation {example.stated_output()}, "
            f"printed {printed}"
        )
    return None


def run_in_order(
    examples: Sequence[Example],
    directory: Path,
    python: Path,
    timeout: float,
    *,
    environment: Mapping[str, str] | None = None,
    on_result: Callable[[Example, str | None], None] = lambda _example, _failure: None,
) -> list[str]:
    """Handle the fences of one README in a directory from top to bottom and return the sequence of sentences saying what failed (empty if all pass).

    Fences that do not follow the conventions are counted without being run. on_result is called for each fence that was run (with a sentence saying it failed, or None).
    """
    return list(
        _failures(
            examples, directory, python, timeout, environment=environment, on_result=on_result
        )
    )


def _failures(
    examples: Sequence[Example],
    directory: Path,
    python: Path,
    timeout: float,
    *,
    environment: Mapping[str, str] | None,
    on_result: Callable[[Example, str | None], None],
) -> Iterator[str]:
    """The content of run_in_order -- write and run the fences in order and emit sentences saying what failed."""
    for example in examples:
        problems = example.problems()
        if problems:
            yield from problems
            continue
        if not example.runs():
            if example.written_as() is not None:
                write(example, directory)
            continue
        try:
            failure = mismatch(
                example, run(example, directory, python, timeout, environment=environment)
            )
        except subprocess.TimeoutExpired:
            failure = f"{example.name} did not finish in {timeout} seconds"
        on_result(example, failure)
        if failure is not None:
            yield failure
