"""動的な値と明示idの収集記録、および古い記録を実行しない反例(#1459)。"""

import hashlib
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from _pytest.mark.structures import ParameterSet
from doeff_adr.runtime_records import UnrecordableError, parametrize_record
from doeff_adr.source_dependencies import (
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
    """実値を別moduleから読むHy testを独立processで収集・実行する。"""
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
    # 同じ秒・同じ大きさのprovider変更でもPythonのtimestamp pycを使わない。
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
    # 書いた直後の file は記録の時刻の区切りの内で、stat を信じず hash で確かめる(card ki-5b70c4b62814)。本物の作業木の file と同じく、
    # 最後の変更から区切りの幅を越えてから記録を取ると、次の収集は hash を読まない。
    time.sleep(RACY_WINDOW_NS / 1_000_000_000 + 0.1)
    cold: pytest.RunResult = collect(project)
    assert len(nodeids(cold)) == 4
    assert imports(project) == ["imported"]
    warm: pytest.RunResult = collect(project)
    assert nodeids(warm) == nodeids(cold)
    assert imports(project) == []
    assert "記録から収集 1 file・収集で import 0 file" in warm.stdout.str()
    assert "hash 0 file" in warm.stdout.str()
    assert "依存変更で再作成 0 file" in warm.stdout.str()
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
    assert "依存変更で再作成 1 file" in changed.stdout.str()
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
        '  (assert False "古い記録で本体を実行した"))\n'
    )
    monkeypatch.setenv("DYNAMIC_VALUE", "1")
    collect(project)
    assert cache_path(project).exists()
    monkeypatch.setenv("DYNAMIC_VALUE", "2")
    result: pytest.RunResult = project.runpytest_subprocess("pkg/test_dynamic.hy", "-q")
    result.assert_outcomes(errors=1)
    assert "記録と実物が食い違った" in result.stdout.str()
    assert not cache_path(project).exists()


def test_dependency_stat_fast_path_hash_refresh_and_same_size_edit(tmp_path: Path) -> None:
    """stat の速い経路・stamp だけの変化での hash の取り直し・同じ大きさの書き換え。速い経路は、file の最後の変更が記録の時刻より
    RACY_WINDOW_NS 以上前の記録だけに効く(card ki-5b70c4b62814)— 記録の時刻は渡して決める(書いた直後の本物の時刻では、どの記録も
    区切りの内で hash になる)。"""
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
    # 今の時刻で取った記録(区切りの内)は stat を信じず hash で確かめる — 同じ大きさの書き換えを、stamp を戻されても見逃さない。
    honest: tuple[SourceDependency, ...] = (snapshot(source, "values.py", digest),)
    assert not stat_is_trusted(honest[0])
    source.write_text("VALUE = 2\n")
    os.utime(source, ns=(status.st_atime_ns, honest[0].mtime_ns))
    edited: DependencyChecks = DependencyChecks()
    assert isinstance(edited.verify(tmp_path, honest), str)
    assert edited._mut_rebuilds == 1


def test_an_old_record_without_a_recorded_time_is_checked_by_hash_once_and_rewritten(tmp_path: Path) -> None:
    """記録の時刻の欄の無い古い記録(recorded_ns = 0)は、stat が一致しても hash で確かめ、確かめた時刻で書き直す(次から速い経路)。"""
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
    """失敗ケース(card ki-5b70c4b62814・atlas の 2026-10-10 20:05 の日次の赤): 時刻の粗い kernel(atlas の 6.8 — 細かい ctime は 6.13 から)
    では、記録を取った時刻と同じ区切りの中の書き換えで mtime・ctime が進まない。大きさも同じなら stat が全部一致し、速い経路は hash を
    読まずに古い記録を信じた(git の racy-git と同じ形)。粗い kernel の代わり = 最初に見た mtime・ctime を返し続ける stat(区切りが
    終わらない間の kernel)。"""
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
    assert checks.verify(tmp_path, saved) == "実値の依存sourceが変わった: values.py"
    assert checks._mut_rebuilds == 1


def test_individual_parameter_marks_are_not_silently_dropped() -> None:
    case: ParameterSet = pytest.param(1, id="skip-me", marks=pytest.mark.skip)
    with pytest.raises(UnrecordableError, match="個別の印"):
        parametrize_record(pytest.mark.parametrize("value", [case]).mark)
