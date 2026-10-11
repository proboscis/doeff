"""doeff-hy-check の依存の展開を子の process で並べる形(agora-redesign #3869 の (b))の失敗ケース。

- 並べた結果(展開の文・位置の対応・所見・失敗の一覧)が直列と同じ — 展開の文の Hy の gensym の名まで同じ。
- 1 つの file の展開に失敗しても、同じ段の他の file の結果は揃う。
- 子の process が答えを返さずに落ちたら、答えの無い file を名指して道具の失敗にする(黙って抜かない)。
- 全部保存から引ける実行では pool を起こさない(保存が効く時の秒を増やさない)。
- `python -m doeff_hy.static_check` で撃った入口(この file は親で __main__・展開の子で __mp_main__ の名で読まれる)も、展開の子が
  書いた保存を次の実行の親が引く。
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from doeff_hy import static_check
from doeff_hy.static_check import (
    Closure,
    ExpansionWorkerLostError,
    project_closure,
)

#: 根の file が import する 3 つの module。どれも file の中の macro が gensym を使い、一番外の setv の所見を持つ。
LEAF = """\
(defmacro kept-twice [x]
  (setv g (hy.gensym "kept"))
  `(do (setv ~g ~x) (+ ~g ~g)))
(setv {name} (kept-twice {value}))
"""

ROOT = """\
(import alpha beta gamma)
(setv total (+ alpha.alpha beta.beta gamma.gamma))
"""


def _tree(root: Path, broken: str | None = None, crashing: str | None = None) -> Path:
    """根の file(main.hy)と、それが import する alpha・beta・gamma を置く。broken は展開に失敗する module、crashing は
    展開の途中で子の process を落とす module。"""
    for number, name in enumerate(("alpha", "beta", "gamma"), 1):
        text = LEAF.format(name=name, value=number)
        if name == broken:
            text = "(defmacro)\n"
        if name == crashing:
            text = "(defmacro gone [] (import os) (os._exit 3))\n(gone)\n"
        (root / f"{name}.hy").write_text(text, encoding="utf-8")
    main = root / "main.hy"
    main.write_text(ROOT, encoding="utf-8")
    return main


@dataclass(frozen=True)
class Shape:
    """比べる形: file ごとの展開の文・位置の対応・所見と、失敗した file の名(順に依らない)。"""

    projections: dict[str, tuple[object, ...]]
    failures: list[str]


def _shape(closure: Closure) -> Shape:
    return Shape(
        {
            path.name: (projection.text, projection.spans, projection.findings)
            for path, projection in closure.projections.items()
        },
        sorted(failure.source.name for failure in closure.failures),
    )


def test_expanding_in_child_processes_gives_the_serial_answer(tmp_path: Path) -> None:
    main = _tree(tmp_path)
    serial = project_closure(tmp_path, [tmp_path], [main], None, limit=1)
    parallel = project_closure(tmp_path, [tmp_path], [main], None, limit=3)
    assert len(serial.projections) == 4, serial
    assert _shape(parallel) == _shape(serial)


def test_a_failed_expansion_does_not_drop_the_others_of_its_wave(tmp_path: Path) -> None:
    main = _tree(tmp_path, broken="beta")
    closure = project_closure(tmp_path, [tmp_path], [main], None, limit=3)
    shape = _shape(closure)
    assert shape.failures == ["beta.hy"]
    assert sorted(shape.projections) == ["alpha.hy", "gamma.hy", "main.hy"]


def test_a_lost_child_names_the_files_without_an_answer(tmp_path: Path) -> None:
    main = _tree(tmp_path, crashing="gamma")
    with pytest.raises(ExpansionWorkerLostError, match=r"gamma\.hy"):
        project_closure(tmp_path, [tmp_path], [main], None, limit=3)
    assert issubclass(
        ExpansionWorkerLostError, RuntimeError
    )  # main は RuntimeError を道具の失敗(exit 2)にする


def test_a_run_that_reads_everything_from_the_store_starts_no_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main = _tree(tmp_path)
    cache = tmp_path / ".cache"
    first = project_closure(tmp_path, [tmp_path], [main], cache, limit=3)
    started: list[int] = []
    real = static_check._start_pool

    def counting(workers: int) -> object:
        started.append(workers)
        return real(workers)

    monkeypatch.setattr(static_check, "_start_pool", counting)
    second = project_closure(tmp_path, [tmp_path], [main], cache, limit=3)
    assert started == [], "全部引ける実行で pool を起こした"
    assert _shape(second) == _shape(first)


#: 日次の命令と同じく doeff_hy.static_check を __main__ の名で撃つ子の process(`python -m` と同じ — 展開の子は __mp_main__ の名で
#: 読む)。保存を引けた数(hit)と外れた数(miss)を最後の行に JSON で出す。pyright は診断の無い答えを返す偽物(引数で名指す)。
MAIN_CHILD = """\
import json, runpy, sys
import doeff_hy.static_cache as cache
seen = {"hit": 0, "miss": 0}
real = cache.load
def load(cache_dir, name):
    answer = real(cache_dir, name)
    seen["hit" if isinstance(answer, cache.CachedProjection) else "miss"] += 1
    return answer
cache.load = load
root, cache_dir, pyright = sys.argv[1:4]
sys.argv = ["doeff-hy-check", "--root", root, "--cache-dir", cache_dir, "--pyright", pyright, root]
try:
    runpy.run_module("doeff_hy.static_check", run_name="__main__", alter_sys=True)
except SystemExit:
    pass
print(json.dumps(seen))
"""


def test_the_entry_run_as_main_reads_the_store_its_expansion_children_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 直す前は赤(agora-redesign card ki-1475765a3f18): 記録は後処理 POST_PROCESSING を __name__ の module で名指していた。
    # `python -m` の入口では、展開の子が __mp_main__ の project を辿った digest を書き、次の実行の親は __main__ の project を
    # 辿って照らす(関数の持ち主の名と __name__ の値が違う)ので、同じ木・同じ置き場でも保存を 1 つも引けなかった
    # (agora-controllers の日次の hy-type-trend は同じ dir でも 593 本中 592 本を展開し直す形だった)。
    project = tmp_path / "proj"
    project.mkdir()
    _tree(project)
    pyright = tmp_path / "pyright"
    pyright.write_text('#!/bin/sh\necho \'{"generalDiagnostics": []}\'\n', encoding="utf-8")
    pyright.chmod(0o755)
    monkeypatch.setenv("DOEFF_HY_CHECK_EXPANDERS", "3")
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pycache"))

    def run() -> dict[str, int]:
        done = subprocess.run(
            [sys.executable, "-c", MAIN_CHILD, str(project), str(tmp_path / ".cache"), str(pyright)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip().splitlines()[-1])

    assert run() == {"hit": 0, "miss": 4}
    assert run() == {"hit": 4, "miss": 0}, "展開の子が書いた保存を、次の実行の親が引けなかった"


def test_the_expander_limit_defaults_to_four_and_is_set_by_the_environment() -> None:
    """展開の子の数の上限は既定 4(core の数ではない — 36 core の機体で検査 2 本が 30 本超の子を起こした・2026-10-11)。環境変数
    DOEFF_HY_CHECK_EXPANDERS で替えられ、1 以上の整数でない値は黙って既定へ落とさず断る。"""
    assert static_check.expander_limit(None) == 4
    assert static_check.expander_limit("") == 4
    assert static_check.expander_limit("2") == 2
    for wrong in ("0", "-1", "four", "1.5"):
        with pytest.raises(ValueError, match="DOEFF_HY_CHECK_EXPANDERS"):
            static_check.expander_limit(wrong)


def test_the_entry_takes_the_limit_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """入口の main は上限の素の値を env_places から受けて expander_limit に渡す(os.environ を直に読まない — doeff-linter の
    DOEFF004・日次 2026-10-11 16:00 の lint の段の赤・card ki-72c8b0827b29)。1 以上の整数でない値は走れなかった(exit 2)。"""
    monkeypatch.setattr(sys, "path", list(sys.path))  # main は根を sys.path の頭に足す
    monkeypatch.setenv("DOEFF_HY_CHECK_EXPANDERS", "0")
    assert static_check.main(["--root", str(tmp_path), "--no-cache"]) == 2
    assert "DOEFF_HY_CHECK_EXPANDERS" in capsys.readouterr().err
