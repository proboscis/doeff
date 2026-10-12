# 盲検の入力 — doeff の日次の母集団の検: package の loop の検の一巡の共有と sh の偽の runner

対象の版: doeff commit c854bccaff823f660484fb32303affc112a2102f(同じ版の file を git archive で書き出した読み取り専用の dir /tmp/doeff-blind-c854bccaf が在る。
file を変えない・検を走らせない・他の会話へ連絡しない。足りない情報は「不足」として返す)。

## 要件(固定)

- R1: tests/test_daily_test_population.py の 2 本(test_make_test_packages_visits_every_package_after_a_red・test_make_test_packages_lists_every_slow_test)は、
  本物の `make test-packages` を偽の runner で回す一巡を 1 回だけ回し、その記録を module の fixture で共に読む。
- R2: 偽の runner は sh。path は cd と `pwd -P` で解く(`realpath -m` は Mac に無い)。
- R3: root の束の 2 本の合計が load 65〜70 で 2 秒以下。
- R4: 赤を見逃さない(package を 1 つ飛ばす・秒の flag を落とす変異が赤)。
- R5: 一巡の共有の前に検が見ていた性質を 1 つも失わない。

利用状況: この file は root の pyproject の `[[tool.doeff.contract-tests]] prefix = ""` に載り、登記の前の `make test-changed`(合計 60 秒の枠)が
どの変更でも走らせる。日次の段 root(`uv run --no-sync pytest -q -m 'not e2e' --durations=0 --durations-min=10`)でも走る。
検の対象の Makefile の target test-packages は、日次の段 packages(`make test-packages PACKAGE_UV_RUN='uv run --no-sync'`)が使う。

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口 | effects | 寿命 |
|---|---|---|---|---|---|---|
| M-loop | Makefile の target `test-packages`(検の対象) | 母集団の選び(packages/*/tests に検の file・PACKAGE_EXTRA_TEST_ROOTS)・赤の後も続ける・最後に `test-packages failed:` で名指す・PACKAGE_PYTEST_DURATIONS | session の起こし方の中身(PACKAGE_UV_RUN に委ねる) | make の変数 PACKAGE_UV_RUN・PACKAGE_PYTEST_DURATIONS・PACKAGE_EXTRA_TEST_ROOTS・rc・要約の行 | process の起動(session ごと) | make の 1 回 |
| M-expect | 期待の導出(`_expected_packages`・`_package_extra_test_roots`・`_DAILY_DURATIONS_FLAGS`・`_stage_run`・`_named_failed_packages`) | 期待の package の集合を file system から(Makefile の実走からは取らない)・外の根は Makefile に聞く・秒の flag・要約の行の読み方 | なし | 関数 | file system を読む・make を 1 回(外の根を聞く) | 検ごと |
| M-runner | 偽の runner `_FAKE_RUNNER`(sh) | 受けた cwd と引数を 1 行(タブ区切り)で記録に足す・引数の 1 つが DOEFF_FAKE_RUNNER_FAIL の dir に物理の path で当たる時だけ rc 1 | path の解き方・記録の形 | env DOEFF_FAKE_RUNNER_RECORD・DOEFF_FAKE_RUNNER_FAIL・rc | 記録の file へ追記 | session の 1 回 |
| M-make | `_run_test_packages` と `_MakeRound` | 本物の Makefile を `-f` で名指し `-C <木>` で回す・runner と記録を tmp に置く・記録を (cwd, 引数) の列に読む・120 秒で打ち切る | 記録の形(検には `_MakeRound.calls` だけを渡す) | `_run_test_packages(repo, tmp, fail, *overrides) -> _MakeRound` | subprocess(make)を 1 回・tmp に file を書く | 呼びごと |
| M-round | fixture `packages_round` と `_PackagesRound` | この repo の一巡を module で 1 回・赤にする package(期待の集合の名前順で最初) | なし | `_PackagesRound(expected, first, made)` | M-make を 1 回 | module |
| M-checks | 検 3 本の判定 | visit: 訪ねた集合 = 期待・外の根を訪ねた・rc≠0・名指し = [first] / flags: 全 session に秒の flag / green: 全部緑の模型の木で全部を訪ね rc 0・名指し無し | なし | pytest の検 | 記録を読む(green は M-make を 1 回) | 検ごと |
| M-route | 検の実行経路 | contract-tests prefix ""・日次の段 root | なし | 設定 | なし | 設定 |

## 変更シナリオと主張

| id | 軸 | 変える要求 | 主張 | 予測する変更範囲 |
|---|---|---|---|---|
| S-hw | hardware | Mac で走らせる(BSD の userland・/bin/sh は bash の POSIX mode・tmp は /var → /private/var の symlink の下・`realpath -m` 無し) | 機体に依るのは M-runner だけで、POSIX の sh の builtin だけを使う。比べるのは物理の path なので、symlink の下の tmp や repo でも同じ dir を同じと数える | なし |
| S-st | storage | 記録の形を変える(引数にタブや改行が入る・記録を file でなく fd へ出す) | 記録の形を知るのは書き手 M-runner と読み手 M-make だけで、検は `_MakeRound.calls` しか見ない | M-runner・M-make |
| S-eff | effects | Makefile の loop が runner の起こし方を変える(package の dir へ cd して `tests` を渡す・引数を足す・母集団の loop を足す・要約の行の書き方を変える) | M-loop の変更に M-runner・M-make・M-round を合わせる必要は無く、退行(package を飛ばす・赤で止める・赤を飲む・外の根を飛ばす・秒の flag を落とす・緑で rc≠0)は M-checks のどれかが赤にする。cd してから相対で渡す形も runner が自分の cwd から解くので同じ package と数える | M-loop だけ |
| S-conc | concurrency | (a) root の pytest を xdist で並べ、3 本が別の worker に載る (b) Makefile の loop が package を並べて走らせ、runner の呼びが重なる | (a) fixture は worker ごとに 1 回で正しさは変わらず、費用は共有の検を受けた worker の数(≤ 2)まで (b) runner は 1 呼び 1 行を printf 1 回で O_APPEND の file に足すので行が混ざらず、検は順に依らない | (a) なし (b) M-loop だけ |
| S-sim | simulation | 新しい検が別の模擬の結末を要る(rc 5 の package・赤 2 つ・時間切れ) | 模擬の結末は M-runner が env から決める。新しい結末は M-runner(と env の名 1 つ)と、それを使う新しい検が `_run_test_packages` で自分の一巡を回す形で足し、共有の一巡と既存の検は変えない | M-runner・新しい検 |
| X-dist | distribution | 対象外 | 検は 1 機体の 1 つの pytest の process の中で自分の checkout を読み、機体をまたぐ状態を持たない | — |

主張の前提: P1 M-loop は session を全部 `$(PACKAGE_UV_RUN)` 経由で起こす。P2 引数に改行とタブを含まない。P3 赤にする package は期待の集合の名前順で最初。

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 |
|---|---|---|---|
| 記録の形は M-runner と M-make だけが知る | frozen dataclass `_MakeRound` | `_MakeRound`・`_run_test_packages` | 慣習 |
| M-loop の退行が赤になる | 振る舞いの検 3 本 | 3 本の検 | make test-changed(contract-tests prefix "")・日次の段 root |
| 一巡は module で 1 回 | pytest の fixture の scope="module" | `packages_round` | 同上 |
| runner は Python を起こさない | sh の script | `_FAKE_RUNNER` | 同上 |

検の実行(設計者が使う形): `uv run --no-sync pytest -q tests/test_daily_test_population.py -k make_test_packages`。lint: `uv run --no-sync ruff check <file>`。

## 実コード(c854bccaf)

### tests/test_daily_test_population.py(抜き出し — 期待の導出)

```python
REPO_ROOT = Path(__file__).resolve().parents[1]
def _expected_packages(repo: Path) -> list[str]:
    """package の母集団の期待 — `packages/<p>/tests` の下に fixtures 以外の検の file が在る p の全部。

    Makefile の実走からは取らない: 期待を実装から取ると、Makefile が母集団を縮めた時に
    訪ねた集合と期待が一緒に縮んで緑のまま残る(盲検 B の反例)。
    """
    files = _test_named_files(repo)
    return sorted(
        {
            parts[1]
            for parts in (path.split("/") for path in files)
            if len(parts) > 3 and parts[0] == "packages" and parts[2] == "tests"
        }
    )


def _test_named_files(repo: Path) -> list[str]:
    """repo の検の file(`_test_file_patterns` に当たる・fixtures の下を除く)の repo の根からの相対 path。"""
    patterns = _test_file_patterns(repo)
    return sorted(
        rel
        for current, dirs, files in _walk(repo)
        for rel in (Path(current, name).relative_to(repo).as_posix() for name in files)
        if _is_test_file(rel, patterns) and "fixtures" not in rel.split("/")
    )


def _walk(repo: Path) -> Iterator[tuple[str, list[str], list[str]]]:
    """`_NOT_WALKED` の dir へ降りない os.walk。"""
    for current, dirs, files in os.walk(repo):
        dirs[:] = sorted(
            name
            for name in dirs
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in _NOT_WALKED)
        )
        yield current, dirs, files


def _is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _is_excluded(path: str, row: str) -> bool:
    return path == row or (row.endswith("/") and path.startswith(row))


def _root_ini(repo: Path) -> dict[str, Any]:
    config = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["pytest"]["ini_options"]


def _root_testpaths(repo: Path) -> list[str]:
    return list(_root_ini(repo)["testpaths"])


def _package_extra_test_roots(repo: Path, overrides: tuple[str, ...] = ()) -> list[str]:
    """`make test-packages` が package の tests/ の外で走らせる根 — 定義は Makefile の PACKAGE_EXTRA_TEST_ROOTS の 1 点。

    検の側に一覧を写さず、本物の Makefile に名を聞く(overrides は make の命令行の変数の上書き)。
    """
    proc = subprocess.run(
        [
            "make",
            "-s",
            "--no-print-directory",
            "-f",
            str(REPO_ROOT / "Makefile"),
            "-C",
            str(repo),
            "print-package-extra-test-roots",
            *overrides,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return proc.stdout.split()


def _population_roots(repo: Path, overrides: tuple[str, ...] = ()) -> list[str]:
    """日次の母集団の根 — root の testpaths・package ごとの tests・package の tests の外の根。"""
    return [
        *_root_testpaths(repo),
        *(f"packages/{package}/tests" for package in _expected_packages(repo)),
        *_package_extra_test_roots(repo, overrides),
    ]

def _named_failed_packages(output: str) -> list[str] | None:
    """`make test-packages` が最後に名指した失敗の package(`test-packages failed:` の行が無ければ None)。"""
    summary = [line for line in output.splitlines() if line.startswith("test-packages failed:")]
    return summary[-1].split(":", 1)[1].split() if summary else None

```

### tests/test_daily_test_population.py(抜き出し — runner・make・fixture・検)

```python
# 偽の runner(make の PACKAGE_UV_RUN に置く)— 受けた cwd と引数を記録の file へ 1 行(タブ区切り)で足し、引数の 1 つが
# DOEFF_FAKE_RUNNER_FAIL の dir に当たる時だけ 1 で終わる。引数は runner の cwd から解くので、package の dir から走らせる形でも
# 同じ package と数える。sh で書く: Python で書くと 1 巡で Python 3.14t を session の数(35)だけ起こし、load 65〜70 で
# 1 巡 4.75 秒かかった(card acp:kanban-issue:ki-e8f54a86b0a8)。path は cd と pwd -P で解く(realpath -m は Mac に無い)。
_FAKE_RUNNER = """\
unset CDPATH
fail=$(cd -- "$DOEFF_FAKE_RUNNER_FAIL" 2>/dev/null && pwd -P)
rc=0
line=$(pwd -P)
for arg in "$@"; do
    line="$line\t$arg"
    if [ -n "$fail" ] && [ -d "$arg" ] && [ "$(cd -- "$arg" && pwd -P)" = "$fail" ]; then rc=1; fi
done
printf '%s\\n' "$line" >> "$DOEFF_FAKE_RUNNER_RECORD"
exit $rc
"""


@dataclass(frozen=True)
class _MakeRound:
    """偽の runner で回した本物の Makefile の `make test-packages` の一巡。"""

    calls: list[tuple[str, list[str]]]  # runner が呼ばれた順の (cwd, 引数)
    output: str
    exit_code: int
    timed_out: bool


def _run_test_packages(repo: Path, tmp: Path, fail: Path, *overrides: str) -> _MakeRound:
    """本物の Makefile の test-packages を repo で偽の runner に回す(fail の dir を受けた session だけ赤)。

    overrides は make の命令行の変数の上書き。runner と記録は tmp に置く。
    """
    runner = tmp / "fake_runner.sh"
    runner.write_text(_FAKE_RUNNER, encoding="utf-8")
    record = tmp / "calls.tsv"
    # 子(make)は doeff の子 process の答え手が起こし、今の環境に偽の runner の 2 つの名を重ねて渡す(agora-redesign #3012)。
    proc = run(
        with_handlers(
            [subprocess_handler],
            RunProcess(
                argv=(
                    "make",
                    "-s",
                    "-f",
                    str(REPO_ROOT / "Makefile"),
                    "-C",
                    str(repo),
                    "test-packages",
                    f"PACKAGE_UV_RUN=sh {runner}",
                    *overrides,
                ),
                env=(
                    EnvEntry(name="DOEFF_FAKE_RUNNER_RECORD", value=str(record)),
                    EnvEntry(name="DOEFF_FAKE_RUNNER_FAIL", value=str(fail)),
                ),
                env_mode=EnvMode.EXTEND,
                timeout=120.0,
            ),
        )
    )
    lines = record.read_text(encoding="utf-8").splitlines() if record.exists() else []
    return _MakeRound(
        calls=[(cwd, argv) for cwd, *argv in (line.split("\t") for line in lines)],
        output=proc.stdout + proc.stderr,
        exit_code=proc.exit_code,
        timed_out=proc.timed_out,
    )


@dataclass(frozen=True)
class _PackagesRound:
    """この repo の一巡 — 期待の集合の名前順で最初の package(first)だけを赤にした。"""

    expected: list[str]
    first: str
    made: _MakeRound


@pytest.fixture(scope="module")
def packages_round(tmp_path_factory: pytest.TempPathFactory) -> _PackagesRound:
    """この repo の `make test-packages` の一巡を module で 1 回だけ回し、その記録を package の loop の検 2 本が共に読む。

    検ごとに回すと、同じ一巡(package の数だけ runner を起こす)を 2 回回す(card acp:kanban-issue:ki-e8f54a86b0a8)。
    この一巡には赤が在るので、「全部が緑なら rc 0」は模型の木の検
    test_make_test_packages_is_green_when_every_package_is_green が見る。
    """
    expected = _expected_packages(REPO_ROOT)
    assert expected, "packages/*/tests に Python のテストを持つ package が 1 つも見つからない"
    first = expected[0]
    made = _run_test_packages(
        REPO_ROOT,
        tmp_path_factory.mktemp("packages-round"),
        REPO_ROOT / "packages" / first / "tests",
    )
    return _PackagesRound(expected=expected, first=first, made=made)


def test_make_test_packages_is_green_when_every_package_is_green(tmp_path: Path) -> None:
    """模型の木(package a・b と package の tests/ の外の根 1 つ)で、どの session も緑なら rc 0 で失敗の package を名指さない。

    この repo の一巡は最初の package を赤にするので、緑の終わり方(要約の行の if を `[ … ] && …` に書き換えると、緑の時に
    recipe の最後の命令が 1 を返す)はこの検だけが見る — 一巡を 2 本で共有する前は、秒の flag の検が全部緑の一巡で rc 0 を見ていた。
    """
    for root in ("packages/a/tests", "packages/b/tests", "extra/sim"):
        (tmp_path / root).mkdir(parents=True)
        (tmp_path / root / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    made = _run_test_packages(
        tmp_path, tmp_path, tmp_path / "no-such-tests", "PACKAGE_EXTRA_TEST_ROOTS=extra/sim"
    )
    output = made.output
    assert not made.timed_out, f"make test-packages が 120 秒で終わらなかった\n{output[-2000:]}"
    visited = sorted(
        arg for _cwd, argv in made.calls for arg in argv if arg.endswith(("tests", "sim"))
    )
    assert visited == ["extra/sim", "packages/a/tests", "packages/b/tests"], (
        f"模型の木の session を全部訪ねていない: {visited}\n{output[-2000:]}"
    )
    assert made.exit_code == 0, (
        f"どの session も緑なのに make test-packages の rc が {made.exit_code}"
        f" — 日次の段 packages が緑の日に赤になる — R8\n{output[-2000:]}"
    )
    assert _named_failed_packages(output) is None, (
        f"どの session も緑なのに失敗の package を名指した: {_named_failed_packages(output)} — R8\n{output[-2000:]}"
    )


def test_make_test_packages_visits_every_package_after_a_red(
    packages_round: _PackagesRound,
) -> None:
    """期待の集合の名前順で最初の package だけを赤にしても、loop は全部を訪ねて最後に名指す。"""
    first, expected = packages_round.first, packages_round.expected
    calls, output = packages_round.made.calls, packages_round.made.output
    assert not packages_round.made.timed_out, (
        f"make test-packages が 120 秒で終わらなかった\n{output[-2000:]}"
    )

    packages_dir = (REPO_ROOT / "packages").resolve()
    reached: set[str] = set()
    for cwd, argv in calls:
        for arg in argv:
            resolved = Path(cwd, arg).resolve()
            if resolved.parent.parent == packages_dir and resolved.name == "tests":
                reached.add(resolved.parent.name)

    assert reached == set(expected), (
        f"make test-packages が訪ねた package が期待と違う: 欠け {sorted(set(expected) - reached)}"
        f" / 余り {sorted(reached - set(expected))}(runner が呼ばれた回数 {len(calls)}・"
        f"最初の {first} だけを赤にした)— 欠けた package は日次で 1 本も走らない(赤の後で"
        f"止めた・または母集団から外した)— ADR-DOE-ENFORCE-001 R8\n{output[-2000:]}"
    )
    extra_roots = _package_extra_test_roots(REPO_ROOT)
    passed = {Path(cwd, arg).resolve() for cwd, argv in calls for arg in argv}
    missed_roots = [root for root in extra_roots if (REPO_ROOT / root).resolve() not in passed]
    assert not missed_roots, (
        f"make test-packages が package の tests/ の外の根 {missed_roots} を訪ねていない(最初の {first}"
        f" だけを赤にした)— その根の検は日次で 1 本も走らない — R8\n{output[-2000:]}"
    )
    assert packages_round.made.exit_code != 0, (
        f"最初の package {first} を赤にしたのに make test-packages の rc が 0 — R8\n{output[-2000:]}"
    )
    named = _named_failed_packages(output)
    assert named == [first], (
        f"最後に失敗の package ({first}) だけを `test-packages failed:` の行で名指していない: "
        f"{named} — R8\n{output[-2000:]}"
    )


...

# 日次の pytest の session が終わりに出す秒の一覧の口 — 10 秒以上の検を本数の上限なしで出す(card acp:kanban-issue:ki-b8a436264866)。
# 上限を付けると(旧 --durations=20)、package に 30 秒を越える検が 20 本を越えた時に残りが log に出ない。
_DAILY_DURATIONS_FLAGS = ("--durations=0", "--durations-min=10")


def _stage_run(name: str) -> str:
    full = tomllib.loads((REPO_ROOT / ".agents" / "land-queue.toml").read_text(encoding="utf-8"))["gate"]["full"]
    return next(stage["run"] for stage in full if stage.get("name") == name)


def test_root_stage_lists_every_slow_test() -> None:
    """処理ステージ root の pytest の命令は、10 秒以上の検を上限なしで出す(日次の log に 1 本ごとの秒を残す)。"""
    segments = [shlex.split(part) for part in _stage_run("root").split("&&")]
    pytest_args = [words[words.index("pytest") + 1 :] for words in segments if "pytest" in words]
    assert len(pytest_args) == 1, f"処理ステージ root に pytest の命令がちょうど 1 本ない: {_stage_run('root')}"
    missing = [flag for flag in _DAILY_DURATIONS_FLAGS if flag not in pytest_args[0]]
    assert not missing, f"処理ステージ root の pytest に {missing} が無い — 30 秒を越える検が日次の log に出ない: {pytest_args[0]}"


def test_make_test_packages_lists_every_slow_test(packages_round: _PackagesRound) -> None:
    """処理ステージ packages の形(`make test-packages` に PACKAGE_UV_RUN だけを渡す)で、package ごとの session が全部
    10 秒以上の検を上限なしで出す。偽の runner が受けた引数を見る(一巡は上の検と共に読む — 赤は最初の package だけで、
    loop が全部を訪ねる事は上の検が見る)。"""
    assert "make test-packages PACKAGE_UV_RUN=" in _stage_run("packages"), _stage_run("packages")
    calls, output = packages_round.made.calls, packages_round.made.output
    assert not packages_round.made.timed_out, (
        f"make test-packages が 120 秒で終わらなかった\n{output[-2000:]}"
    )
    assert calls, f"make test-packages が runner を 1 度も呼ばなかった\n{output[-2000:]}"
    short = [
        argv for _cwd, argv in calls if not all(flag in argv for flag in _DAILY_DURATIONS_FLAGS)
    ]
    assert not short, (
        f"package の session {len(short)} / {len(calls)} 本の pytest に {list(_DAILY_DURATIONS_FLAGS)} が揃っていない"
        f"(Makefile の PACKAGE_PYTEST_DURATIONS)— 30 秒を越える検が日次の log に出ない: {short[:3]}"
    )
```

### Makefile(target test-packages と変数)

```make
# Run tests in all subpackages that have tests/ directories
# - tests/ に Python の検が 1 本も無い package(doeff-indexer / doeff-linter — 検は Rust の cargo test、
#   tests/fixtures の test_*.py は検体)は pytest に渡さない。検は test_*.py か test_*.hy(Hy の deftest か上から順に
#   実行する script — root の ini の doeff_hy_test_files で doeff-adr の plugin が集める・agora-redesign #2591)。渡すと「収集 0 件」の rc 5 で loop が止まり、
#   後ろの package が 1 本も走らない(2026-09-24 実測)。
# - 実 API / 実 CLI を撃つ e2e は日次の門(.agents/land-queue.toml gate.full)と同じく除く(-m "not e2e")。
# - PACKAGE_UV_RUN: 日次の門は make sync の直後に `uv run --no-sync` で呼ぶ(素の uv run の暗黙の再 sync で
#   直前に組んだ VM を組み直さないように)。
# - package の dir へ cd せず、repo の根から `pytest packages/<p>/tests` を呼ぶ。pytest の要約の FAILED 行は
#   cwd からの相対なので、package の dir から走らせると `tests/test_cli.py::…` の形になり、同名の test file を
#   持つ package どうしで失敗名が衝突する(日次の道具は失敗名を repo の根から pytest へそのまま渡す)。
#   session は package ごとに独立のまま(1 session に畳むと同名の test file が衝突する)。
# - 1 package の赤で止めず全 package を走らせ、最後に失敗の package を名指して 0 以外で終わる(test-rust と
#   同じ形)。赤で止めると後ろの package が 1 本も走らないまま日次に見えない(ADR-DOE-ENFORCE-001 R8・
#   tests/test_daily_test_population.py・card acp:kanban-issue:ki-08ec2d7c901f)。
# - PACKAGE_PYTEST_DURATIONS: package ごとの session の終わりに、10 秒以上かかった検を長い順に本数の上限なしで出す(日次の log に
#   1 本ごとの秒を残す)。2026-10-09 20:00 の日次で doeff-cluster が 814 → 1,740 秒に伸び、処理ステージ packages が時間切れに
#   なったが、log にも台帳にも 1 本ごとの秒が無く、どの検が伸びたかを割れなかった(card acp:kanban-issue:ki-107a03190129)。
PACKAGE_UV_RUN ?= uv run
#   2026-10-11: 上限 20 本を外した(--durations=0)— package に 30 秒を越える検が 20 本を越えると残りが log に出ないため。
#   root の段の pytest の命令(.agents/land-queue.toml)も同じ 2 つを持つ(card acp:kanban-issue:ki-b8a436264866)。
PACKAGE_PYTEST_DURATIONS ?= --durations=0 --durations-min=10
# package の tests/ の外に在る検の根(package ごとの tests/ と同じく、根ごとに別の session で走らせる)。
# - packages/doeff-cluster/src/doeff_cluster/sim: 模擬の環境の下の deftest(各 service の入口の組み立てを模擬の handler の組で回す
#   検 — doeff-linter の DOEFF136 は検がこの dir の下に在ることを求める・fixture は package の根の conftest.py(source は pytest を
#   import しない — src の外で検の dir の祖先に当たる置き場・agora-redesign #2681)・agora-redesign #2542)。
PACKAGE_EXTRA_TEST_ROOTS = packages/doeff-cluster/src/doeff_cluster/sim
# 母集団の根の定義はこの変数の 1 点。置き場の検(tests/test_daily_test_population.py)と、登記の前の入口の session の境目
# (scripts/run_changed_tests.py の read_session_roots・agora-redesign #2682)は下の target で読む
# (検の側に 2 つ目の一覧を書かない — 書くと、根を足した時に片方だけが古くなる・agora-redesign #2577)。
print-package-extra-test-roots:
	@echo $(PACKAGE_EXTRA_TEST_ROOTS)
test-packages:
	@echo "Running tests in subpackages..."
	@failed=""; \
	for dir in packages/*/; do \
		if [ -d "$$dir/tests" ]; then \
			if [ -z "$$(find "$$dir/tests" \( -name 'test_*.py' -o -name 'test_*.hy' \) -not -path '*/fixtures/*' | head -1)" ]; then \
				echo ""; \
				echo "=== Skipping $$(basename $$dir) (no Python tests — Rust tests run under cargo) ==="; \
				continue; \
			fi; \
			echo ""; \
			echo "=== Testing $$(basename $$dir) ==="; \
			$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS) || failed="$$failed $$(basename $$dir)"; \
		fi; \
	done; \
	for root in $(PACKAGE_EXTRA_TEST_ROOTS); do \
		if [ ! -d "$$root" ]; then echo ""; echo "=== Skipping $$root (no such directory) ==="; continue; fi; \
		echo ""; \
		echo "=== Testing $$root ==="; \
		$(PACKAGE_UV_RUN) pytest "$$root" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS) || failed="$$failed $$root"; \
	done; \
	if [ -n "$$failed" ]; then echo ""; echo "test-packages failed:$$failed"; exit 1; fi
```

### .agents/land-queue.toml(日次の段 root と packages)

```toml
  { name = "root", run = "UV_CACHE_DIR=${UV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/uv-lanes/land-doeff} make sync && UV_CACHE_DIR=${UV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/uv-lanes/land-doeff} PYTHONUNBUFFERED=1 uv run --no-sync pytest -q -m 'not e2e' --durations=0 --durations-min=10", pytest = true },
  { name = "packages", run = "UV_CACHE_DIR=${UV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/uv-lanes/land-doeff} make sync && gate_path=$(sh scripts/gate_tools.sh path) && PATH=$gate_path$PATH UV_CACHE_DIR=${UV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/uv-lanes/land-doeff} PYTHONUNBUFFERED=1 make test-packages PACKAGE_UV_RUN='uv run --no-sync'", pytest = true },
```

### pyproject.toml(contract-tests)

```toml
[[tool.doeff.contract-tests]]
prefix = ""
tests = [
    "tests/test_daily_test_population.py",  # 7.5 秒 — 検の file の置き場と集め手(日次の母集団)
    "tests/test_contract_table_follows_packages.py",  # 1.3 秒 — この表と package の増減のずれ(agora-redesign #2658)
]
```
