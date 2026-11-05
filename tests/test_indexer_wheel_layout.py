"""Test that the doeff-indexer wheel is built so that after installation it works as the `doeff-indexer` command (a defect found in the doeff-publish effort).


The PyPI 0.1.3 Linux x86_64 wheel holds the command binary at `python/doeff_indexer/bin/doeff-indexer`, not in `doeff_indexer/bin/` where the command entrypoint
(packages/doeff-indexer/python/doeff_indexer/_cli.py) looks. After `pip install doeff-indexer`,
`doeff-indexer --help` failed with "binary not found". The cause is the maturin version: the binary is excluded from the python source by .gitignore and
pyproject.toml's `include` adds it to the wheel. maturin 1.12 puts the added file in at its path from the project root, while 1.13 puts it
under python-source. 0.1.3 was built in the CI of the day maturin-action pulled 1.12.

There are two defenses (the CI side is tests/test_indexer_wheel_workflow.py):

1. The layout check of the tool tools/verify_indexer_wheel.py names 0.1.3's layout, a binary without an executable mark, and python/ at the root.
2. The maturin lower bound of the build entry (packages/doeff-indexer/pyproject.toml) is 1.13.

The check that installs a real wheel into a fresh venv and fires the command (the tool's `--smoke`) builds Rust, so it is not in pytest (a cold build exceeds the
120-second limit of one test) -- it is held by local checks against a built wheel and by the CI job.
"""

from __future__ import annotations

import importlib.util
import re
import zipfile
from pathlib import Path
from types import ModuleType

import pytest
import tomllib

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "verify_indexer_wheel.py"
PACKAGE = REPO / "packages" / "doeff-indexer"
# The maturin version that began placing include files under python-source.
MATURIN_FLOOR = (1, 13, 0)

# The bin dir entries of the PyPI 0.1.3 Linux x86_64 wheel (the layout of maturin 1.12).
PYPI_0_1_3 = (
    "doeff_indexer/bin/.gitignore",
    "doeff_indexer/bin/.gitkeep",
    "python/doeff_indexer/bin/.gitignore",
    "python/doeff_indexer/bin/.gitkeep",
    "python/doeff_indexer/bin/doeff-indexer",
)
LINUX_WHEEL = "doeff_indexer-0.1.4-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
WINDOWS_WHEEL = "doeff_indexer-0.1.4-cp310-abi3-win_amd64.whl"
EXECUTABLE = 0o100755 << 16
PLAIN = 0o100644 << 16


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_indexer_wheel", TOOL)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wheel(tmp_path: Path, name: str, entries: dict[str, int]) -> Path:
    """Build a wheel with entries (path inside the wheel -> permissions placed in the ZipInfo field)."""
    wheel = tmp_path / name
    with zipfile.ZipFile(wheel, "w") as archive:
        for entry, attributes in entries.items():
            info = zipfile.ZipInfo(entry)
            info.external_attr = attributes
            archive.writestr(info, b"\x7fELF binary" if "doeff-indexer" in entry else b"")
    return wheel


def _versions(requirement: str) -> tuple[int, ...]:
    """A number sequence of `v1.15.0` or `maturin>=1.13` (padded with 0 so the digit count matches MATURIN_FLOOR and they can be compared)."""
    numbers = tuple(int(part) for part in re.findall(r"\d+", requirement))
    return numbers + (0,) * (len(MATURIN_FLOOR) - len(numbers))


def test_a_wheel_with_the_binary_in_the_package_bin_dir_passes(tmp_path: Path) -> None:
    wheel = _wheel(
        tmp_path,
        LINUX_WHEEL,
        {
            "doeff_indexer/__init__.py": PLAIN,
            "doeff_indexer/bin/.gitkeep": PLAIN,
            "doeff_indexer/bin/doeff-indexer": EXECUTABLE,
        },
    )
    assert _tool().layout_problems(wheel) == []


def test_a_windows_wheel_needs_the_exe_and_no_executable_bit(tmp_path: Path) -> None:
    good = _wheel(tmp_path, WINDOWS_WHEEL, {"doeff_indexer/bin/doeff-indexer.exe": PLAIN})
    assert _tool().layout_problems(good) == []
    # Even if a Windows wheel has a binary without an extension, the command entrypoint looks for .exe.
    bare = tmp_path / "bare"
    bare.mkdir()
    wrong = _wheel(bare, WINDOWS_WHEEL, {"doeff_indexer/bin/doeff-indexer": PLAIN})
    assert any("doeff-indexer.exe is missing" in problem for problem in _tool().layout_problems(wrong))


def test_the_pypi_0_1_3_layout_is_named_for_both_faults(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, LINUX_WHEEL, dict.fromkeys(PYPI_0_1_3, EXECUTABLE))
    problems = _tool().layout_problems(wheel)
    assert any("doeff_indexer/bin/doeff-indexer is missing" in problem for problem in problems)
    assert any("python/doeff_indexer/bin/doeff-indexer" in problem for problem in problems)


def test_a_stray_python_dir_is_named_even_when_the_binary_is_in_place(tmp_path: Path) -> None:
    # When maturin 1.12 builds in a work tree where .gitignore has no effect (outside git), the binary goes into both paths.
    wheel = _wheel(
        tmp_path,
        LINUX_WHEEL,
        {
            "doeff_indexer/bin/doeff-indexer": EXECUTABLE,
            "python/doeff_indexer/bin/doeff-indexer": EXECUTABLE,
        },
    )
    problems = _tool().layout_problems(wheel)
    assert len(problems) == 1
    assert "python/" in problems[0]


def test_a_binary_without_the_executable_bit_is_named(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, LINUX_WHEEL, {"doeff_indexer/bin/doeff-indexer": PLAIN})
    assert any("lacks the executable bit" in problem for problem in _tool().layout_problems(wheel))


def test_main_exits_nonzero_and_prints_each_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = _wheel(tmp_path, LINUX_WHEEL, dict.fromkeys(PYPI_0_1_3, EXECUTABLE))
    assert _tool().main([str(wheel)]) == 1
    assert capsys.readouterr().err.count("verify_indexer_wheel:") == 2


def test_the_build_entry_asks_for_maturin_from_the_layout_fix() -> None:
    requires = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))[
        "build-system"
    ]["requires"]
    (maturin,) = [requirement for requirement in requires if requirement.startswith("maturin")]
    assert _versions(maturin.split(",")[0]) >= MATURIN_FLOOR, maturin


# The list of wheels to publish: 3 kinds x 5 OSes (PyPI 0.1.3 has 15).
FULL_SET = [
    f"doeff_indexer-0.2.0-{python}-{abi}-{platform}.whl"
    for python, abi in (("cp310", "abi3"), ("cp313", "cp313t"), ("cp314", "cp314t"))
    for platform in (
        "manylinux_2_17_x86_64.manylinux2014_x86_64",
        "manylinux_2_17_aarch64.manylinux2014_aarch64",
        "macosx_10_12_x86_64",
        "macosx_11_0_arm64",
        "win_amd64",
    )
]


def test_the_full_set_of_fifteen_wheels_has_nothing_missing() -> None:
    assert len(FULL_SET) == 15
    assert _tool().missing_wheels([Path(name) for name in FULL_SET]) == []


def test_five_free_threaded_3_13_wheels_missing_are_each_named() -> None:
    # The first manual run of 0.2.0: no wheel for free-threaded 3.13 was built at all, leaving 10 (the layout check still passed).
    ten = [name for name in FULL_SET if "cp313t" not in name]
    problems = _tool().missing_wheels([Path(name) for name in ten])
    assert len(problems) == 5
    assert all("cp313-cp313t" in problem for problem in problems)
    assert any("linux-aarch64" in problem for problem in problems)
    assert any("windows-x86_64" in problem for problem in problems)


def test_a_wheel_built_twice_does_not_stand_for_another_kind() -> None:
    # When the macOS and Windows free-threaded jobs build the same abi3 wheel as the normal jobs, wheels with the same name are counted not as 2 wheels but as
    # a missing kind (the list is counted by kind x OS, not by the number of files).
    same_as_abi3 = [name for name in FULL_SET if "cp314t" not in name] + [
        name for name in FULL_SET if "abi3" in name and "macosx" in name
    ]
    problems = _tool().missing_wheels([Path(name) for name in same_as_abi3])
    assert len(problems) == 5
    assert all("cp314-cp314t" in problem for problem in problems)


def test_main_with_expect_all_exits_nonzero_for_a_short_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wheels = [
        _wheel(
            tmp_path,
            name,
            {
                "doeff_indexer/bin/doeff-indexer.exe"
                if "win_amd64" in name
                else "doeff_indexer/bin/doeff-indexer": PLAIN if "win_amd64" in name else EXECUTABLE
            },
        )
        for name in FULL_SET
        if "cp313t" not in name
    ]
    assert _tool().main(["--expect-all", *map(str, wheels)]) == 1
    assert capsys.readouterr().err.count("cp313-cp313t") == 5
    # A call that does not look at the list (layout only) passes with the same wheel.
    assert _tool().main([*map(str, wheels)]) == 0
