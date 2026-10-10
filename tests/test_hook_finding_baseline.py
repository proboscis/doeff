"""commit の hook の doeff-linter と semgrep(Python)の所見を基点と比べる道具(scripts/hook_finding_baseline.py・agora-redesign #2848)の検。

失敗ケース(#2848 の決め): 前からの所見の在る file に新しい所見を 1 つ足すと止まる・足さなければ通る・前からの所見を直したのに
基点が古いと赤。基点の鍵は行番号に依らない。
doeff-linter(#2906 の 2 便目の失敗ケース): 探し道の linter は呼ばず、基点の鍵と同じ組み立ての入力の binary を land-arm の開発版 →
断面の置き場から探す・鍵は linter の検だけの変更では動かず path 依存(indexer)の src で動く・置き場に無ければ「測れない」と
名指して通す・lower は渡した build の鍵を書く・HEAD の鍵の binary が無ければ書かずに止まる。
semgrep(#2906 の失敗ケース): 探し道の semgrep の版に依らず uv.lock の版を呼ぶ・lock の版が基点と違えば名指して赤・lock に semgrep が
無ければ探し道へ戻らず止まる。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import tomllib

ROOT: Path = Path(__file__).resolve().parents[1]
SCRIPT: Path = ROOT / "scripts" / "hook_finding_baseline.py"
LOCKED: Path = ROOT / "scripts" / "semgrep_locked.py"


def _module(name: str, path: Path) -> ModuleType:
    """script を module として読み、純粋な関数を直に試すため(同じ dir の module を import する script は、先にその module を読む)。"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SEMGREP_LOCKED = _module("semgrep_locked", LOCKED)
LINTER_LOCKED = _module("doeff_linter_locked", ROOT / "scripts" / "doeff_linter_locked.py")
TOOL = _module("hook_finding_baseline", SCRIPT)
BASE: dict[str, dict[str, int]] = {"DOEFF016": {"pkg/a.py": 2, "pkg/b.py": 1}}


def test_a_new_finding_in_a_file_with_old_findings_is_red() -> None:
    comparison = TOOL.compare(BASE, {"DOEFF016": {"pkg/a.py": 3, "pkg/b.py": 1}}, None)
    assert [(f.path, f.baseline, f.current) for f in comparison.grown] == [("pkg/a.py", 2, 3)]
    assert comparison.stale == ()


def test_the_same_findings_on_moved_lines_pass() -> None:
    # 鍵は file × 規則の数 — 行がずれただけ(数が同じ)なら比べに出ない。
    comparison = TOOL.compare(BASE, {"DOEFF016": {"pkg/a.py": 2, "pkg/b.py": 1}}, None)
    assert comparison.grown == () and comparison.stale == ()


def test_a_fix_without_lowering_the_baseline_is_red() -> None:
    comparison = TOOL.compare(BASE, {"DOEFF016": {"pkg/a.py": 2}}, None)
    assert comparison.grown == ()
    assert [(f.path, f.baseline, f.current) for f in comparison.stale] == [("pkg/b.py", 1, 0)]


def test_only_the_measured_files_are_compared() -> None:
    # commit の hook は commit に入る file だけを測る — 測っていない b.py が 0 に見えても下げ忘れにしない。
    comparison = TOOL.compare(BASE, {"DOEFF016": {"pkg/a.py": 2}}, frozenset({"pkg/a.py"}))
    assert comparison.grown == () and comparison.stale == ()


def test_lower_only_lowers() -> None:
    current = {"DOEFF016": {"pkg/a.py": 5, "pkg/b.py": 0}, "DOEFF021": {"pkg/c.py": 4}}
    assert TOOL.lowered(BASE, current) == {"DOEFF016": {"pkg/a.py": 2}}


def test_only_linter_errors_are_counted_and_paths_are_from_the_root(tmp_path: Path) -> None:
    report = [
        {"rule": "DOEFF016", "severity": "error",
         "violations": [{"file": str(tmp_path / "pkg" / "a.py")}, {"file": "pkg/a.py"}, {"file": "pkg/b.py"}]},
        {"rule": "DOEFF009", "severity": "warning", "violations": [{"file": "pkg/a.py"}]},
    ]
    assert TOOL.linter_error_counts(report, tmp_path) == {"DOEFF016": {"pkg/a.py": 2, "pkg/b.py": 1}}


def test_semgrep_results_are_counted_by_the_rule_name(tmp_path: Path) -> None:
    report = {"results": [{"check_id": "tmp.doeff-no-sleep-in-tests", "path": "tests/t.py"},
                          {"check_id": "doeff-no-sleep-in-tests", "path": "tests/t.py"}], "errors": []}
    assert TOOL.semgrep_counts(report, tmp_path) == {"doeff-no-sleep-in-tests": {"tests/t.py": 2}}


# --- 入口から(一時の git repo と、file の中の BAD の数を error として名乗る偽の linter)-----------------------------------
# doeff-linter は、基点の鍵と同じ組み立ての入力の binary を land-arm の開発版 → 断面の置き場から探して呼ぶ(#2906)。rig は一時の repo に
# 偽の crate(linter と path 依存の indexer)を commit し、HOME を一時の dir に向けて偽の開発版・断面の置き場を置く。探し道の
# doeff-linter は呼ばれたら印を残す。

FAKE_LINTER = """\
import json, sys
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print(Path(__file__).with_name("version").read_text().strip())
    raise SystemExit(0)
with open(Path(__file__).with_name("calls"), "a") as out:
    out.write(" ".join(sys.argv[1:]) + "\\n")
paths = [a for a in sys.argv[1:] if a.endswith(".py")]
violations = [{"file": p} for p in paths for line in Path(p).read_text().splitlines() if "BAD" in line]
print(json.dumps([{"rule": "DOEFF999", "severity": "error", "violations": violations}]))
raise SystemExit(1 if violations else 0)
"""

FAKE_PATH_LINTER = """\
from pathlib import Path
Path(__file__).with_name("called").write_text("探し道の doeff-linter が呼ばれた\\n")
raise SystemExit("探し道の doeff-linter が呼ばれた")
"""

CRATES: dict[str, str] = {
    "packages/doeff-linter/Cargo.toml": '[package]\nname = "doeff-linter"\n\n[dependencies]\ndoeff-indexer = { path = "../doeff-indexer" }\n',
    "packages/doeff-linter/src/main.rs": "fn main() {}\n",
    "packages/doeff-linter/tests/t.rs": "// 検\n",
    "packages/doeff-indexer/Cargo.toml": '[package]\nname = "doeff-indexer"\n',
    "packages/doeff-indexer/src/lib.rs": "// indexer\n",
}


@dataclass(frozen=True)
class Rig:
    """一時の git repo の根・子の環境(呼び手の環境を写さない)・偽の land-arm の開発版と断面の置き場。"""

    repo: Path
    environment: dict[str, str]
    dev: Path
    snapshots: Path


def _commit(rig: Rig, message: str) -> str:
    """一時の repo の今の木を commit して sha を返すため。"""
    subprocess.run(["git", "add", "-A"], cwd=rig.repo, env=rig.environment, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", message],
                   cwd=rig.repo, env=rig.environment, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=rig.repo, env=rig.environment,
                          capture_output=True, text=True, check=True).stdout.strip()


def _place_linter(directory: Path, commit: str) -> Path:
    """偽の linter を directory に置き、commit から組んだ物として名乗らせるため。"""
    directory.mkdir(parents=True, exist_ok=True)
    linter = directory / "doeff-linter"
    linter.write_text(f"#!{sys.executable}\n{FAKE_LINTER}")
    linter.chmod(0o755)
    (directory / "version").write_text(f"doeff-linter 0.0.0 (doeff {commit})\n")
    return linter


def _install_dev(rig: Rig, commit: str) -> None:
    """偽の land-arm の開発版を commit の物に置き換えるため(記録 installed.json の commit も)。"""
    _place_linter(rig.dev, commit)
    (rig.dev / "installed.json").write_text(json.dumps({"commit": commit}))


def _change_linter_source(rig: Rig, text: str) -> str:
    """linter の src を変えて commit するため(組み立ての入力の鍵が動く)。"""
    (rig.repo / "packages" / "doeff-linter" / "src" / "main.rs").write_text(text)
    return _commit(rig, "linter の src を変える")


def _rig(tmp_path: Path) -> Rig:
    """一時の git repo(偽の crate と pkg の Python)と、その HEAD から組んだ偽の開発版を作るため。"""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("x = 1  # BAD\ny = 2\n")
    (repo / "pkg" / "b.py").write_text("z = 3\n")
    for relative, text in CRATES.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(text)
    fake = tmp_path / "bin"
    fake.mkdir()
    path_linter = fake / "doeff-linter"
    path_linter.write_text(f"#!{sys.executable}\n{FAKE_PATH_LINTER}")
    path_linter.chmod(0o755)
    # 置き場は HOME の下の決まった場所(scripts/doeff_linter_locked.py)— HOME を一時の dir に向けて差し替える。
    environment = {"PATH": f"{fake}{os.pathsep}{os.defpath}", "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path)}
    subprocess.run(["git", "init", "-q"], cwd=repo, env=environment, check=True)
    rig = Rig(repo=repo, environment=environment, dev=tmp_path / ".local" / "share" / "doeff-linter-dev",
              snapshots=tmp_path / ".cache" / "doeff-linter-snapshots")
    _install_dev(rig, _commit(rig, "はじめ"))
    return rig


def _run(rig: Rig, *argv: str) -> subprocess.CompletedProcess[str]:
    """script を一時の repo の根で走らせるため。"""
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(rig.repo), *argv],
                          env=rig.environment, capture_output=True, text=True, check=False)


def _baseline_version(rig: Rig) -> str:
    """一時の repo の doeff-linter の基点が名乗る版。"""
    return json.loads((rig.repo / "scripts" / "hook_finding_baseline" / "doeff-linter.json").read_text())["version"]


def test_the_hook_stops_only_a_new_finding_and_a_stale_baseline(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    baseline = json.loads((rig.repo / "scripts" / "hook_finding_baseline" / "doeff-linter.json").read_text())
    assert baseline["counts"] == {"DOEFF999": {"pkg/a.py": 1}}

    # 前からの所見だけ(足さない)→ 通る
    assert _run(rig, "check", "doeff-linter", "pkg/a.py").returncode == 0
    # 前からの所見の在る file に新しい所見を 1 つ足す → 止まる
    (rig.repo / "pkg" / "a.py").write_text("x = 1  # BAD\ny = 2  # BAD\n")
    added = _run(rig, "check", "doeff-linter", "pkg/a.py")
    assert added.returncode == 1
    assert "DOEFF999 pkg/a.py: 基点 1 → 今 2" in added.stderr
    # 前からの所見を直したのに基点が古い → 赤。lower の後は通る。
    (rig.repo / "pkg" / "a.py").write_text("x = 1\ny = 2\n")
    fixed = _run(rig, "check", "doeff-linter", "pkg/a.py")
    assert fixed.returncode == 1
    assert "下がっていない" in fixed.stderr
    assert _run(rig, "lower", "doeff-linter").returncode == 0
    assert _run(rig, "check", "doeff-linter", "pkg/a.py").returncode == 0


def test_the_linter_on_the_path_is_never_called(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    assert _run(rig, "check", "doeff-linter", "pkg/a.py").returncode == 0
    assert not (tmp_path / "bin" / "called").exists()
    assert (rig.dev / "calls").exists()


def test_a_tests_only_change_keeps_the_key_and_an_indexer_change_moves_it(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    first = LINTER_LOCKED.input_key(rig.repo, "HEAD")
    assert first is not None
    (rig.repo / "packages" / "doeff-linter" / "tests" / "t.rs").write_text("// 検を足す\n")
    assert LINTER_LOCKED.input_key(rig.repo, _commit(rig, "linter の検だけ")) == first
    (rig.repo / "packages" / "doeff-indexer" / "src" / "lib.rs").write_text("// indexer を変える\n")
    assert LINTER_LOCKED.input_key(rig.repo, _commit(rig, "indexer の src")) != first


def test_a_baseline_whose_linter_is_not_built_is_named_as_unmeasured_and_passes(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    built_for = _baseline_version(rig)
    _install_dev(rig, _change_linter_source(rig, "fn main() { /* 規則を変えた */ }\n"))
    (rig.repo / "pkg" / "a.py").write_text("x = 1  # BAD\ny = 2  # BAD\n")
    other = _run(rig, "check", "doeff-linter", "pkg/a.py")
    assert other.returncode == 0
    assert "測れない" in other.stderr
    assert built_for in other.stderr
    # 開発版は基点と別の鍵で在る — main を取り込めば測れる、と添える。
    assert "main を取り込めば" in other.stderr


def test_the_snapshot_store_answers_when_the_dev_build_is_newer(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=rig.repo, env=rig.environment,
                           capture_output=True, text=True, check=True).stdout.strip()
    assert _run(rig, "init", "doeff-linter").returncode == 0
    _install_dev(rig, _change_linter_source(rig, "fn main() { /* 規則を変えた */ }\n"))
    _place_linter(rig.snapshots / first, first)
    (rig.repo / "pkg" / "a.py").write_text("x = 1  # BAD\ny = 2  # BAD\n")
    added = _run(rig, "check", "doeff-linter", "pkg/a.py")
    assert added.returncode == 1
    assert "DOEFF999 pkg/a.py: 基点 1 → 今 2" in added.stderr
    assert (rig.snapshots / first / "calls").exists()


def test_lower_writes_the_key_of_the_given_build(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    before = _baseline_version(rig)
    changed = _change_linter_source(rig, "fn main() { /* 規則を変えた */ }\n")
    built = _place_linter(tmp_path / "built", changed)
    assert _run(rig, "--linter", str(built), "lower", "doeff-linter").returncode == 0
    assert _baseline_version(rig) == LINTER_LOCKED.input_key(rig.repo, changed)
    assert _baseline_version(rig) != before


def test_writing_without_a_built_linter_for_head_is_named_and_stops(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _change_linter_source(rig, "fn main() { /* 規則を変えた */ }\n")
    missing = _run(rig, "init", "doeff-linter")
    assert missing.returncode != 0
    assert "--linter" in missing.stderr
    assert not (rig.repo / "scripts" / "hook_finding_baseline" / "doeff-linter.json").exists()


def test_init_never_overwrites_a_baseline(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    again = _run(rig, "init", "doeff-linter")
    assert again.returncode == 2
    assert "書き直さない" in again.stderr


# --- semgrep は uv.lock の版を呼ぶ(#2906)— 偽の uv が `tool run --python <版> --from semgrep==<版> semgrep` を答え、探し道の semgrep は別の版 ---

LOCK_TEMPLATE = 'version = 1\n\n[[package]]\nname = "semgrep"\nversion = "{version}"\n\n[[package]]\nname = "other"\nversion = "0.1.0"\n'

FAKE_UV = """\
import json, sys
from pathlib import Path
here = Path(__file__).parent
args = sys.argv[1:]
with open(here / "uv-calls.jsonl", "a") as out:
    out.write(json.dumps(args) + "\\n")
if args[:3] != ["tool", "run", "--python"] or args[4] != "--from" or args[6] != "semgrep":
    raise SystemExit(f"偽の uv が知らない呼び: {args}")
version, rest = args[5].split("==")[1], args[7:]
if "--version" in rest:
    print(version)
    raise SystemExit(0)
paths = [a for a in rest if a.endswith(".py")]
results = [{"check_id": "R1", "path": p} for p in paths for line in Path(p).read_text().splitlines() if "BAD" in line]
print(json.dumps({"results": results, "errors": []}))
"""

FAKE_PATH_SEMGREP = """\
import sys
if "--version" in sys.argv:
    print("9.9.9")
    raise SystemExit(0)
raise SystemExit("探し道の semgrep が呼ばれた")
"""


def _semgrep_rig(tmp_path: Path) -> Rig:
    """一時の repo に uv.lock(semgrep 1.2.3)を置き、偽の uv と、別の版を名乗る探し道の semgrep を置くため。"""
    rig = _rig(tmp_path)
    (rig.repo / "uv.lock").write_text(LOCK_TEMPLATE.format(version="1.2.3"))
    for name, body in (("uv", FAKE_UV), ("semgrep", FAKE_PATH_SEMGREP)):
        tool = tmp_path / "bin" / name
        tool.write_text(f"#!{sys.executable}\n{body}")
        tool.chmod(0o755)
    return rig


def test_the_locked_command_names_the_lock_version(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text(LOCK_TEMPLATE.format(version="1.169.0"))
    # 道具の python は、script を走らせている python の版(hook の設定が `uv run --python` で宣言した版)— 木の .python-version
    # (3.14t)を uv tool run に選ばせない(card acp:kanban-issue:ki-a28a482132ab)。
    running: str = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert SEMGREP_LOCKED.locked_command(tmp_path) == [
        "uv", "tool", "run", "--python", running, "--from", "semgrep==1.169.0", "semgrep",
    ]


def test_semgrep_is_called_at_the_lock_version_whatever_the_path_has(tmp_path: Path) -> None:
    rig = _semgrep_rig(tmp_path)
    assert _run(rig, "init", "semgrep").returncode == 0
    baseline = json.loads((rig.repo / "scripts" / "hook_finding_baseline" / "semgrep.json").read_text())
    assert baseline == {"version": "1.2.3", "counts": {"R1": {"pkg/a.py": 1}}}
    (rig.repo / "pkg" / "b.py").write_text("z = 3  # BAD\n")
    added = _run(rig, "check", "semgrep", "pkg/b.py")
    assert added.returncode == 1
    assert "R1 pkg/b.py: 基点 0 → 今 1" in added.stderr
    calls = [json.loads(line) for line in (tmp_path / "bin" / "uv-calls.jsonl").read_text().splitlines()]
    assert calls
    assert all(call[:3] == ["tool", "run", "--python"] and call[4:7] == ["--from", "semgrep==1.2.3", "semgrep"] for call in calls)


def test_a_lock_version_other_than_the_baseline_is_named_and_red(tmp_path: Path) -> None:
    rig = _semgrep_rig(tmp_path)
    assert _run(rig, "init", "semgrep").returncode == 0
    (rig.repo / "uv.lock").write_text(LOCK_TEMPLATE.format(version="1.2.4"))
    other = _run(rig, "check", "semgrep", "pkg/a.py")
    assert other.returncode == 1
    assert "1.2.3" in other.stderr
    assert "1.2.4" in other.stderr
    assert "lower semgrep" in other.stderr


def test_a_lock_without_semgrep_stops_without_falling_back_to_the_path(tmp_path: Path) -> None:
    rig = _semgrep_rig(tmp_path)
    assert _run(rig, "init", "semgrep").returncode == 0
    calls = tmp_path / "bin" / "uv-calls.jsonl"
    before = calls.read_text()
    (rig.repo / "uv.lock").write_text('version = 1\n\n[[package]]\nname = "other"\nversion = "0.1.0"\n')
    missing = _run(rig, "check", "semgrep", "pkg/a.py")
    assert missing.returncode != 0
    assert "1 つに決まらない" in missing.stderr
    assert calls.read_text() == before


# --- 日次の段(#2906 の 4 便目)— git の無い木(remote_check の写し)で、組んだ時に書いた鍵で照らし、「測れない」を赤にする ---------


def _copy_without_git(rig: Rig, tmp_path: Path) -> Path:
    """一時の repo を .git 抜きで写すため(日次の段の木 = git ls-files の名簿の file だけを写した物)。"""
    copy = tmp_path / "copy"
    shutil.copytree(rig.repo, copy, ignore=shutil.ignore_patterns(".git"))
    return copy


def _record_key(rig: Rig, key: str) -> None:
    """偽の land-arm の開発版の記録に、組んだ時の鍵を書き、記録の commit は git の知らない物にするため。"""
    record = rig.dev / "installed.json"
    record.write_text(json.dumps({"commit": "0" * 40, "input_key": key}))


def test_a_tree_without_git_walks_its_files_and_skips_made_dirs(tmp_path: Path) -> None:
    for relative in ("a.py", "sub/b.pyi", "sub/c.txt", ".venv/x.py", "__pycache__/y.py", "node_modules/z.py"):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text("x = 1\n")
    assert TOOL.population(tmp_path, "doeff-linter") == ["a.py", "sub/b.pyi"]


def test_strict_turns_unmeasured_red(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    _install_dev(rig, _change_linter_source(rig, "fn main() { /* 規則を変えた */ }\n"))
    strict = _run(rig, "--strict", "check", "doeff-linter")
    assert strict.returncode == TOOL.UNMEASURED_RC
    assert "測れない" in strict.stderr
    # hook(--strict なし)は同じ木で通す
    assert _run(rig, "check", "doeff-linter").returncode == 0


def test_a_recorded_key_measures_a_tree_without_git(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    _record_key(rig, _baseline_version(rig))
    copy = _copy_without_git(rig, tmp_path)
    run = [sys.executable, str(SCRIPT), "--root", str(copy), "--strict", "check", "doeff-linter"]
    same = subprocess.run(run, env=rig.environment, capture_output=True, text=True, check=False)
    assert same.returncode == 0, same.stderr
    assert "dir を歩いて" in same.stderr
    (copy / "pkg" / "a.py").write_text("x = 1  # BAD\ny = 2  # BAD\n")
    grown = subprocess.run(run, env=rig.environment, capture_output=True, text=True, check=False)
    assert grown.returncode == 1
    assert "DOEFF999 pkg/a.py: 基点 1 → 今 2" in grown.stderr


def test_a_snapshot_with_a_recorded_key_is_found_without_git(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    assert _run(rig, "init", "doeff-linter").returncode == 0
    snapshot = rig.snapshots / ("f" * 40)
    _place_linter(snapshot, "f" * 40)
    (snapshot / "input_key").write_text(_baseline_version(rig) + "\n")
    _install_dev(rig, _change_linter_source(rig, "fn main() { /* 規則を変えた */ }\n"))
    copy = _copy_without_git(rig, tmp_path)
    found = subprocess.run([sys.executable, str(SCRIPT), "--root", str(copy), "--strict", "check", "doeff-linter"],
                           env=rig.environment, capture_output=True, text=True, check=False)
    assert found.returncode == 0, found.stderr
    assert (snapshot / "calls").exists()


def test_the_key_entry_prints_the_key_of_a_commit(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    printed = subprocess.run([sys.executable, str(ROOT / "scripts" / "doeff_linter_locked.py"), "key", "HEAD"],
                             cwd=rig.repo, env=rig.environment, capture_output=True, text=True, check=False)
    assert printed.returncode == 0, printed.stderr
    assert printed.stdout.strip() == LINTER_LOCKED.input_key(rig.repo, "HEAD")


def test_the_daily_gate_runs_the_baseline_check_strictly() -> None:
    # 日次の全体検証の段の列に、hook と同じ基点の比べを repo 全体へ --strict で当てる段が在る(消すと、hook を「測れない」で
    # 通った commit を測る所が無くなる)。2026-10-10 から段は日次の task の git の作業木で直に走り(ADR-DOE-ENFORCE-001 R10)、
    # 名簿は git の追跡する file — build の生成物(.venv など)は混ざらない。基点の鍵の linter の用意は tests/test_gate_tools.py。
    stages = tomllib.loads((ROOT / ".agents" / "land-queue.toml").read_text(encoding="utf-8"))["gate"]["full"]
    lint = [stage for stage in stages if stage["name"] == "lint"]
    assert len(lint) == 1
    assert "hook_finding_baseline.py --strict check doeff-linter" in lint[0]["run"]
    assert "hook_finding_baseline.py --strict check semgrep" in lint[0]["run"]


def test_the_script_reads_its_sibling_modules_under_a_safe_path() -> None:
    # PYTHONSAFEPATH=1(作業役の shell に在る)の下では script の dir が sys.path に入らない。それでも隣の module
    # (doeff_linter_locked・semgrep_locked)を読み、入口まで来る — 引数なしは使い方を出して 2(import で落ちると 1)。
    ran = subprocess.run([sys.executable, str(SCRIPT)], env={**os.environ, "PYTHONSAFEPATH": "1"},
                         capture_output=True, text=True, check=False)
    assert "ModuleNotFoundError" not in ran.stderr, ran.stderr
    assert ran.returncode == 2, ran.stderr
