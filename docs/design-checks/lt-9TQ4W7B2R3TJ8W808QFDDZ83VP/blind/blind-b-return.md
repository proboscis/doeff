## 結論

失敗例を 1 つ具体化できました。S1 が予想の範囲に入れている変更、「code の読みの置き場の entry を source の file ごとにする」を実装した形です。entry は file の中身の sha256 で引き、entry の中の行は関数の在処(qualname と最初の行)で引きます。

提示された検は、静的に追った限りどれもこの差分を拒みません。それでも、別の file の macro が変わって compile し直された補助関数について、前の process が覚えた古い code の読みを返します。その結果、macro の digest が古い code の形で作られ、使い手の記録を「今に合う」と判じます(B1 違反)。

実測は「読む木の file を読んだ」ことだけです。テスト・検査・Python は一度も走らせていません。以下で「通る」「赤になる」と書くものは、すべて推測です。

---

## 1. 機能要求と差分

**要求**: 新しい作業木(会話ごとの worktree・worker の版ごとの root)でも、最初の process から code の読みを置き場から引きたい、というものです。今の `.facts` は、鍵に在処の module の file の path を含みます。行の鍵の `_code_sha256` も marshal の中の co_filename と行番号を含みます。このため作業木を作るたびに当たらず、macro_use.py の註によれば関数 378 個で 0.05〜0.06 秒かかります。実装者は bound_source の前例(source の中身で引けば作業木と版をまたいで共有できる)に倣います。

**差分**(`/home/ubuntu/.worktrees/doeff-at-167d38ec/packages/doeff-hy/src/doeff_hy_bytecode_guard/macro_use.py` の code の読みの節だけ):

```diff
@@ def _remembered(...)
-    entry = _facts_entry(key)
-    walker = _Walker(sha256_of, CodeReadings(_stored_facts(entry) if entry is not None else {}))
+    walker = _Walker(sha256_of, CodeReadings({}))
     walk(walker)
     digest = walker.digest()
-    if entry is not None and walker.readings.missed:
-        _write_facts(entry, walker.readings.used)
+    walker.readings.write()  # 同じ中身の file の関数の歩みは、次の process(別の作業木でも)が読み直さない
@@
-FACTS_TAG = "doeff-hy/code-facts/1"
+FACTS_TAG = "doeff-hy/code-facts/2"  # entry を source の file ごと・file の中身で引く形
@@ class CodeReadings:
-    def __init__(self, stored: dict[str, CodeFacts]) -> None:
-        self._stored = stored
-        self.used: dict[str, CodeFacts] = {}
-        self.missed = False
+    def __init__(self, stored: dict[str, dict[str, CodeFacts]]) -> None:
+        self._stored = stored                                # entry → 置き場から読んだ行(在処 → 読み)
+        self.used: dict[str, dict[str, CodeFacts]] = {}      # entry → この歩みが使った行
+        self.missed: set[str] = set()                        # 読み直した行を持つ entry
-    def facts(self, code: CodeType) -> CodeFacts:
-        sha = _code_sha256(code)
-        ...
+    def facts(self, code: CodeType, entry: str | None) -> CodeFacts:
+        place = f"{code.co_qualname}\0{code.co_firstlineno}"
+        rows = self._rows(entry)
+        remembered = _FACTS.get(id(code))
+        if remembered is not None and remembered[0] is code:
+            facts = remembered[1]
+        elif place in rows:
+            facts = rows[place]
+            _FACTS[id(code)] = (code, facts)
+        else:
+            facts = _code_facts(code)
+            if entry is not None:
+                self.missed.add(entry)
+        if entry is not None:
+            self.used.setdefault(entry, {})[place] = facts
+        return facts
+
+    def _rows(self, entry: str | None) -> dict[str, CodeFacts]:
+        if entry is None:
+            return {}
+        if entry not in self._stored:
+            self._stored[entry] = _stored_facts(entry)
+        return self._stored[entry]
+
+    def write(self) -> None:
+        for entry in sorted(self.missed):
+            _write_facts(entry, {**self._stored.get(entry, {}), **self.used[entry]})
-(_CODE_SHAS と _code_sha256 を削除)
-def _facts_entry(key: "UsedMacroKey | ValueReference") -> str | None:
-    ...  parts = (FACTS_TAG, own, sys.version, place, _module_file(module) or "")
+def _facts_entry(home: ModuleType | None, sha256_of: Sha256Of) -> str | None:
+    """関数の module の source の file 1 つの entry。鍵 = 印・この module の code・Python の版・module 名・file の中身。
+    path は入れない — 同じ中身の file の関数は同じ code なので、作業木と版をまたいで当たる。"""
+    from doeff_hy_bytecode_guard import code_store
+    store = code_store.store_dir()
+    file = None if home is None else vars(home).get("__file__")
+    if store is None or home is None or not isinstance(file, str):
+        return None
+    own, content = _own_digest(), sha256_of(file)
+    if own is None or content is None:
+        return None
+    parts = (FACTS_TAG, own, sys.version, home.__name__, content)
+    return code_store.entry_path(store, code_store.content_key(parts, b""), code_store.FACTS_SUFFIX)
@@ class _Walker.function
-        facts = self.readings.facts(function.__code__)
+        facts = self.readings.facts(function.__code__, _facts_entry(home, self._sha256_of))
```

## 2. 本来の責務の持ち主と、違反する契約

- **「Hy の code が何で決まるか」の知識の持ち主は S と L です。**
  - S の `code_key` の欄は、source の中身・module 名・Hy の版・cache_tag・最適化の段です。
  - S の docstring には「macro の依存は鍵に入れず、当たった entry の記録で確かめる(compile の前には依存が分からない)」とあります。
  - L は code に記録を付け、引いた code を必ず照らします。つまり持ち主は「source の中身だけでは code が決まらない」ことを知っていて、記録で確かめています。
- **M の code の読みの置き場の契約**(B1 の許し・M の不変条件)は「鍵 = code の中身(marshal の sha256)」です。読みの入力そのもので引くので、M は code が何で決まるかを知る必要がありません。
- **差分で動く判断**: 「この読みは今の code に合うか」の判断が、「source の file の中身が同じなら code も同じ」という前提に置き換わります。これは S と L の知識の弱い写しです。Hy の版・`-O`・展開に使った macro が抜けていて、記録による確かめもありません。
- **結果として破れる約束**:
  - 読みが鍵の純な関数でなくなります。
  - digest の材料(関数の形 `shape`)が file の中身を鍵に process をまたいで覚えられます。B1 が禁じる「生きた物を file で信じる形」に当たります。
  - S1 の主張「正しさは中の code の sha256 で決まり、digest は変わらない」と、S4 の主張「置き場には中身で決まる読みだけ」が崩れます。
  - さらに `_FACTS[id(code)]` に古い読みが入り、process の中の覚えも汚れます。

## 3. 提示された各検査が拒まない理由(すべて推測・未実行)

| 検査 | 拒まない理由 |
|---|---|
| 前半の「冷えるべき時」「冷えなくてよい時」(#3938 の節を含む) | fixture が `DOEFF_HY_CODE_STORE=off` なので `_facts_entry` は None です。forget の後は別の code object なので毎回 `_code_facts` を通り、基準版と同じになります。 |
| `test_a_macro_changed_in_another_tree…` | tree-b の macros.hy は中身が違うので鍵が違います。読み直され、body の形が変わって古いと判じます。 |
| 別の module の値・環境変数・別の top の package の値の 3 本 | 変わるのは素の値と環境変数で、歩みが毎回生きた値で入れます。code は同じなので、読みが当たっても digest は変わります。 |
| `test_a_second_process_does_not_parse…`・`…does_not_import_hy_repr` | bound_source に触れていません。 |
| `test_a_steady_check_process_does_not_read_code_facts_again` | 1 つ目の process の 6 つの歩みが、macros.hy の中身の鍵の entry に行を足し合わせます。3 つ目の process は全部当たり、`_constant_text` は 0 回です。 |
| `test_one_import_stats_each_macro_file_once` | `_file_state` を呼ぶ所を増やしていません。中身の sha256 は `file_sha256` の os.stat で、数えられる口を通りません。2 つ目の process の記録は全部当たり、compiled は空です。 |
| `test_a_file_changed_between_two_imports…` | 変わるのは tables.py の文の読み(中身の鍵)で、関数の code は変わりません。 |
| `test_the_digest_is_the_sha256…` | `_feed`・`digest` に触れていません。`CodeReadings({})` は新しい引数の型でも空の dict として通ります。 |
| `tests/test_bound_source_store.py` | B に触れていません。 |
| 実行時の entry の形の確かめ(`_facts_from_rows`) | 行は 7 欄で先頭が str のまま、意味だけが変わります。TAG を /2 に上げたので、旧い形の entry は引きません。 |
| code-quality fast(semgrep) | `.semgrep.yaml` で doeff-hy の path を名指す規則は `doeff_hy/handle.hy`・`macros.hy` だけでした(読んだ)。汎用の規則に当たる構文も足していません。 |
| code-quality fast(doeff-linter) | error 級の規則(DOEFF002・006・007・008・016・021)のソースを読んだ限り、当たる形を足していません。self の属性の代入は `__init__` だけで、ほかは入れ物の中身の書きです。tuple を返す注記・引数の入れ物の書き換え・相対 import もありません。 |
| code-quality fast(依存の向き) | import を足していません。 |
| 起動時の import の軽さ(docstring の決まり) | 起動時に読まれる module に触れていません。 |

## 4. 検査通過と違反の両方を確かめる最小の手順

**(a) 検査の通過**: 差分を当てた worktree で、次を実行します。期待は緑です(推測)。
- `pytest packages/doeff-hy/tests/test_macro_use_record.py packages/doeff-hy/tests/test_bound_source_store.py`
- `code-quality --scope changed`

**(b) 違反**: 同じ設定で、次の検を 1 本足して走らせます。
- 環境: `DOEFF_HY_CODE_STORE=<tmp>/store`・`PYTHONPYCACHEPREFIX=<tmp>/pycache`・`PYTHONDONTWRITEBYTECODE` なし。
- package `facts_probe` の中身:
  - `defs.hy`: ``(defmacro defhelper [name value] `(defn ~name [] ~value))``
  - `macros.hy`: `(require facts_probe.defs [defhelper])` / `(defhelper helper 1)` / `(defmacro body [] (helper))`
  - `user.hy`: `(require facts_probe.macros [body])` / `(setv value (body))`
- 子の process: `sys.path` に根を足し、`import hy, doeff_hy` の後で `importlib.import_module("facts_probe.user").value` を出します。

手順:
1. 子を走らせます。期待 `value == 1`。
2. defs.hy の `[] ~value)` を `[] (* ~value 10))` に替えます。同じ行の中で、大きさは変わります。
3. 子をもう一度走らせます。
   - 期待(基準版 167d38ec): `value == 10`。
   - 期待(差分): `value == 1`。古い展開を今に合うと判じています。

2 回目の process(差分の場合)で起きる事:
1. user の .pyc の照らしで `_macro_now` が macros を import します。
2. macros.hy 自身の記録(defhelper の digest)は古いので、macros.hy は compile し直され、helper の code は 10 を返す物に変わります。
3. 一方、macros.hy の中身は同じなので、`(facts_probe.macros, macros.hy の中身)` の entry の行 `helper\0 2` が当たり、`return 1` の時の shape を返します。
4. body の digest は記録と同じになり、SAME と判じます。

helper の行番号は、Hy の `macroexpand` の `replace_hy_obj` が呼んだ所の位置を写すので、2 で揃います。これは hy/macros.py を読んで確かめました。

**対照**:
- 3 の時だけ `DOEFF_HY_CODE_STORE=off` にすると、差分でも 10 になるはずです。置き場の entry が原因だと切り分けられます。
- pycache を消して 3 を走らせると 10 で、これが展開し直した時の真の値です。
- `CROSS_CHILD` の record/check の形でも、check の答えは基準版が False、差分が True のはずです。

**S4 の形**: 作業木 a と b で macros.hy と user.hy が同じで、defs.hy だけが違うとします。b の記録は a の helper の読みで作られます。その後 b の defs.hy を a と同じに戻しても、b は「今に合う」と判じて value 10 のまま残るはずです。基準版では古いと判じます。

この probe はそのまま、欠けている失敗ケースの検になります。基準版で緑、差分で赤になる見込みです。

## 5. 未確認の前提と、足りない情報

- **実測**: 読む木の file を読んだことだけです。pytest・code-quality・doeff-linter・semgrep・Python は走らせていません。
- **Hy の挙動(推測)**:
  - macro が int を返せば Hy が model にする、と見ています。
  - `require` に下線を含む module 名を書ける、と見ています。
  - `(* 1 10)` で code の形が変わる、と見ています。
- **行の鍵の衝突(未確認)**: 歩みが値として辿る関数で、同じ qualname と最初の行を持つ物が同じ file に 2 つあると(module の高さの lambda を同じ行に 2 つ書くなど)、差分は読みを取り違えます。doeff_hy の file にその形があれば、置き場を使う「import の順の違う process」の検が赤になり得ます。その検は提示の集合の外です。
- **linter の基準版の状態(未確認)**: DOEFF002 のソースを読む限り、基準版の `self.missed = True`(`CodeReadings.facts` の中)は当たりに見えます。一方、`scripts/hook_finding_baseline/doeff-linter.json` に guard の file はありません。`code-quality --scope changed` が何をどう数えるかは確かめていません。差分はこの代入を消すので、当たりを増やさない向きではあります。
- **型検査**: 日次なので考えていません。
- **実害の頻度(調べていない)**: agora などで、展開の時に呼ばれる補助関数が別の file の macro(defk・deff など)で定義されている例が実際にどれだけあるかは調べていません。
- **他に探した候補**(短く):
  - **長く走る道具が `IMPORT_SCOPE` を自分で開く形**(G がこれを公開しているので静的には止まらない): 提示の検は通る見込みです。違反を示すには、module の object が残る長い process で依る file が変わる場面が要ります。今回は具体化していません。
  - **bound_source の鍵を (path, 更新時刻, 大きさ) にする形**: `_replace` が必ず大きさを変えるので、検は通る見込みです。ただし S4 を捨てるので、目立ちます。
  - **`_REMEMBERED` の subject の同一を外す形**: ContextVar の既定値の検で赤になる見込みです。
  - **`record.files` が今に合えば歩みを省く形**: 別の module の値の検(条 2)で赤になる見込みです。
  - **`scope.confirmed` を process 全体に上げる形**: `…between_two_imports…` で赤になる見込みです。

この失敗例が見つかったことは、合格や安全の判定を意味しません。

WAIT: work — 依頼元の次の指示
