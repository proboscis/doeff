"""既存lint入口の欠測拒否と、Python/Hy差分のpre-commit選択を実行して検証する。"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from doeff_core_effects import EnvEntry, EnvMode, ProcessOutcome, RunProcess, subprocess_handler

from doeff import run, with_handlers

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


# hook の python の宣言(.pre-commit-config.yaml の `uv run --python` の版 — card acp:kanban-issue:ki-a28a482132ab)。
HOOK_PYTHON: str = "3.12"
FAKE_VERSIONS: dict[str, str] = {"semgrep": "0.0.0-fake", "doeff-linter": "doeff-linter 0.0.0 (fake)"}
# 偽の lock の行の source — 道具の版の読み手(scripts/semgrep_locked.py)は index から入れる版の行だけを読む(card ki-33d2441b7e28)。
FAKE_SOURCE: str = 'source = { registry = "https://pypi.org/simple" }\n'
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


# hook の semgrep の 3 項は uv.lock の版を `uv tool run --python <版> --from semgrep==<版> semgrep` で呼ぶ(#2906)。偽の uv はその
# 呼びを記録して同じ dir の偽の semgrep へ回し、ほかの呼び(`uv run --no-project --python … python …`)は本物の uv へ渡す。
# 素の hook(scripts/git-hooks/pre-commit)は pre-commit も同じ形で呼ぶ — 偽の uv は記録して検の環境の pre-commit へ回す。
FAKE_UV: str = (
    "import json, os, sys\n"
    "args = sys.argv[1:]\n"
    "if args[:3] == ['tool', 'run', '--python'] and args[4] == '--from' and args[6] in ('semgrep', 'pre-commit'):\n"
    "    with open(os.environ['LINT_CALLS'], 'a') as output:\n"
    "        output.write(json.dumps(['uv', *args[:7]]) + '\\n')\n"
    "    if args[6] == 'pre-commit':\n"
    "        os.execv(sys.executable, [sys.executable, '-m', 'pre_commit', *args[7:]])\n"
    "    semgrep = os.path.join(os.path.dirname(sys.argv[0]), 'semgrep')\n"
    "    os.execv(semgrep, [semgrep, *args[7:]])\n"
    "os.execv(os.environ['REAL_UV'], [os.environ['REAL_UV'], *args])\n"
)


def _uv_dir(uv: str, kind: str) -> str:
    """本物の uv の置き場(`uv cache dir` / `uv python dir`)— HOME を替えた子にも同じ置き場を渡すため。"""
    return subprocess.run([uv, kind, "dir"], capture_output=True, text=True, check=True).stdout.strip()


def _environment(directory: Path, path: str | None = None) -> tuple[EnvEntry, ...]:
    """子の環境へ今の環境の上に重ねる分(path = 子の PATH・既定は偽の道具の dir と /usr/bin・/bin)。今の環境の受け継ぎと
    外す名(SKIP など)は、子を起こす doeff の答え手が行う(_run_child・agora-redesign #3012)。"""
    real_uv: str | None = shutil.which("uv")
    assert real_uv is not None, "uv が要る(hook の script を走らせる)"
    fake_uv: Path = directory / "uv"
    fake_uv.write_text(f"#!{sys.executable}\n{FAKE_UV}", encoding="utf-8")
    fake_uv.chmod(0o755)
    return (
        EnvEntry(name="PATH", value=path if path is not None else f"{directory}:/usr/bin:/bin"),
        EnvEntry(name="REAL_UV", value=real_uv),
        EnvEntry(name="LINT_CALLS", value=str(directory / "calls.jsonl")),
        EnvEntry(name="PRE_COMMIT_HOME", value=str(directory / "pre-commit-cache")),
        EnvEntry(name="PRE_COMMIT_ALLOW_NO_CONFIG", value="0"),
        # hook の doeff-linter の項は、基点の鍵の binary を HOME の下の land-arm の開発版 → 断面の置き場から探す(#2906)—
        # HOME を一時の dir に向け(開発版は _repository が置く・断面の置き場は空 = 機体の物を読まない)、uv の cache と Python の
        # 置き場は本物を指したままにする(HOME を替えても uv が取り直さない)。
        EnvEntry(name="HOME", value=str(directory)),
        EnvEntry(name="UV_CACHE_DIR", value=_uv_dir(real_uv, "cache")),
        EnvEntry(name="UV_PYTHON_INSTALL_DIR", value=_uv_dir(real_uv, "python")),
    )


def _run_child(
    argv: tuple[str, ...], cwd: Path, overlay: tuple[EnvEntry, ...], drop: tuple[str, ...] = ("SKIP",),
) -> ProcessOutcome:
    """子を doeff の子 process の答え手(subprocess_handler)で起こすため — 子の環境は今の環境から drop の名を外し、overlay を
    重ねた物(受け継ぎは答え手の中で行う・agora-redesign #3012)。"""
    return run(with_handlers(
        [subprocess_handler],
        RunProcess(argv=argv, cwd=str(cwd), env=overlay, env_mode=EnvMode.EXTEND, env_drop=drop),
    ))


@pytest.mark.parametrize("tool_status", [None, 0, 1, 2])
def test_make_lint_doeff_propagates_missing_and_tool_failure(
    tmp_path: Path, tool_status: int | None,
) -> None:
    if tool_status is not None:
        _tool(tmp_path, "doeff-linter", tool_status)
    # packages/doeff-cluster の段(#2031・#2683)は warning を基点と比べるので、偽の linter(何も報せない)に合わせて空の基点を
    # 渡し、比べの script を走らせる uv を探し道に足す(本物の doeff-linter は探し道に入れない)。
    baseline: Path = tmp_path / "empty-baseline.json"
    baseline.write_text("{}", encoding="utf-8")
    uv: str | None = shutil.which("uv")
    assert uv is not None, "uv が要る(比べの script を走らせる)"
    environment: tuple[EnvEntry, ...] = (
        *_environment(tmp_path, f"{tmp_path}:{Path(uv).parent}:/usr/bin:/bin"),
        EnvEntry(name="DOEFF_CLUSTER_WARNING_BASELINE", value=str(baseline)),
    )
    result: ProcessOutcome = _run_child(
        ("/usr/bin/make", "-f", str(ROOT / "Makefile"), "lint-doeff"), tmp_path, environment,
    )
    assert (result.exit_code == 0) == (tool_status == 0), result.stdout + result.stderr
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
    # 木の python の宣言(3.14t)も写す — hook の python は設定の宣言で決まり、木の既定に依らない(card acp:kanban-issue:ki-a28a482132ab)。
    shutil.copyfile(ROOT / ".python-version", repository / ".python-version")
    # Python の semgrep と doeff-linter の項は、基点と比べる script を通る(#2848)— script と、偽の道具の版に合わせた空の基点を置く。
    (repository / "scripts" / "hook_finding_baseline").mkdir(parents=True)
    for script in ("hook_finding_baseline.py", "semgrep_locked.py", "doeff_linter_locked.py"):
        shutil.copyfile(ROOT / "scripts" / script, repository / "scripts" / script)
    # semgrep の版は uv.lock が決める(#2906)— 偽の道具の版に合わせる(行は index から入れる版 — 読み手は index の外の行で止まる)。
    (repository / "uv.lock").write_text(
        f'version = 1\n\n[[package]]\nname = "semgrep"\nversion = "{FAKE_VERSIONS["semgrep"]}"\n{FAKE_SOURCE}', encoding="utf-8",
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


@dataclass(frozen=True)
class _HookRun:
    """pre-commit を 1 回走らせた結果(result)と、偽の道具が受けた呼びの列(calls — 1 つの呼びは引数の列)。"""

    result: ProcessOutcome
    calls: list[list[str]]


def _pre_commit(
    directory: Path, changed: str, *, semgrep_status: int | None = 0, linter_report: str | None = None,
) -> _HookRun:
    """偽の道具を道の先頭に置いた検体の repo で、changed だけを pre-commit に渡して走らせるため。"""
    _tool(directory, "doeff-linter", 0, report=linter_report)
    if semgrep_status is not None:
        _tool(directory, "semgrep", semgrep_status)
    repository: Path = _repository(directory, changed, "value = 1\n")
    uv: str | None = shutil.which("uv")
    assert uv is not None, "uv が要る(基点と比べる script を走らせる)"
    # 外側の `uv run pytest` の venv(VIRTUAL_ENV)を子へ継がせない — 継ぐと hook の `uv run` がその venv の bin を探し道の先頭に
    # 置き、本物の semgrep が偽の道具を隠す。
    result: ProcessOutcome = _run_child(
        (sys.executable, "-m", "pre_commit", "run", "--files", changed),
        repository,
        _environment(directory, f"{directory}:{Path(uv).parent}:/usr/bin:/bin"),
        ("SKIP", "VIRTUAL_ENV"),
    )
    calls_path: Path = directory / "calls.jsonl"
    calls: list[list[str]] = (
        [json.loads(line) for line in calls_path.read_text().splitlines()]
        if calls_path.exists() else []
    )
    return _HookRun(result=result, calls=calls)


@pytest.mark.parametrize("changed", [
    "doeff/example.py", "packages/example.hy", "packages/example.hyk", "packages/example.hyp",
])
def test_pre_commit_runs_matching_linter_on_only_the_changed_file(
    tmp_path: Path, changed: str,
) -> None:
    hook = _pre_commit(tmp_path, changed)
    assert hook.result.exit_code == 0, hook.result.stdout + hook.result.stderr
    semgrep_calls: list[list[str]] = [call for call in hook.calls if call[0] == "semgrep" and "--version" not in call]
    assert len(semgrep_calls) == 1, hook.result.stdout + hook.result.stderr
    # どの項も uv.lock の版を uv の道具の置き場から、設定が宣言した python で呼ぶ(探し道の semgrep を直に呼ばない — #2906・木の
    # .python-version の 3.14t では ruamel-yaml-clib が組めない — card acp:kanban-issue:ki-a28a482132ab)。
    assert [call for call in hook.calls if call[0] == "uv"] == [
        ["uv", "tool", "run", "--python", HOOK_PYTHON, "--from", "semgrep==0.0.0-fake", "semgrep"],
    ]
    assert semgrep_calls[0][-1] == changed
    # Python は基点と比べる script が JSON で数える(#2848)・Hy は semgrep が所見 1 つで止める(--error)。
    assert ("--json" if changed.endswith(".py") else "--error") in semgrep_calls[0]
    assert "doeff/" not in semgrep_calls[0]
    assert "packages/" not in semgrep_calls[0]
    python_calls: list[list[str]] = [call for call in hook.calls if call[0] == "doeff-linter" and "--version" not in call]
    assert len(python_calls) == int(changed.endswith(".py"))


def test_python_change_with_a_linter_error_not_in_the_baseline_is_stopped(tmp_path: Path) -> None:
    # 基点(空)に無い error が変えた file に 1 つ在る → 止まる(#2848 の失敗ケース — 前からの所見だけなら通る、は script の検)。
    report: str = json.dumps([{"rule": "DOEFF016", "severity": "error", "violations": [{"file": "doeff/example.py"}]}])
    result = _pre_commit(tmp_path, "doeff/example.py", linter_report=report).result
    assert result.exit_code != 0, result.stdout + result.stderr
    assert "DOEFF016 doeff/example.py: 基点 0 → 今 1" in result.stdout + result.stderr


@pytest.mark.parametrize("tool_status", [None, 1, 2])
def test_hy_only_change_rejects_missing_or_failing_semgrep(
    tmp_path: Path, tool_status: int | None,
) -> None:
    hook = _pre_commit(tmp_path, "packages/example.hy", semgrep_status=tool_status)
    assert hook.result.exit_code != 0, hook.result.stdout + hook.result.stderr
    assert not any(call[0] == "doeff-linter" for call in hook.calls)


def test_unrelated_document_does_not_require_python_or_hy_linters(tmp_path: Path) -> None:
    hook = _pre_commit(tmp_path, "notes.md", semgrep_status=None)
    assert hook.result.exit_code == 0, hook.result.stdout + hook.result.stderr
    assert hook.calls == []


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
    result: ProcessOutcome = _run_child(
        (sys.executable, "-m", "pre_commit", "run", "semgrep-hy", "--files", changed),
        repository, _environment(tmp_path),
    )
    assert result.exit_code == int(nested), result.stdout + result.stderr
    if nested:
        assert rule_id in result.stdout + result.stderr


# 素の commit の hook(scripts/git-hooks/pre-commit)— make hooks-install と agora の担当の Pod の準備(agora の
# deploy/agent-worker/worker-prepare.sh)が入れる物。pre-commit の枠組みを据え付けない機体でも、commit は semgrep の規則を通る
# (card ki-8c0bb496bfab — 10-11 の日次の 5 回の赤は、Pod の commit が枠組みの semgrep の項を通らずに入れた業務の語)。
PLAIN_HOOK: Path = ROOT / "scripts" / "git-hooks" / "pre-commit"
# 素の hook が撃つ pre-commit の項の別名 — .pre-commit-config.yaml の semgrep の項はどれもこの別名を持つ。
PLAIN_HOOK_ALIAS: str = "semgrep-rules"
VOCABULARY_RULE: str = "doeff-packages-have-no-application-vocabulary"
# 素の hook の検の repo の uv.lock — 道具の版は木の uv.lock が決める(#2906)。偽の uv は版を見ずに回す。
FAKE_LOCKED: dict[str, str] = {"semgrep": FAKE_VERSIONS["semgrep"], "pre-commit": "0.0.0-fake"}


def _plain_hook_repository(
    directory: Path, changed: str, source: str, rule_packages: tuple[str, ...] = (), *, fake_linter: bool = False,
) -> Path:
    """素の hook が読む物(scripts/ の追跡している file・hook の設定・木の python の宣言)と、業務の語の規則だけの .semgrep.yaml を
    写して commit した repo に、changed だけを stage するため(台帳の突合は docs/adr と .semgrep.yaml に触れた commit だけが撃つ)。
    rule_packages = 検の repo の規則の paths.include にだけ足す package(項は写したまま — 規則に package を足す便の形)。
    fake_linter = 偽の linter の crate を commit し、その鍵を基点(空)に書く(_tool で置いた偽の linter が開発版として見つかる)。"""
    repository: Path = directory / "repository"
    repository.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(repository)], check=True)
    tracked: list[str] = subprocess.run(
        ["/usr/bin/git", "ls-files", "scripts"], cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    for name in (*tracked, ".pre-commit-config.yaml", ".python-version"):
        (repository / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, repository / name)
    rules = yaml.safe_load((ROOT / ".semgrep.yaml").read_text())["rules"]
    vocabulary = [rule for rule in rules if rule["id"] == VOCABULARY_RULE]
    vocabulary[0]["paths"]["include"].extend(f"/packages/{package}/**" for package in rule_packages)
    (repository / ".semgrep.yaml").write_text(yaml.safe_dump({"rules": vocabulary}), encoding="utf-8")
    (repository / "uv.lock").write_text(
        "version = 1\n"
        + "".join(
            f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n{FAKE_SOURCE}' for name, version in FAKE_LOCKED.items()
        ),
        encoding="utf-8",
    )
    # Python の semgrep の項は基点と比べる(#2848)— 偽の版の空の基点にする(本線の基点の所見を検に持ち込まない)。
    (repository / "scripts" / "hook_finding_baseline" / "semgrep.json").write_text(
        json.dumps({"version": FAKE_VERSIONS["semgrep"], "counts": {}}), encoding="utf-8",
    )
    if fake_linter:
        (repository / "scripts" / "hook_finding_baseline" / "doeff-linter.json").write_text(
            json.dumps({"version": _commit_fake_crate(repository, directory), "counts": {}}), encoding="utf-8",
        )
    subprocess.run(["/usr/bin/git", "add", "."], cwd=repository, check=True)
    subprocess.run(["/usr/bin/git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "base"],
                   cwd=repository, check=True)
    changed_path: Path = repository / changed
    changed_path.parent.mkdir(parents=True, exist_ok=True)
    changed_path.write_text(source, encoding="utf-8")
    subprocess.run(["/usr/bin/git", "add", changed], cwd=repository, check=True)
    return repository


def _plain_hook_with_a_linter(directory: Path, changed: str, report: str) -> _HookRun:
    """素の hook を、偽の doeff-linter(report を答える)と、その鍵を名乗る空の基点のある検の repo で走らせ、偽の道具が受けた
    呼びの列も返すため(linter の所見が基点を越えるかを、素の hook が commit の時に比べるかを見る)。"""
    _tool(directory, "doeff-linter", 0, report=report)
    executable: str | None = shutil.which("semgrep")
    assert executable is not None, "実検体の検査には dev 依存の semgrep が必要です"
    (directory / "semgrep").symlink_to(executable)
    repository: Path = _plain_hook_repository(directory, changed, "value = 1\n", fake_linter=True)
    uv: str | None = shutil.which("uv")
    assert uv is not None, "uv が要る(hook の script を走らせる)"
    result: ProcessOutcome = _run_child(
        ("/bin/sh", str(PLAIN_HOOK)), repository,
        _environment(directory, f"{directory}:{Path(uv).parent}:/usr/bin:/bin"), ("SKIP", "VIRTUAL_ENV"),
    )
    calls_path: Path = directory / "calls.jsonl"
    calls: list[list[str]] = (
        [json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
    )
    return _HookRun(result=result, calls=calls)


def _plain_hook(directory: Path, changed: str, source: str, rule_packages: tuple[str, ...] = ()) -> ProcessOutcome:
    """本物の semgrep を偽の uv の隣に置いた検の repo で、素の hook を git が撃つのと同じく repo の根で走らせるため。"""
    executable: str | None = shutil.which("semgrep")
    assert executable is not None, "実検体の検査には dev 依存の semgrep が必要です"
    (directory / "semgrep").symlink_to(executable)
    repository: Path = _plain_hook_repository(directory, changed, source, rule_packages)
    uv: str | None = shutil.which("uv")
    assert uv is not None, "uv が要る(hook の script を走らせる)"
    return _run_child(
        ("/bin/sh", str(PLAIN_HOOK)), repository,
        _environment(directory, f"{directory}:{Path(uv).parent}:/usr/bin:/bin"), ("SKIP", "VIRTUAL_ENV"),
    )


# 10-11 の日次の赤の形 — 汎用の package の Hy の註(5431b983e・f3f569e9d・7438f793f)・Python の docstring(abae18074・
# 0e39b2cbd)と、枠組みが Python と Hy の外から渡す文書の 3 つ。名が ASCII の外の文書も同じく止める(git は既定でその名を
# 引用符つきで出す — stage の名を自分で読んで選ぶ hook は、そこで黙って通す)。
@pytest.mark.parametrize(("changed", "source"), [
    pytest.param("packages/doeff-cluster/src/doeff_cluster/example.hy",
                 ";; 起き直しの lease(agora-controllers の card ki-0)\n(setv value 1)\n", id="hy-comment"),
    pytest.param("packages/doeff-codex/tests/test_example.py",
                 '"""使い手の repo(agora-controllers)の日次の赤。"""\n\nvalue = 1\n', id="python-docstring"),
    pytest.param("packages/doeff-records/README.md", "宣言の欄(agora の kanban の行)\n", id="document"),
    pytest.param("packages/doeff-records/説明.md", "宣言の欄(agora の kanban の行)\n", id="document-non-ascii-name"),
])
def test_plain_hook_stops_staged_application_vocabulary(tmp_path: Path, changed: str, source: str) -> None:
    result: ProcessOutcome = _plain_hook(tmp_path, changed, source)
    output: str = result.stdout + result.stderr
    assert result.exit_code != 0, f"素の hook が業務の語を足した stage を通した:\n{output}"
    assert VOCABULARY_RULE in output, output


@pytest.mark.parametrize(("changed", "source"), [
    pytest.param("packages/doeff-cluster/src/doeff_cluster/example.hy",
                 ";; 起き直しの lease(card ki-0)\n(setv value 1)\n", id="hy-comment"),
    pytest.param("packages/doeff-records/README.md", "宣言の欄(#2830)\n", id="document"),
])
def test_plain_hook_passes_staged_file_without_findings(tmp_path: Path, changed: str, source: str) -> None:
    result: ProcessOutcome = _plain_hook(tmp_path, changed, source)
    assert result.exit_code == 0, result.stdout + result.stderr


# どの package に業務の語の規則を当てるかは、規則の paths.include の 1 か所が決める — 項は package の名を持たない。持っていた
# 時は、規則に package を足した便(10-10 の doeff-codex は規則と項を別の commit で足した)が項を直し忘れると、素の hook がその
# package の文書を黙って通した(設計検証 lt-Z4J4B9281EK68D2Q8PZAJVZAV5 の盲検 A)。
PACKAGE_ADDED_TO_THE_RULE: str = "doeff-example-generic"


@pytest.mark.parametrize(("rule_packages", "stops"), [
    pytest.param((PACKAGE_ADDED_TO_THE_RULE,), True, id="package-added-to-the-rule"),
    pytest.param((), False, id="package-outside-the-rule"),
])
def test_plain_hook_follows_the_rule_paths_for_documents(
    tmp_path: Path, rule_packages: tuple[str, ...], stops: bool,
) -> None:
    result: ProcessOutcome = _plain_hook(
        tmp_path, f"packages/{PACKAGE_ADDED_TO_THE_RULE}/README.md", "宣言の欄(agora の kanban の行)\n", rule_packages,
    )
    output: str = result.stdout + result.stderr
    assert (result.exit_code != 0) == stops, output
    assert (VOCABULARY_RULE in output) == stops, output


def test_plain_hook_leaves_file_selection_to_the_config(tmp_path: Path) -> None:
    # 素の hook は stage した file を自分で選ばず、どの項にも当たらない stage でも pre-commit を起こす — file を選ぶのは項の
    # files / types の 1 か所(設計検証 lt-Z4J4B9281EK68D2Q8PZAJVZAV5 の盲検 B: 「当たる file が無ければ起こさない」近道を hook に
    # 足すと、名が ASCII の外の文書や、項に足した file 種を黙って通した)。
    hooks = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"][0]["hooks"]
    names: list[str] = [hook["name"] for hook in hooks if hook.get("alias") == PLAIN_HOOK_ALIAS]
    result: ProcessOutcome = _plain_hook(tmp_path, "notes/plain.txt", "宣言の欄\n")
    output: str = result.stdout + result.stderr
    assert names
    assert result.exit_code == 0, output
    assert [name for name in names if name not in output] == [], output


def test_plain_hook_runs_every_semgrep_hook_of_the_config() -> None:
    # 素の hook は semgrep の項を名で並べず別名で撃つ — semgrep の項を足した便が別名を書き忘れると、素の hook だけの機体
    # (agora の担当の Pod)でその項が黙って抜ける。
    hooks = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"][0]["hooks"]
    semgrep_hooks: list[str] = sorted(hook["id"] for hook in hooks if "semgrep" in hook["entry"])
    aliased: list[str] = sorted(hook["id"] for hook in hooks if hook.get("alias") == PLAIN_HOOK_ALIAS)
    assert semgrep_hooks
    assert aliased == semgrep_hooks
    assert f"run {PLAIN_HOOK_ALIAS}" in PLAIN_HOOK.read_text(encoding="utf-8")


# 素の hook は doeff-linter の基点の比べの項も撃つ(card ki-c6f026ac047e)— 10-18 の 3 commit は Pod(素の hook だけ)から入り、
# 検の file 2 つに DOEFF004 の所見を 9 件足したのに、素の hook が linter の項を撃たず、日次(10-19 04:05 JST)で初めて見つかった。
# 着地の門は衝突の印だけを見る(operator 裁定 land-gate-runs-no-tests)ので、基点を越える所見を止められる最初の点は commit の時。
PLAIN_HOOK_LINTER_ID: str = "doeff-linter"


def test_plain_hook_runs_the_doeff_linter_baseline_hook_of_the_config() -> None:
    # 基点の比べ(hook_finding_baseline.py check doeff-linter)を持つ項は 1 つで、素の hook はその項を id で撃つ。項の id を
    # 替えた便が素の hook の呼びを直し忘れると、素の hook だけの機体(agora の担当の Pod)で linter の項が黙って抜ける。
    hooks = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"][0]["hooks"]
    linter_hooks: list[str] = [hook["id"] for hook in hooks if "hook_finding_baseline.py check doeff-linter" in hook["entry"]]
    assert linter_hooks == [PLAIN_HOOK_LINTER_ID]
    assert f"run {PLAIN_HOOK_LINTER_ID}" in PLAIN_HOOK.read_text(encoding="utf-8")


def test_plain_hook_stops_a_staged_python_file_with_a_linter_finding_not_in_the_baseline(tmp_path: Path) -> None:
    # 基点(空)に無い error が stage した file に 1 つ在る → 素の hook が止める(失敗ケース — 素の hook が linter の項を撃たない時は通る)。
    report: str = json.dumps([{"rule": "DOEFF016", "severity": "error", "violations": [{"file": "doeff/example.py"}]}])
    result: ProcessOutcome = _plain_hook_with_a_linter(tmp_path, "doeff/example.py", report).result
    output: str = result.stdout + result.stderr
    assert result.exit_code != 0, f"素の hook が基点を越える linter の所見を通した:\n{output}"
    assert "DOEFF016 doeff/example.py: 基点 0 → 今 1" in output, output


def test_plain_hook_runs_the_linter_once_on_only_the_staged_python_file(tmp_path: Path) -> None:
    hook: _HookRun = _plain_hook_with_a_linter(tmp_path, "doeff/example.py", "[]")
    assert hook.result.exit_code == 0, hook.result.stdout + hook.result.stderr
    runs: list[list[str]] = [call for call in hook.calls if call[0] == "doeff-linter" and "--version" not in call]
    assert len(runs) == 1, hook.calls
    assert runs[0][-1] == "doeff/example.py"
    assert "doeff/" not in runs[0]


def test_every_uv_hook_declares_the_hook_python() -> None:
    # uv で走る項は、どれも hook の python を宣言する — 宣言が無いと uv は木の .python-version(3.14t)を選び、semgrep の依存の
    # ruamel-yaml-clib が組めずに落ちる(Pod 2026-10-10・card acp:kanban-issue:ki-a28a482132ab)。
    hooks = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"][0]["hooks"]
    uv_entries: list[str] = [hook["entry"] for hook in hooks if hook["entry"].startswith("uv ")]
    assert uv_entries
    assert [entry for entry in uv_entries if f"--python {HOOK_PYTHON}" not in entry] == []
