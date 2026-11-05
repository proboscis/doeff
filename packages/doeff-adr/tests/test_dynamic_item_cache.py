"""Collection records for dynamic values and explicit ids, and the counterexample of not running stale records (#1459)."""

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from _pytest.mark.structures import ParameterSet
from doeff_adr._runtime_records import UnrecordableError, parametrize_record
from doeff_adr._source_dependencies import (
    RACY_WINDOW_NS,
    DependencyChecks,
    SourceDependency,
    SourceSnapshot,
    snapshot,
    stat_is_trusted,
)

pytest_plugins = ["pytester"]

SOURCE = """
(require doeff-hy.macros [deftest val])
(import os pytest)
(import pkg.inputs [HOST VALUES CASES])
(with [log (open (get os.environ "IMPORT_LOG") "a")] (.write log "imported\\n"))
(deftest test-host {:interpreters [HOST]} (assert True))
(deftest test-values [value] {:params {"value" (lfor v VALUES v)}}
  (assert (in value VALUES)))
(deftest test-cases [case] {:params {"case" (lfor c CASES (pytest.param c :id c.label))}}
  (assert (= case.answer 42)))
"""

INPUTS = """
from types import SimpleNamespace
HOST = "host-a"
VALUES = [1, 2]
CASES = [SimpleNamespace(label="case-a", answer=42)]
"""


@pytest.fixture
def dynamic_project(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    """Collect and run, in an independent process, a Hy test that reads actual values from another module."""
    package: Path = pytester.path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "inputs.py").write_text(INPUTS)
    (package / "test_dynamic.hy").write_text(SOURCE)
    pytester.makeini(
        """
        [pytest]
        pythonpath = .
        doeff_adr_hy_files = pkg/test_dynamic.hy
        doeff_adr_wiring = off
        doeff_adr_items_cache = items
        """
    )
    pytester.makeconftest(
        """
        import pytest
        from doeff import run

        @pytest.fixture
        def doeff_interpreter_name():
            return "default"

        @pytest.fixture
        def doeff_interpreter(doeff_interpreter_name):
            assert doeff_interpreter_name in {"default", "host-a", "host-b"}
            return run
        """
    )
    monkeypatch.setenv("IMPORT_LOG", str(pytester.path / "imports.log"))
    # Even if the provider changes within the same second and with the same size, do not use Python's timestamp pyc.
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    return pytester


def imports(project: pytest.Pytester) -> list[str]:
    log: Path = project.path / "imports.log"
    result: list[str] = log.read_text().splitlines() if log.exists() else []
    log.write_text("")
    return result


def collect(project: pytest.Pytester, *args: str) -> pytest.RunResult:
    return project.runpytest_subprocess("pkg/test_dynamic.hy", "--collect-only", "-q", *args)


def nodeids(result: pytest.RunResult) -> list[str]:
    return [line for line in result.stdout.lines if line.startswith("pkg/test_dynamic.hy::")]


def cache_path(project: pytest.Pytester) -> Path:
    source: Path = project.path / "pkg/test_dynamic.hy"
    digest: str = hashlib.sha256(source.read_bytes()).hexdigest()
    return project.path / "items" / f"{digest}.json"


def test_dynamic_values_interpreters_and_explicit_ids_are_cached(dynamic_project: pytest.Pytester) -> None:
    project: pytest.Pytester = dynamic_project
    cold: pytest.RunResult = collect(project)
    assert len(nodeids(cold)) == 4
    assert imports(project) == ["imported"]
    # A file right after being written is inside the record's timestamp window, so check by hash without trusting stat. As with a real working tree file,
    # rewrite the record timestamp to the form where the record was taken after the window width passed since the last change (do not wait on the wall clock -- collection is another process, so
    # the time supply cannot be swapped).
    entry: object = json.loads(cache_path(project).read_text())
    assert isinstance(entry, dict) and isinstance(entry["sources"], list) and entry["sources"], entry
    for source in entry["sources"]:
        assert isinstance(source, dict) and isinstance(source["mtime_ns"], int) and isinstance(source["ctime_ns"], int), source
        source["recorded_ns"] = max(source["mtime_ns"], source["ctime_ns"]) + 10 * RACY_WINDOW_NS
    cache_path(project).write_text(json.dumps(entry))
    warm: pytest.RunResult = collect(project)
    assert nodeids(warm) == nodeids(cold)
    assert imports(project) == []
    assert "collected from record: 1 files; imported during collection: 0 files" in warm.stdout.str()
    assert "hash 0 file" in warm.stdout.str()
    assert "rebuilt on dependency change: 0 files" in warm.stdout.str()
    collect(project, "-k", "not_selected")
    assert imports(project) == []
    result: pytest.RunResult = project.runpytest_subprocess("pkg/test_dynamic.hy", "-q")
    result.assert_outcomes(passed=4)
    assert imports(project) == ["imported"]


@pytest.mark.parametrize("initial_values", ["[1, 2]", "[]"])
def test_changed_provider_invalidates_interpreters_values_and_ids(
    dynamic_project: pytest.Pytester, initial_values: str
) -> None:
    project: pytest.Pytester = dynamic_project
    provider: Path = project.path / "pkg/inputs.py"
    provider.write_text(INPUTS.replace("[1, 2]", initial_values))
    collect(project)
    assert cache_path(project).exists()
    imports(project)
    provider.write_text(INPUTS.replace("host-a", "host-b").replace("[1, 2]", "[3]").replace("case-a", "case-b"))
    changed: pytest.RunResult = collect(project)
    assert imports(project) == ["imported"]
    assert "rebuilt on dependency change: 1 files" in changed.stdout.str()
    assert any("[host-b]" in name for name in nodeids(changed))
    assert any("[3]" in name for name in nodeids(changed))
    assert any("[case-b]" in name for name in nodeids(changed))
    assert not any("host-a" in name or "case-a" in name for name in nodeids(changed))
    warm: pytest.RunResult = collect(project)
    assert nodeids(warm) == nodeids(changed)
    assert imports(project) == []


@pytest.mark.parametrize("changed_part", ["value", "id", "type"])
def test_runtime_mismatch_fails_before_body_and_forgets_cache(
    dynamic_project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, changed_part: str
) -> None:
    project: pytest.Pytester = dynamic_project
    source: Path = project.path / "pkg/test_dynamic.hy"
    form: str = '(int (os.getenv "DYNAMIC_VALUE" "1"))' if changed_part == "value" else '1 :id (os.getenv "DYNAMIC_VALUE" "1")'
    if changed_part == "type":
        form = '(if (= (os.getenv "DYNAMIC_VALUE") "1") 1 True)'
    source.write_text(
        '(require doeff-hy.macros [deftest])\n(import os pytest)\n'
        f'(deftest test-changing [value] {{:params {{"value" [(pytest.param {form})]}}}}\n'
        '  (assert False "stale record executed the body"))\n'
    )
    monkeypatch.setenv("DYNAMIC_VALUE", "1")
    collect(project)
    assert cache_path(project).exists()
    monkeypatch.setenv("DYNAMIC_VALUE", "2")
    result: pytest.RunResult = project.runpytest_subprocess("pkg/test_dynamic.hy", "-q")
    result.assert_outcomes(errors=1)
    assert "record and real thing disagree" in result.stdout.str()
    assert not cache_path(project).exists()


READS_CONFTEST = """
import collections
import pathlib
import pytest
from doeff import run

_reads = collections.Counter()
_inside_import = collections.Counter()
_read_bytes = pathlib.Path.read_bytes


def _counting_read_bytes(self):
    _reads[self.name] += 1
    if _inside_import["depth"]:
        _reads[f"{self.name}@import"] += 1
    return _read_bytes(self)


pathlib.Path.read_bytes = _counting_read_bytes


@pytest.hookimpl(wrapper=True)
def pytest_doeff_import_hy_module(collector):
    # Count separately the reads inside the interval measured by the load limit of doeff-hy-pytest (the import of one file).
    _inside_import["depth"] += 1
    try:
        return (yield)
    finally:
        _inside_import["depth"] -= 1


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(f"times inputs.py was read {_reads['inputs.py']}")
    terminalreporter.write_line(f"times inputs.py was read inside the import interval {_reads['inputs.py@import']}")


@pytest.fixture
def doeff_interpreter_name():
    return "default"


@pytest.fixture
def doeff_interpreter(doeff_interpreter_name):
    return run
"""


def collect_two_dynamic_files(project: pytest.Pytester) -> pytest.RunResult:
    """Collect 2 actual-value files at once and output the number of times the already-loaded pkg/inputs.py was read (in total and inside the import interval)."""
    (project.path / "pkg/test_dynamic_b.hy").write_text(SOURCE + ";; second file (change the content hash so the record cache does not hit)\n")
    project.makeini(
        """
        [pytest]
        pythonpath = .
        doeff_adr_hy_files =
            pkg/test_dynamic.hy
            pkg/test_dynamic_b.hy
        doeff_adr_wiring = off
        doeff_adr_items_cache = items
        """
    )
    project.makeconftest(READS_CONFTEST)
    result: pytest.RunResult = project.runpytest_subprocess(
        "pkg/test_dynamic.hy", "pkg/test_dynamic_b.hy", "--collect-only", "-q"
    )
    assert "imported during collection: 2 files" in result.stdout.str(), result.stdout.str()
    assert len(list((project.path / "items").glob("*.json"))) == 2
    return result


def test_two_dynamic_files_in_one_collection_read_each_loaded_source_once(dynamic_project: pytest.Pytester) -> None:
    """Failure case (agora-controllers daily 2026-10-11 12:00): every time an actual-value record was written, all already-loaded local sources were
    re-read and hashed. Loaded modules grow as the session proceeds, so the CPU in the import interval of actual-value files grew with the position of the file, and
    only in heavily loaded runs exceeded the 1 second load limit of doeff-hy-pytest (9 of the 13 in the table). Within one collection, the same source is
    read only once."""
    result: pytest.RunResult = collect_two_dynamic_files(dynamic_project)
    assert "times inputs.py was read 1" in result.stdout.str(), result.stdout.str()


def test_the_import_window_of_a_dynamic_file_reads_no_loaded_source(dynamic_project: pytest.Pytester) -> None:
    """Failure case (same card): even with the read-once form, the first actual-value file of a session read all already-loaded sources inside the import interval of that file
    (in a local sequence of 23 files, CPU 0.941 seconds after the first actual-value file's lookup). Writing a record is a cost of the collection tooling, not the weight of the test file --
    inside the import interval only collect the names of files of loaded modules, and do the reads and hashing outside the interval."""
    result: pytest.RunResult = collect_two_dynamic_files(dynamic_project)
    assert "times inputs.py was read inside the import interval 0" in result.stdout.str(), result.stdout.str()
    assert "times inputs.py was read 1" in result.stdout.str(), result.stdout.str()


def test_dependency_stat_fast_path_hash_refresh_and_same_size_edit(tmp_path: Path) -> None:
    """Fast path of stat, re-taking the hash on a stamp-only change, and same-size rewrites. The fast path applies only to records whose last file change is earlier than the record timestamp by
    RACY_WINDOW_NS or more -- the record timestamp is decided by passing it in (with the real time right after writing, every record would
    be inside the window and use the hash)."""
    source: Path = tmp_path / "values.py"
    source.write_text("VALUE = 1\n")
    digest: str = hashlib.sha256(source.read_bytes()).hexdigest()
    later: int = source.stat().st_ctime_ns + 10 * RACY_WINDOW_NS
    saved: tuple[SourceDependency, ...] = (snapshot(source, "values.py", digest, recorded_ns=later),)
    checks: DependencyChecks = DependencyChecks()
    assert checks.verify(tmp_path, saved) == SourceSnapshot(saved)
    assert checks._mut_stat_hits == 1
    assert checks._mut_hashes == {}
    status: os.stat_result = source.stat()
    os.utime(source, ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000))
    after_stamp: int = source.stat().st_ctime_ns + 10 * RACY_WINDOW_NS
    changed_stamp: DependencyChecks = DependencyChecks(clock=lambda: after_stamp)
    refreshed: SourceSnapshot | str = changed_stamp.verify(tmp_path, saved)
    assert isinstance(refreshed, SourceSnapshot)
    assert len(changed_stamp._mut_hashes) == 1
    assert refreshed.sources[0].recorded_ns == after_stamp
    next_collection: DependencyChecks = DependencyChecks()
    assert next_collection.verify(tmp_path, refreshed.sources) == refreshed
    assert next_collection._mut_hashes == {}
    # A record taken at the current time (inside the window) is checked by hash without trusting stat -- a same-size rewrite is not missed even if the stamp is restored.
    honest: tuple[SourceDependency, ...] = (snapshot(source, "values.py", digest),)
    assert not stat_is_trusted(honest[0])
    source.write_text("VALUE = 2\n")
    os.utime(source, ns=(status.st_atime_ns, honest[0].mtime_ns))
    edited: DependencyChecks = DependencyChecks()
    assert isinstance(edited.verify(tmp_path, honest), str)
    assert edited._mut_rebuilds == 1


def test_an_old_record_without_a_recorded_time_is_checked_by_hash_once_and_rewritten(tmp_path: Path) -> None:
    """An old record without the record timestamp field (recorded_ns = 0) is checked by hash even if stat matches, and rewritten with the checked time (the fast path from next time)."""
    source: Path = tmp_path / "values.py"
    source.write_text("VALUE = 1\n")
    digest: str = hashlib.sha256(source.read_bytes()).hexdigest()
    fresh: SourceDependency = snapshot(source, "values.py", digest)
    old: SourceDependency = SourceDependency(fresh.path, fresh.digest, fresh.size, fresh.mtime_ns, fresh.ctime_ns, fresh.device, fresh.inode)
    assert old.recorded_ns == 0 and not stat_is_trusted(old)
    later: int = fresh.ctime_ns + 10 * RACY_WINDOW_NS
    checks: DependencyChecks = DependencyChecks(clock=lambda: later)
    refreshed: SourceSnapshot | str = checks.verify(tmp_path, (old,))
    assert isinstance(refreshed, SourceSnapshot)
    assert len(checks._mut_hashes) == 1 and refreshed.sources[0].recorded_ns == later
    again: DependencyChecks = DependencyChecks()
    assert again.verify(tmp_path, refreshed.sources) == refreshed
    assert again._mut_hashes == {}


def test_a_same_size_edit_within_one_coarse_timestamp_tick_is_not_missed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure case (red atlas daily run of 2026-10-10 20:05): on a kernel with coarse timestamps (atlas 6.8 -- fine ctime only from 6.13),
    a rewrite inside the same window as the time the record was taken does not advance mtime / ctime. If the size is also the same, all of stat matches, and the fast path trusted the stale record without
    reading the hash (the same shape as git's racy-git). Stand-in for the coarse kernel = a stat that keeps returning the first mtime / ctime seen (a kernel while the window
    has not ended)."""
    real_stat = Path.stat
    first_seen: dict[str, tuple[int, int]] = {}

    def coarse_stat(self: Path, *, follow_symlinks: bool = True) -> SimpleNamespace:
        status: os.stat_result = real_stat(self, follow_symlinks=follow_symlinks)
        mtime_ns, ctime_ns = first_seen.setdefault(str(self), (status.st_mtime_ns, status.st_ctime_ns))
        return SimpleNamespace(
            st_size=status.st_size, st_mtime_ns=mtime_ns, st_ctime_ns=ctime_ns, st_dev=status.st_dev, st_ino=status.st_ino
        )

    monkeypatch.setattr(Path, "stat", coarse_stat)
    source: Path = tmp_path / "values.py"
    source.write_text("VALUE = 1\n")
    saved: tuple[SourceDependency, ...] = (snapshot(source, "values.py", hashlib.sha256(source.read_bytes()).hexdigest()),)
    source.write_text("VALUE = 2\n")
    checks: DependencyChecks = DependencyChecks()
    assert checks.verify(tmp_path, saved) == "dependency source of the actual value changed: values.py"
    assert checks._mut_rebuilds == 1


def test_individual_parameter_marks_are_not_silently_dropped() -> None:
    case: ParameterSet = pytest.param(1, id="skip-me", marks=pytest.mark.skip)
    with pytest.raises(UnrecordableError, match="per-item marks"):
        parametrize_record(pytest.mark.parametrize("value", [case]).mark)
