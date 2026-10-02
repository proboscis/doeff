"""doeff-hy-check の基点との比べ・strict・展開の cache(agora-redesign #2153)の失敗ケース。

- strict: `:pre [(: args dict)]` の defk は型引数の無い dict として赤(reportMissingTypeArgument)。
- `(| X None)` の取り違え: None になりうる値の属性を読むと赤(reportOptionalMemberAccess)。
- 基点: 基点に在る赤だけなら exit 0、基点に無い赤が 1 つでも出たら exit 1。行がずれただけでは新しい赤にしない。
- 展開から記帳を外す: 型検査に見せる Python に `setattr(…, '__doeff_…__', …)`・`hy.macros.require` が残らず、
  strict で doeff-hy 自身の import の赤(重複・stub 無し・private な補助の名)が出ない。
- 展開の cache: 2 度目は保存した展開を引き、診断は 1 度目と同じ。source を変えれば鍵が変わる。
"""

import contextlib
import io
import json
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

from doeff_hy.static_baseline import Baseline, Identity, read_baseline, split

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk <-])

(defk count-keys [args]
  {:pre [(: args dict)] :post [(: % int)]}
  (len args))

(defk maybe-name [key]
  {:pre [(: key str)] :post [(: % (| str None))]}
  (if key key None))

(defk shout [key]
  {:pre [(: key str)] :post [(: % str)]}
  (<- name (maybe-name key))
  (.upper name))
"""

NEW_MISTAKE = """
(defk total [key]
  {:pre [(: key str)] :post [(: % int)]}
  (count-keys key))
"""


@dataclass(frozen=True)
class Run:
    """doeff-hy-check を 1 回走らせた結果(終了コードと JSON の診断)。"""

    code: int
    diagnostics: tuple[dict[str, object], ...]

    def errors(self) -> list[tuple[str, int]]:
        return [(str(d["rule"]), int(str(d["line"]))) for d in self.diagnostics if d["severity"] == "error"]


def _check(root: Path, *extra: str) -> Run:
    from doeff_hy.static_check import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["--root", str(root), "--json", "--cache-dir", str(root / ".cache"), *extra, str(root / "probe.hy")])
    text = out.getvalue()
    return Run(code, tuple(json.loads(text)) if text.strip() else ())


@pytest.fixture
def probe(tmp_path: Path) -> Path:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    return tmp_path


@needs_pyright
def test_strict_reports_a_bare_dict_contract(probe: Path) -> None:
    # 基本の検めでは型引数の無い dict は赤にならず、strict では defk の行(3 行目 — 引数の注記の位置)が赤。
    assert ("reportMissingTypeArgument", 3) not in _check(probe).errors()
    assert ("reportMissingTypeArgument", 3) in _check(probe, "--strict").errors()


@needs_pyright
def test_a_none_union_read_as_its_member_is_red(probe: Path) -> None:
    # (| str None) を返す defk の値に .upper を当てる(14 行目)と赤。
    assert ("reportOptionalMemberAccess", 14) in _check(probe).errors()


@needs_pyright
def test_only_reds_missing_from_the_baseline_fail(probe: Path) -> None:
    baseline = probe / "baseline.json"
    assert _check(probe, "--write-baseline", str(baseline)).code == 0
    assert read_baseline(baseline).counts, "基点に既存の赤が載っていない"
    # 基点に在る赤だけ = exit 0(赤は known として出る)。
    known = _check(probe, "--baseline", str(baseline))
    assert known.code == 0
    assert all(d["known"] for d in known.diagnostics if d["severity"] == "error")
    # 上に行を足して既存の赤の行がずれても、新しい赤にはしない。
    (probe / "probe.hy").write_text(";; 註\n;; 註\n" + MODULE, encoding="utf-8")
    assert _check(probe, "--baseline", str(baseline)).code == 0
    # 基点に無い赤を 1 つ足すと exit 1、新しい赤だけが known でない。
    (probe / "probe.hy").write_text(MODULE + NEW_MISTAKE, encoding="utf-8")
    fresh = _check(probe, "--baseline", str(baseline))
    assert fresh.code == 1
    new = [d for d in fresh.diagnostics if d["severity"] == "error" and not d["known"]]
    assert {d["line"] for d in new} == {18}, new


@needs_pyright
def test_strict_shows_no_reds_from_the_doeff_hy_expansion(probe: Path) -> None:
    from doeff_hy.static_check import import_roots, project, pyright_settings

    projection = project(probe, import_roots(probe, pyright_settings(probe)), probe / "probe.hy")
    text = getattr(projection, "text")
    assert "__doeff_" not in text and "hy.macros.require" not in text, text
    rules = {d["rule"] for d in _check(probe, "--strict").diagnostics}
    assert not rules & {"reportDuplicateImport", "reportMissingTypeStubs", "reportPrivateUsage", "reportUnusedImport"}, rules


@needs_pyright
def test_the_expansion_cache_returns_the_same_diagnostics(probe: Path) -> None:
    first = _check(probe)
    entries = sorted((probe / ".cache").rglob("*.json"))
    assert entries, "展開が保存されていない"
    assert _check(probe).diagnostics == first.diagnostics
    assert sorted((probe / ".cache").rglob("*.json")) == entries  # 2 度目は保存し直さない(同じ鍵)
    (probe / "probe.hy").write_text(MODULE + "\n;; 変えた\n", encoding="utf-8")
    _check(probe)
    assert len(list((probe / ".cache").rglob("*.json"))) == len(entries) + 1  # source が変われば別の鍵


@dataclass(frozen=True)
class Seen:
    """split に渡す診断の最小の形。"""

    path: str
    line: int
    rule: str
    message: str
    severity: str = "error"


def test_split_counts_the_same_red_by_multiplicity() -> None:
    # 同じ識別子の赤が基点で 1 件・今 2 件なら、1 件が新しい(集合で比べると見逃す形)。
    base = Baseline(Counter({Identity("a.hy", "r", "m"): 1}))
    verdict = split(base, [Seen("a.hy", 3, "r", "m"), Seen("a.hy", 9, "r", "m")])
    assert [d.line for d in verdict.known] == [3] and [d.line for d in verdict.new] == [9]


def _baseline_of(tmp_path: Path, messages: list[str]) -> Baseline:
    path = tmp_path / "base.json"
    rows = [{"path": "a.hy", "rule": "r", "message": m, "line": i} for i, m in enumerate(messages, 1)]
    path.write_text(json.dumps({"version": 1, "errors": rows}), encoding="utf-8")
    return read_baseline(path)


def test_a_shifted_hy_generated_number_is_still_the_known_red(tmp_path: Path) -> None:
    # 定義を 1 つ足して Hy の生成名の番号が 44 → 45・28 → 29 にずれても、基点の同じ赤のまま(#2287)。
    # 基点の行は読む時に畳むので、基点の作り直しは要らない。
    base = _baseline_of(tmp_path, ["x is _hy_anon_44", "y of _hy_let_first_28", "z of _lazy_sent_cached_1"])
    verdict = split(base, [Seen("a.hy", 3, "r", "x is _hy_anon_45"), Seen("a.hy", 4, "r", "y of _hy_let_first_29"), Seen("a.hy", 5, "r", "z of _lazy_sent_cached_2")])
    assert len(verdict.known) == 3 and verdict.new == ()


def test_folding_does_not_hide_an_added_generated_red_or_other_numbers(tmp_path: Path) -> None:
    # 畳んだ後も個数で比べるので、生成名の赤が本当に 1 つ増えた分は新しい赤。型の名の数字は畳まない。
    base = _baseline_of(tmp_path, ["x is _hy_anon_44", "type Int32 at 7"])
    verdict = split(base, [
        Seen("a.hy", 1, "r", "x is _hy_anon_9"),
        Seen("a.hy", 2, "r", "x is _hy_anon_10"),
        Seen("a.hy", 3, "r", "type Int64 at 7"),
    ])
    assert [d.line for d in verdict.known] == [1]
    assert [d.message for d in verdict.new] == ["x is _hy_anon_10", "type Int64 at 7"]


def test_split_follows_a_moved_file_one_to_one() -> None:
    # 基点の赤が消え、同じ規則と文言の赤が別の path に 1 つ出た = 移動。2 つ目は新しい。
    base = Baseline(Counter({Identity("old.hy", "r", "m"): 1}))
    verdict = split(base, [Seen("new.hy", 1, "r", "m"), Seen("other.hy", 1, "r", "m")])
    assert [d.path for d in verdict.known] == ["new.hy"] and [d.path for d in verdict.new] == ["other.hy"]


def test_a_baseline_of_another_version_is_unreadable(tmp_path: Path) -> None:
    from doeff_hy.static_baseline import BaselineUnreadable

    path = tmp_path / "b.json"
    path.write_text(json.dumps({"version": 9, "errors": []}), encoding="utf-8")
    with pytest.raises(BaselineUnreadable):
        read_baseline(path)


def test_the_require_scan_is_kept_by_the_source_text_and_redone_when_it_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 失敗ケース(agora-redesign #2675): 展開の cache の鍵を作るための require の読み(Hy の reader で source を全部読む)を毎回し直し、
    # 依存 350 個の file の 1 回の測りの約 29 秒のほとんどがそれだった。読みの結果を source の中身の指紋ごとに保存して引く。
    # 同じ中身は読み直さない・中身が変われば読み直す・鍵は保存の有無で変わらない(展開の保存はそのまま当たる)。
    from doeff_hy import static_cache

    cache = tmp_path / ".cache"
    text = "(require probe_macros [m])\n(m 1)\n"
    assert static_cache._required_modules(text, cache) == ("probe_macros",)
    assert len(list((cache / "requires").rglob("*.txt"))) == 1
    assert not list(cache.rglob("*.json"))  # 展開の保存(*.json)の数えに混ざらない

    def unread(_text: str) -> tuple[str, ...]:
        raise AssertionError("同じ中身を読み直した")

    monkeypatch.setattr(static_cache, "_read_required_modules", unread)
    assert static_cache._required_modules(text, cache) == ("probe_macros",)
    monkeypatch.undo()
    changed = "(require probe_macros [m])\n(require other_macros [n])\n"
    assert static_cache._required_modules(changed, cache) == ("probe_macros", "other_macros")
    source = tmp_path / "probe.hy"
    source.write_text(text, encoding="utf-8")
    roots = (tmp_path,)
    assert static_cache.cache_key(roots, source, "probe", "probe.hy", cache) == static_cache.cache_key(
        roots, source, "probe", "probe.hy"
    )
