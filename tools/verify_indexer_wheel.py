"""Check that the doeff-indexer wheel, after being installed, is in a form that works as a command (a defect found in the doeff-publish initiative).

The PyPI 0.1.3 Linux x86_64 wheel kept the command's binary at `python/doeff_indexer/bin/doeff-indexer`,
not at `doeff_indexer/bin/`. The command entry point (python/doeff_indexer/_cli.py) looks only under the package's `bin/`, so
after `pip install doeff-indexer`, `doeff-indexer --help` failed with "binary not found".

Cause: the binary is excluded from the python source by `.gitignore`, and `include` in pyproject.toml adds it to the wheel. maturin 1.12.0 puts the
added files in with their path from the project root unchanged (with the `python/` prefix), while 1.13.0 rewrites them to a path under python-source (`doeff_indexer/bin/`).
0.1.3 was built in CI where maturin-action pulled 1.12.0, and was uploaded as is.

This tool checks two things after building and before uploading:

- Layout (all wheels): `doeff_indexer/bin/doeff-indexer` (`.exe` on Windows) exists, has the executable bit except on Windows, and
  nothing under `python/` is at the root of the wheel.
- Behavior after install (the one wheel given with `--smoke`): install it with pip into a fresh venv, `doeff-indexer --help` succeeds, and
  `find-interpreters` finds the `# doeff: interpreter` in a file that has neither the word `Program` nor a type annotation.

- The list (`--expect-all`): all 15 published wheels are present (3 kinds -- regular abi3 and free-threaded 3.13 and 3.14 -- x 2 Linux x 2 macOS x
  Windows). In the 0.2.0 manual run the 5 wheels for 3.13 were not built, leaving 10, yet the jobs and the layout check still passed.

Usage: python tools/verify_indexer_wheel.py [--expect-all] [--smoke <a wheel that runs on this machine>] <wheel>...
(standard library only -- it runs with a Python that merely has the repo checked out, so it can also be called from a CI job before upload)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PACKAGE = "doeff_indexer"
# The mark of a file that has neither the word `Program`, nor `@do`, nor a type annotation (the first example in docs/MARKERS.md).
SAMPLE_FILE = "markers_only.py"
SAMPLE = "from doeff import run\n\ndef my_interpreter(program):  # doeff: interpreter\n    return run(program)\n"
SAMPLE_INTERPRETER = "my_interpreter"
EXECUTABLE_BITS = 0o111
# The list of wheels to publish: 3 kinds x 5 OSes = 15 (the same count as PyPI 0.1.3). A kind is (python tag, abi tag).
# In the first manual run of 0.2.0, maturin 1.15 rejected the free-threaded 3.13 specification, so the 5 wheels for 3.13 were not built, leaving 10.
# Even so every job passed, and so did the layout check -- because CI had no list of the wheels to build.
EXPECTED_KINDS = (("cp310", "abi3"), ("cp313", "cp313t"), ("cp314", "cp314t"))
EXPECTED_PLATFORMS = ("linux-x86_64", "linux-aarch64", "macos-x86_64", "macos-arm64", "windows-x86_64")


def _is_windows(wheel: Path) -> bool:
    """From the last part of the wheel's name (the platform tag -- e.g. `win_amd64`), tell whether it is a Windows wheel."""
    return wheel.stem.split("-")[-1].startswith("win")


def layout_problems(wheel: Path) -> list[str]:
    """Read whether the wheel's contents hold the binary where the command entry point looks, and return the missing parts one per line (empty if good)."""
    windows = _is_windows(wheel)
    binary = f"{PACKAGE}/bin/doeff-indexer{'.exe' if windows else ''}"
    with zipfile.ZipFile(wheel) as archive:
        infos = {info.filename: info for info in archive.infolist()}
    info = infos.get(binary)
    missing = (
        () if info is not None else (f"{wheel.name}: {binary} is missing (the command entry looks only here)",)
    )
    empty = (f"{wheel.name}: {binary} is empty",) if info is not None and info.file_size == 0 else ()
    not_executable = (
        (f"{wheel.name}: {binary} lacks the executable bit",)
        if info is not None and not windows and not (info.external_attr >> 16) & EXECUTABLE_BITS
        else ()
    )
    stray = sorted(name for name in infos if name.startswith("python/"))
    misplaced = (
        (
            f"{wheel.name}: there is something under python/ at the root of the wheel ({', '.join(stray)}) -- "
            "installing it drops it into site-packages/python/ (the include layout of maturin 1.12 and earlier)",
        )
        if stray
        else ()
    )
    return [*missing, *empty, *not_executable, *misplaced]


def _platform_of(wheel: Path) -> str | None:
    """From the last part of the wheel's name (the platform tag), read the OS name in the list (None if it cannot be read)."""
    tag = wheel.stem.split("-")[-1]
    if tag.startswith("win_amd64"):
        return "windows-x86_64"
    if tag.startswith("macosx"):
        return "macos-arm64" if tag.endswith("arm64") else "macos-x86_64" if tag.endswith("x86_64") else None
    if "manylinux" in tag or tag.startswith("linux"):
        return "linux-aarch64" if "aarch64" in tag else "linux-x86_64" if "x86_64" in tag else None
    return None


def missing_wheels(wheels: list[Path]) -> list[str]:
    """Return, one per line, those of the 15 published wheels (kind x OS) that are not among the given wheels (empty if all are present).

    What is counted is kind x OS, not the number of files: when the macOS and Windows free-threaded jobs build the same abi3 wheel as the regular job,
    wheels of the same name are counted as one kind, and the free-threaded kind remains missing.
    """
    present = set()
    for wheel in wheels:
        parts = wheel.stem.split("-")
        if len(parts) >= 5:
            present.add((parts[2], parts[3], _platform_of(wheel)))
    return [
        f"{platform} has no {python}-{abi} wheel"
        for python, abi in EXPECTED_KINDS
        for platform in EXPECTED_PLATFORMS
        if (python, abi, platform) not in present
    ]


def _venv_tool(venv: Path, name: str) -> Path:
    return (
        venv
        / ("Scripts" if sys.platform == "win32" else "bin")
        / (f"{name}.exe" if sys.platform == "win32" else name)
    )


def _run(argv: list[str | Path]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(part) for part in argv], capture_output=True, text=True, check=False)


def smoke_problems(wheel: Path, work: Path) -> list[str]:
    """Install the wheel into a fresh venv, fire the command, and return, one per line, where it does not work (empty if good). work is an empty dir."""
    venv = work / "venv"
    created = _run([sys.executable, "-m", "venv", venv])
    if created.returncode != 0:
        return [f"cannot create the venv: {created.stderr.strip()}"]
    # The command entry point (_cli) reads neither doeff nor the extension module, so install only the wheel, without the dependency (doeff -- read by the Python-side effects and handlers),
    # without going out to PyPI, and fire the command.
    installed = _run(
        [
            _venv_tool(venv, "python"),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--no-deps",
            "--no-index",
            wheel,
        ]
    )
    if installed.returncode != 0:
        return [f"cannot install {wheel.name}: {installed.stderr.strip()}"]
    command = _venv_tool(venv, "doeff-indexer")
    helped = _run([command, "--help"])
    if helped.returncode != 0:
        return [
            f"after install, `doeff-indexer --help` exited with code {helped.returncode}: {(helped.stderr or helped.stdout).strip()}"
        ]
    sample = work / "sample"
    sample.mkdir()
    (sample / SAMPLE_FILE).write_text(SAMPLE, encoding="utf-8")
    found = _run([command, "find-interpreters", "--root", sample])
    if found.returncode != 0:
        return [
            f"after install, `doeff-indexer find-interpreters` exited with code {found.returncode}: {found.stderr.strip()}"
        ]
    names = [entry["name"] for entry in json.loads(found.stdout)["entries"]]
    if SAMPLE_INTERPRETER not in names:
        return [
            f"find-interpreters does not find the file with only the `# doeff: interpreter` mark (found: {names})"
        ]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the layout of the doeff-indexer wheel and its behavior after install"
    )
    parser.add_argument("wheels", nargs="+", type=Path, help="Wheels whose layout to check")
    parser.add_argument(
        "--smoke", type=Path, help="The wheel to install into a fresh venv and fire the command with (one that runs on this machine)"
    )
    parser.add_argument(
        "--expect-all",
        action="store_true",
        help="Also check that all 15 published wheels (kind x OS) are present (added by the job before publishing)",
    )
    args = parser.parse_args(argv)

    layout = [problem for wheel in args.wheels for problem in layout_problems(wheel)]
    if args.expect_all:
        layout = [*missing_wheels(args.wheels), *layout]
    if args.smoke is None:
        smoke: list[str] = []
    else:
        with tempfile.TemporaryDirectory(prefix="verify-indexer-wheel-") as work:
            smoke = smoke_problems(args.smoke, Path(work))
    problems = [*layout, *smoke]
    for problem in problems:
        print(f"verify_indexer_wheel: {problem}", file=sys.stderr)
    if not problems:
        print(
            f"verify_indexer_wheel: layout of {len(args.wheels)} wheels is good"
            + (", and the command works after install" if args.smoke else "")
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
