## 結論

検査を通るのに責務を侵す実装を 1 つ見つけました。中身は、読み手(program_effects.py)が Hy の展開の木を書き換えてから、展開の置き場に書き込む変更です。展開の置き場の鍵には読み手の digest が入っていません。そのため、読み手が変わっても、前の版の読み手が形を決めた木がそのまま読まれます。

検査は 1 つも走らせていません。「通る」はすべて読んだうえでの推測です。共有の木は変えていません。使った git の操作は読むだけの `git diff --stat` だけで、これで対象の 3 file が c1e7731d と同じ中身であることを確かめました。

## 1. 機能の要求と差分

**機能の要求(想定)**: 解析器が展開した木には Hy の gensym の名(`_hy_gensym_arg_517` など)が入り、その番号は同じ process で先に何を展開したかで変わります。import 側の code は、compile の口で gensym を module の中の順に振り直しています(#3667)。型検査の展開も同じ規則で振り直しています(#3869 の `static_check.with_canonical_gensyms`)。そこで解析器の木も同じ規則で振り直し、`_closeness` が `co_varnames` と比べる引数の名と、置き場の項の中身を、process の順に依らない形にする、という要求です。

- 【読んで確かめた】
  - import 側は loader_hooks.py:245 で `records.canonical_gensyms(code)` をかけている。
  - `_expand_hy` には振り直しが無い。
  - `_closeness`(program_effects.py の 2796 行あたり)は木の引数の名を `co_varnames` と比べる。
  - `_validation-predicate`(macros.hy:2497)は gensym の名の引数を持つ `fn` を作る。

**実装者が program_effects.py に置きそうな理由**:
- 振り直しを要るのは読み手の側(`_closeness`)です。
- hy_expansion.py を変えると `_expander_digest` が変わり、全部の module を展開し直します(ki-81f1c868203f が消した 257 秒)。S1 は「読み手の変更は program_effects.py だけ」と言っています。
- `_store_expansion` を呼ぶ `_expanded` が program_effects.py にあり、import 側と同じく「振り直してから保存」という順になります。
- 文書の手順どおり、`_TREE_KEY_FORMAT` も上げます。

**差分 D1(program_effects.py だけを変える)**:

```diff
-_TREE_KEY_FORMAT = "v9"
+# v10: the tree's gensyms are renumbered as the import side's code has them (_canonical_gensyms).
+_TREE_KEY_FORMAT = "v10"
@@ def _expanded(source: str, filename: str, module_name: str, places: "_CachePlaces | None") -> _Expanded:
-    """The tree the expansion's store holds for these inputs; else the expansion, stored there."""
+    """The tree the expansion's store holds for these inputs; else the expansion with its gensyms
+    renumbered (``_canonical_gensyms``), stored there — the same bytes whichever process wrote it."""
     if places is None:
-        return _expand_hy(source, filename, module_name)
+        return _canonical_gensyms(_expand_hy(source, filename, module_name))
     stored = _stored_expansion(places.expansion)
     if stored is not None:
         return stored
-    expanded = _expand_hy(source, filename, module_name)
+    expanded = _canonical_gensyms(_expand_hy(source, filename, module_name))
     _store_expansion(places.expansion, expanded)
     return expanded
+
+
+def _canonical_gensyms(expanded: _Expanded) -> _Expanded:
+    """Hy's gensym names renumbered in module order, by the rule the import side applies to the code
+    (doeff_hy_bytecode_guard.gensym_renaming — agora-redesign #3667): the parameter names _closeness
+    compares with co_varnames no longer depend on what this process expanded first."""
+    from doeff_hy_bytecode_guard import gensym_renaming
+
+    nodes = tuple(ast.walk(expanded.tree))
+    rename = gensym_renaming(
+        text for node in nodes for _, value in ast.iter_fields(node) for text in _field_texts(value)
+    )
+    for node in nodes:
+        for name, value in ast.iter_fields(node):
+            if isinstance(value, str):
+                setattr(node, name, rename(value))
+            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
+                setattr(node, name, [rename(item) for item in value])
+    return expanded
+
+
+def _field_texts(value: object) -> tuple[str, ...]:
+    """The strings of one AST field (an identifier, a string constant, global / nonlocal names)."""
+    if isinstance(value, str):
+        return (value,)
+    if isinstance(value, list):
+        return tuple(item for item in value if isinstance(item, str))
+    return ()
```

関数の中の import は、既にある `_read_cached_tree` と同じ形です。検査を避けるためのものではありません。`gensym_renaming` は guard の `__init__` が公開している口です(42 行・読んで確かめた)。

## 2. 本来の持ち主と、違反する契約

- **木の中身を決める知識の持ち主**: X(hy_expansion.py)です。D1 では「置き場に入る木の名を振り直す」という判断が R(読み手の派生の置き場)へ移り、A(読み手)の都合(`_closeness` の照合)が X の項の中身を決めます。X の書き込みの effect(`_store_expansion`)を通して、R の判断が X の鍵の下に残ります。`_store_expansion(place, expanded)` はどんな `_Expanded` でも受け取るので、出どころを守れません。
- **違反する契約**(いずれも読んで確かめた):
  - hy_expansion.py の頭: 「この file の digest が鍵にあるので、別の版の展開が作った木は読まれない」。
  - `_ExpansionKey` の docstring: 置き場は列挙した欄だけの関数である。D1 の後は、木が program_effects.py の code にも依ります。
  - S1 の前提「展開の木の中身を決める code は全部 X に在り、R・A は木を読むだけ」と、S2 の「前の版が作った木を読まない」。
  - 要件 3(#2973 の守りは派生の値の側に残す): R の作った物が、読み手の digest も `_TREE_KEY_FORMAT` も無い鍵(hy_expansion.py の 96〜104 行の `text()`)の下に置かれます。文書にある手での逃げ道である「形の印を上げる」も届きません。
  - hy_expansion.py 74〜75 行の「doeff の版の違う木の項は横に並んで置かれ、上書きし合わない」: 版の違う読み手が、同じ place の同じ項の名(`_entry_name(macros)` は D1 の前後で同じ)を取り合います。
- **起きる事(推測)**:
  - 日次の機体の `/work/state` の置き場には D1 の前の木が残っています。そのため D1 を入れた後も、source が変わらない module には D1 の振り直しが当たりません。
  - 後で D1 を revert した読み手は、D1 が形を決めた木を読み続けます。項は誰も消しません(hy_expansion.py と program_effects.py に削除の code は無い・grep で確かめた)。

## 3. 提示された各検査が D1 を拒否しない理由

- **契約の検の 20 本(推測・未実行)**:
  - どの検も新しい `tmp_path` の置き場で動き、別の読み手の code が形を決めた展開の項を置いてから木を比べる検がありません。
  - `changed_file`(573〜588 行)は写しに注釈を 1 行足し、`__file__` を向け替えるだけです。動く code は変わらないので、602 行の検は「展開 0 回・答えが同じ」しか見られず、D1 はどちらも満たします。
  - 510 行の検は派生の place だけを見ます。643 行の検は hy_expansion.py を変える場合だけを見ます。
  - `pe._expand_hy` を直に呼ぶ検(299 行)は `_expanded` を通りません。
  - 観測の検は、観測の区間が変わらないので通ります。
  - 壊れた項の検は、同じ項の名へ書き直すので通ります。
- **test_result_cache・ADR-001(推測)**: grep の限りでは `_compile_hy`・`_expand_hy`・gensym を参照しません。読んだ部分は .py の module の `module_ast` です。全文は読んでいません。
- **test_budget(推測)**: 790 行の検の source には gensym が無く、D1 の仕事は観測の中に入るので、cold 1 回・cached 0 回は変わりません。
- **ruff(推測)**:
  - pyproject.toml の 142 行の select に PLC は無く、関数の中の import は当たりません。
  - B010 は属性の名が定数の時だけで、D1 は変数の名を渡しています。
- **pyright(読んで確かめた)**: pyrightconfig.json の include は `doeff`・`packages/doeff-adr/{src,tests}` だけです。Makefile も `pyright doeff/` です。analyzer は範囲の外です。
- **doeff-linter(推測)**:
  - 基点で program_effects.py に在るのは DOEFF002 の 1 件だけです。
  - error の規則のうち、DOEFF006 は `tuple[str, ...]` を除外します。DOEFF007 は引数の append などの method と添字だけを見ます(D1 の `setattr` はループの中の変数に対するもの)。DOEFF008 は代入文だけを見ます。DOEFF016 は相対の import です。どれにも当たりません。
  - DOEFF012 は warning、DOEFF014 は info なので、hook を止めません。
  - architecture.hy は env_places の層だけを宣言しています。
- **semgrep(推測)**: analyzer に向けた規則はありません。packages 全体に当たる Python の規則は、doeff の Program・handler・VM・`doeff\.\w+\s*=`・future annotations などが対象で、D1 にはどれもありません。
- **commit の hook の台帳の突合(読んで確かめた)**: 数えるのは defadr・規則の id・law だけで、D1 はどれも足しません。
- **code-quality の fast の段**: 木の中に定義を見つけられず、判断できません。

## 4. 検査の通過と違反を確かめる手順(依頼側の使い捨ての写しで行う)

1. 写しに D1 を当て、`uv run --no-sync pytest packages/doeff-effect-analyzer/tests/python/test_hy_tree_cache.py -q -p no:cacheprovider` を走らせます。期待は 20 passed です(推測)。
2. 同じ写しの `packages/doeff-effect-analyzer/tests/python/test_d1_violation.py` に下の 2 本を置き、同じ命令で走らせます。期待は 2 本とも pass で、それが違反を示します。

```python
import ast
from pathlib import Path
import pytest
pytest.importorskip("hy"); pytest.importorskip("doeff_hy")
from doeff_effect_analyzer import hy_expansion  # noqa: E402
from doeff_effect_analyzer import program_effects as pe  # noqa: E402

CHECKED = """
(require doeff-hy.macros [defk <-])
(defk checked [x]
  {:pre [(: x int) (check > x 0)] :post [(: % int)]}
  x)
"""

@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "trees"; monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", str(d)); return d

def counted(monkeypatch):
    seen, real = [], pe._expand_hy
    monkeypatch.setattr(pe, "_expand_hy", lambda s, f, m: seen.append(m) or real(s, f, m))
    return seen

def a_new_process():
    for memo in (pe._MODULE_CACHE, pe._DEFINITION_INDEX, pe._BODY_FACTS, pe._BODY_NODES): memo.clear()

def test_v1_d1_reads_the_tree_the_previous_reader_stored(cache_dir, monkeypatch):
    hy_expansion._expand_hy(CHECKED, "/src/c.hy", "c")          # 長い run と同じく gensym の数えを進める
    raw = hy_expansion._expand_hy(CHECKED, "/src/c.hy", "c")
    assert "_hy_gensym_" in ast.dump(raw.tree)                 # 前提
    before = ast.dump(raw.tree)
    # D1 の前の _expanded が置く物そのもの。D1 は hy_expansion.py に触れないので鍵は同じ
    hy_expansion._store_expansion(hy_expansion._expansion_key(CHECKED, "c").place(cache_dir), raw)
    seen = counted(monkeypatch)
    warm = ast.dump(pe._compile_hy(CHECKED, "/src/c.hy", "c").tree)
    assert seen == [] and warm == before                       # 展開 0 回・前の読み手の木
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", "off"); a_new_process()
    cold = ast.dump(pe._compile_hy(CHECKED, "/src/c.hy", "c").tree)
    assert warm != cold                                        # 同じ code・同じ source で木が置き場の履歴で変わる

def test_v2_a_reverted_reader_reads_the_tree_d1_renumbered(cache_dir, monkeypatch):
    hy_expansion._expand_hy(CHECKED, "/src/c.hy", "c")
    seen = counted(monkeypatch)
    with_d1 = ast.dump(pe._compile_hy(CHECKED, "/src/c.hy", "c").tree)
    monkeypatch.setattr(pe, "_reader_digest", lambda: "reader=D1 reverted")   # revert した読み手
    monkeypatch.setattr(pe, "_canonical_gensyms", lambda expanded: expanded)
    a_new_process()
    warm = ast.dump(pe._compile_hy(CHECKED, "/src/c.hy", "c").tree)
    assert seen == ["c"] and warm == with_d1                   # 展開せず、D1 が形を決めた木を読む
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", "off"); a_new_process()
    assert warm != ast.dump(pe._compile_hy(CHECKED, "/src/c.hy", "c").tree)
```

本物に近い別の手順もあります。同じ `DOEFF_EFFECT_ANALYZER_CACHE` を指して、c1e7731d の写しで先に同じ source を解析し、その後で D1 の写しで解析します。期待は、D1 の写しで展開が 0 回になり、木に振り直していない名が残ることです。

## 5. 確かめていない前提と、足りない情報

- 検査は 1 つも走らせていません。20 passed、ruff・linter・semgrep の通過は、すべて読んだうえでの推測です。
- `CHECKED` の書き方(`(check > x 0)` と、defk の `:pre` に型の検が要るか)と、その展開に gensym が出るかは、macros.hy の 2550〜2563 行を読んだうえでの推測です。そのため検の中に前提の assert を置きました。
- gensym の数えが process に 1 つであることは、records.py の注釈を読んで判断しました。
- 今の解析器が gensym の食い違いで実際に誤答するか、木の差が ProgramEffects の答えの差にまで届くかは、確かめていません。違反の判定には木の差を使っています。
- 答えの置き場(result_cache)の material に program_effects.py が入るかは確かめていません。入るなら、revert の後に古い木から作った答えを新しい鍵で置き直す、と推測します。
- semgrep の規則の当たり範囲は include の綴りを目で読んで判断したもので、実際の照合はしていません。doeff-linter は読んだ規則(002・006・007・008 と severity)の範囲で判断しました。
- 同じ穴は別の形でも起きると推測します(未検証)。例えば、R が `_expand_hy` に渡す前に source を書き換えて元の source で鍵を作る場合や、R が木の枝を刈ってから保存する場合です。

WAIT: work — 依頼した側の次の指示
