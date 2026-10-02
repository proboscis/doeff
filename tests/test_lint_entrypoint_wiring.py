"""既存lint入口の欠測拒否と、Python/Hy差分のpre-commit選択を実行して検証する。"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT: Path = Path(__file__).resolve().parents[1]


def _linter_locked() -> ModuleType:
    """hook の doeff-linter の項が鍵を計算する module を読み、偽の crate の鍵を同じ計算で出すため。"""
    spec = importlib.util.spec_from_file_location("doeff_linter_locked", ROOT / "scripts" / "doeff_linter_locked.py")
    assert spec is not None
    assert spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass は module を sys.modules から引く
    spec.loader.exec_module(module)
    return module


LINTER_LOCKED: ModuleType = _linter_locked()


FAKE_VERSIONS: dict[str, str] = {"semgrep": "0.0.0-fake", "doeff-linter": "doeff-linter 0.0.0 (fake)"}
# 基点と比べる道(scripts/hook_finding_baseline.py)が読む JSON の答え — 所見の無い報告。
FAKE_REPORTS: dict[str, str] = {"semgrep": '{"results": [], "errors": []}', "doeff-linter": "[]"}


def _tool(directory: Path, name: str, exit_code: int, *, report: str | None = None) -> None:
    """呼ばれた引数を記録し、--version には偽の版を、JSON を求める呼びには偽の報告(report か所見の無い報告)を答える偽の道具。"""
    answer: str = report if report is not None else FAKE_REPORTS.get(name, "")
    path: Path = directory / name
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['LINT_CALLS'], 'a') as output:\n"
        "    output.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + '\\n')\n"
        "if '--version' in sys.argv:\n"
        f"    print({FAKE_VERSIONS.get(name, '0')!r})\n"
        "    raise SystemExit(0)\n"
        "if '--json' in sys.argv or 'json' in sys.argv:\n"
        f"    print({answer!r})\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


# hook の semgrep の 3 項は uv.lock の版を `uv tool run --from semgrep==<版> semgrep` で呼ぶ(#2906)。偽の uv はその呼びを記録して
# 同じ dir の偽の semgrep へ回し、ほかの呼び(`uv run --no-project python …`)は本物の uv へ渡す。
FAKE_UV: str = (
    "import json, os, sys\n"
    "args = sys.argv[1:]\n"
    "if args[:3] == ['tool', 'run', '--from'] and args[4] == 'semgrep':\n"
    "    with open(os.environ['LINT_CALLS'], 'a') as output:\n"
    "        output.write(json.dumps(['uv', *args[:5]]) + '\\n')\n"
    "    semgrep = os.path.join(os.path.dirname(sys.argv[0]), 'semgrep')\n"
    "    os.execv(semgrep, [semgrep, *args[5:]])\n"
    "os.execv(os.environ['REAL_UV'], [os.environ['REAL_UV'], *args])\n"
)


def _uv_dir(uv: str, kind: str) -> str:
    """本物の uv の置き場(`uv cache dir` / `uv python dir`)— HOME を替えた子にも同じ置き場を渡すため。"""
    return subprocess.run([uv, kind, "dir"], capture_output=True, text=True, check=True).stdout.strip()


def _environment(directory: Path) -> dict[str, str]:
    environment: dict[str, str] = dict(os.environ)
    real_uv: str | None = shutil.which("uv")
    assert real_uv is not None, "uv が要る(hook の script を走らせる)"
    fake_uv: Path = directory / "uv"
    fake_uv.write_text(f"#!{sys.executable}\n{FAKE_UV}", encoding="utf-8")
    fake_uv.chmod(0o755)
    environment.update({
        "PATH": f"{directory}:/usr/bin:/bin",
        "REAL_UV": real_uv,
        "LINT_CALLS": str(directory / "calls.jsonl"),
        "PRE_COMMIT_HOME": str(directory / "pre-commit-cache"),
        "PRE_COMMIT_ALLOW_NO_CONFIG": "0",
        # hook の doeff-linter の項は、基点の鍵の binary を HOME の下の land-arm の開発版 → 断面の置き場から探す(#2906)—
        # HOME を一時の dir に向け(開発版は _repository が置く・断面の置き場は空 = 機体の物を読まない)、uv の cache と Python の
        # 置き場は本物を指したままにする(HOME を替えても uv が取り直さない)。
        "HOME": str(directory),
        "UV_CACHE_DIR": _uv_dir(real_uv, "cache"),
        "UV_PYTHON_INSTALL_DIR": _uv_dir(real_uv, "python"),
    })
    environment.pop("SKIP", None)
    return environment


@pytest.mark.parametrize("tool_status", [None, 0, 1, 2])
def test_make_lint_doeff_propagates_missing_and_tool_failure(
    tmp_path: Path, tool_status: int | None,
) -> None:
    if tool_status is not None:
        _tool(tmp_path, "doeff-linter", tool_status)
    # packages/doeff-cluster の段(#2031・#2683)は warning を基点と比べるので、偽の linter(何も報せない)に合わせて空の基点を
    # 渡し、比べの script を走らせる uv を探し道に足す(本物の doeff-linter は探し道に入れない)。
    environment: dict[str, str] = _environment(tmp_path)
    baseline: Path = tmp_path / "empty-baseline.json"
    baseline.write_text("{}", encoding="utf-8")
    environment["DOEFF_CLUSTER_WARNING_BASELINE"] = str(baseline)
    uv: str | None = shutil.which("uv")
    assert uv is not None, "uv が要る(比べの script を走らせる)"
    environment["PATH"] = f"{tmp_path}:{Path(uv).parent}:/usr/bin:/bin"
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["/usr/bin/make", "-f", str(ROOT / "Makefile"), "lint-doeff"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=False,
    )
    assert (result.returncode == 0) == (tool_status == 0), result.stdout + result.stderr
    if tool_status is None:
        assert "doeff-linter" in result.stderr
        assert not (tmp_path / "calls.jsonl").exists()
    else:
        calls: list[list[str]] = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
        assert calls[0] == ["doeff-linter", "--no-log", "doeff/", "packages/"]
        assert calls[1] == ["doeff-linter", "--no-log"]  # packages/doeff-cluster の dir で package を丸ごと
        if tool_status == 0:
            assert calls[2] == ["doeff-linter", "--no-log", "--output-format", "json"]  # warning の基点との比べ


def _commit_fake_crate(repository: Path, directory: Path) -> str:
    """偽の linter の crate を commit し、HOME(= directory)の下の偽の land-arm の開発版をその commit から組んだ物として置き
    (探し道に置いた偽の linter の写しと記録 installed.json)、組み立ての入力の鍵を返す。"""
    crate: Path = repository / "packages" / "doeff-linter"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text('[package]\nname = "doeff-linter"\n', encoding="utf-8")
    (crate / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    subprocess.run(["/usr/bin/git", "add", "packages"], cwd=repository, check=True)
    subprocess.run(["/usr/bin/git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "crate"],
                   cwd=repository, check=True)
    commit: str = subprocess.run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=repository,
                                 capture_output=True, text=True, check=True).stdout.strip()
    dev: Path = directory / ".local" / "share" / "doeff-linter-dev"
    dev.mkdir(parents=True)
    if (directory / "doeff-linter").is_file():
        shutil.copy2(directory / "doeff-linter", dev / "doeff-linter")
    (dev / "installed.json").write_text(json.dumps({"commit": commit}), encoding="utf-8")
    key: str | None = LINTER_LOCKED.input_key(repository, commit)
    assert key is not None
    return key


def _repository(directory: Path, changed: str, source: str) -> Path:
    repository: Path = directory / "repository"
    repository.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(repository)], check=True)
    shutil.copyfile(ROOT / ".pre-commit-config.yaml", repository / ".pre-commit-config.yaml")
    # Python の semgrep と doeff-linter の項は、基点と比べる script を通る(#2848)— script と、偽の道具の版に合わせた空の基点を置く。
    (repository / "scripts" / "hook_finding_baseline").mkdir(parents=True)
    for script in ("hook_finding_baseline.py", "semgrep_locked.py", "doeff_linter_locked.py"):
        shutil.copyfile(ROOT / "scripts" / script, repository / "scripts" / script)
    # semgrep の版は uv.lock が決める(#2906)— 偽の道具の版に合わせる。
    (repository / "uv.lock").write_text(
        f'version = 1\n\n[[package]]\nname = "semgrep"\nversion = "{FAKE_VERSIONS["semgrep"]}"\n', encoding="utf-8",
    )
    # doeff-linter の版は組み立ての入力の鍵(#2906)— 偽の crate を commit し、その鍵を基点に書き、偽の開発版の記録をその commit に。
    linter_key: str = _commit_fake_crate(repository, directory)
    for tool, version in (("semgrep", FAKE_VERSIONS["semgrep"]), ("doeff-linter", linter_key)):
        (repository / "scripts" / "hook_finding_baseline" / f"{tool}.json").write_text(
            json.dumps({"version": version, "counts": {}}), encoding="utf-8",
        )
    changed_path: Path = repository / changed
    changed_path.parent.mkdir(parents=True, exist_ok=True)
    changed_path.write_text(source)
    subprocess.run(["/usr/bin/git", "add", "."], cwd=repository, check=True)
    return repository


def _pre_commit(
    directory: Path, changed: str, *, semgrep_status: int | None = 0, linter_report: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    _tool(directory, "doeff-linter", 0, report=linter_report)
    if semgrep_status is not None:
        _tool(directory, "semgrep", semgrep_status)
    repository: Path = _repository(directory, changed, "value = 1\n")
    environment: dict[str, str] = _environment(directory)
    uv: str | None = shutil.which("uv")
    assert uv is not None, "uv が要る(基点と比べる script を走らせる)"
    environment["PATH"] = f"{directory}:{Path(uv).parent}:/usr/bin:/bin"
    # 外側の `uv run pytest` の venv を子へ継がせない — 継ぐと hook の `uv run` がその venv の bin を探し道の先頭に置き、
    # 本物の semgrep が偽の道具を隠す。
    environment.pop("VIRTUAL_ENV", None)
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, "-m", "pre_commit", "run", "--files", changed],
        cwd=repository, env=environment, capture_output=True, text=True, check=False,
    )
    calls_path: Path = directory / "calls.jsonl"
    calls: list[list[str]] = (
        [json.loads(line) for line in calls_path.read_text().splitlines()]
        if calls_path.exists() else []
    )
    return result, calls


@pytest.mark.parametrize("changed", [
    "doeff/example.py", "packages/example.hy", "packages/example.hyk", "packages/example.hyp",
])
def test_pre_commit_runs_matching_linter_on_only_the_changed_file(
    tmp_path: Path, changed: str,
) -> None:
    result, calls = _pre_commit(tmp_path, changed)
    assert result.returncode == 0, result.stdout + result.stderr
    semgrep_calls: list[list[str]] = [call for call in calls if call[0] == "semgrep" and "--version" not in call]
    assert len(semgrep_calls) == 1, result.stdout + result.stderr
    # どの項も uv.lock の版を uv の道具の置き場から呼ぶ(探し道の semgrep を直に呼ばない — #2906)。
    assert [call for call in calls if call[0] == "uv"] == [["uv", "tool", "run", "--from", "semgrep==0.0.0-fake", "semgrep"]]
    assert semgrep_calls[0][-1] == changed
    # Python は基点と比べる script が JSON で数える(#2848)・Hy は semgrep が所見 1 つで止める(--error)。
    assert ("--json" if changed.endswith(".py") else "--error") in semgrep_calls[0]
    assert "doeff/" not in semgrep_calls[0]
    assert "packages/" not in semgrep_calls[0]
    python_calls: list[list[str]] = [call for call in calls if call[0] == "doeff-linter" and "--version" not in call]
    assert len(python_calls) == int(changed.endswith(".py"))


def test_python_change_with_a_linter_error_not_in_the_baseline_is_stopped(tmp_path: Path) -> None:
    # 基点(空)に無い error が変えた file に 1 つ在る → 止まる(#2848 の失敗ケース — 前からの所見だけなら通る、は script の検)。
    report: str = json.dumps([{"rule": "DOEFF016", "severity": "error", "violations": [{"file": "doeff/example.py"}]}])
    result, _ = _pre_commit(tmp_path, "doeff/example.py", linter_report=report)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "DOEFF016 doeff/example.py: 基点 0 → 今 1" in result.stdout + result.stderr


@pytest.mark.parametrize("tool_status", [None, 1, 2])
def test_hy_only_change_rejects_missing_or_failing_semgrep(
    tmp_path: Path, tool_status: int | None,
) -> None:
    result, calls = _pre_commit(tmp_path, "packages/example.hy", semgrep_status=tool_status)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not any(call[0] == "doeff-linter" for call in calls)


def test_unrelated_document_does_not_require_python_or_hy_linters(tmp_path: Path) -> None:
    result, calls = _pre_commit(tmp_path, "notes.md", semgrep_status=None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == []


@pytest.mark.parametrize("nested", [False, True])
def test_hy_only_hook_runs_real_semgrep_handler_boundary_rule(
    tmp_path: Path, nested: bool,
) -> None:
    executable: str | None = shutil.which("semgrep")
    assert executable is not None, "実検体の検査には dev 依存の semgrep が必要です"
    (tmp_path / "semgrep").symlink_to(executable)
    changed: str = "packages/example/src/handlers/example.hy"
    source: str = (
        "(defn factory []\n  (defhandler nested [] (object [] (resume None))))\n"
        if nested else "(defhandler top-level [] (object [] (resume None)))\n"
    )
    repository: Path = _repository(tmp_path, changed, source)
    rule_id: str = "doeff-hy-defhandler-must-be-top-level"
    rules = yaml.safe_load((ROOT / ".semgrep.yaml").read_text())["rules"]
    selected = [rule for rule in rules if rule["id"] == rule_id]
    assert len(selected) == 1
    (repository / ".semgrep.yaml").write_text(yaml.safe_dump({"rules": selected}))
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, "-m", "pre_commit", "run", "semgrep-hy", "--files", changed],
        cwd=repository, env=_environment(tmp_path), capture_output=True, text=True, check=False,
    )
    assert result.returncode == int(nested), result.stdout + result.stderr
    if nested:
        assert rule_id in result.stdout + result.stderr
